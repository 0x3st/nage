import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import ContextError, PolicyDeniedError, ToolError
from ..prompts import CORE_SYSTEM_PROMPT
from ..providers import MessageRole, ProviderMessage, ProviderToolDefinition
from ..retrieval import FileRetriever, detect_repository_root


class TrustLevel(str, Enum):
    CURRENT_USER_INTENT = "current_user_intent"
    HOST_METADATA = "host_metadata"
    POLICY_SELECTED = "policy_selected"
    UNTRUSTED_DATA = "untrusted_data"
    PRIOR_MODEL_OUTPUT = "prior_model_output"


class ContextSource(str, Enum):
    STDIN = "stdin"
    FILE = "file"
    PROJECT_MANIFEST = "project_manifest"
    WORKSPACE = "workspace"
    MEMORY = "memory"
    CONTINUATION = "continuation"
    TOOL_RESULT = "tool_result"
    PRIOR_MODEL = "prior_model"


_CONTEXT_SOURCE_TRUST = {
    ContextSource.STDIN: TrustLevel.UNTRUSTED_DATA,
    ContextSource.FILE: TrustLevel.UNTRUSTED_DATA,
    ContextSource.PROJECT_MANIFEST: TrustLevel.UNTRUSTED_DATA,
    ContextSource.WORKSPACE: TrustLevel.HOST_METADATA,
    ContextSource.MEMORY: TrustLevel.POLICY_SELECTED,
    ContextSource.CONTINUATION: TrustLevel.POLICY_SELECTED,
    ContextSource.TOOL_RESULT: TrustLevel.UNTRUSTED_DATA,
    ContextSource.PRIOR_MODEL: TrustLevel.PRIOR_MODEL_OUTPUT,
}


@dataclass(frozen=True)
class RequestOptions:
    dry_run: bool = False
    fresh: bool = False
    no_memory: bool = False
    show_context: bool = False

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bool)
            for value in (
                self.dry_run,
                self.fresh,
                self.no_memory,
                self.show_context,
            )
        ):
            raise TypeError("request options must be booleans")


@dataclass(frozen=True)
class StdinContext:
    present: bool
    media_type: str
    bytes_captured: int
    complete: bool
    binary: bool
    excerpt: str = ""
    truncated: bool = False
    artifact_id: Optional[str] = None
    sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bool)
            for value in (
                self.present,
                self.complete,
                self.binary,
                self.truncated,
            )
        ):
            raise TypeError("stdin state flags must be booleans")
        if (
            not isinstance(self.bytes_captured, int)
            or isinstance(self.bytes_captured, bool)
            or self.bytes_captured < 0
        ):
            raise ValueError("bytes_captured cannot be negative")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError("stdin media type must not be empty")
        if not isinstance(self.excerpt, str):
            raise TypeError("stdin excerpt must be text")
        if self.binary and self.excerpt:
            raise ValueError("binary stdin cannot contain a text excerpt")
        if self.artifact_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", self.artifact_id
        ):
            raise ValueError("stdin artifact ID has an invalid format")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("stdin content hash is invalid")


@dataclass(frozen=True)
class RequestEnvelope:
    request_id: str
    request: str
    working_directory: Path
    repository_root: Optional[Path]
    started_at: datetime
    terminal_identity: Optional[str] = None
    stdin: Optional[StdinContext] = None
    options: RequestOptions = field(default_factory=RequestOptions)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.request_id):
            raise ContextError("Request ID has an invalid format.")
        if not isinstance(self.request, str) or not self.request.strip():
            raise ContextError("Request must be a non-empty string.")
        if not isinstance(self.working_directory, Path):
            raise ContextError("Working directory must be a path.")
        try:
            working_directory = self.working_directory.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ContextError(
                "Working directory must be an existing absolute path."
            ) from exc
        if not working_directory.is_dir():
            raise ContextError("Working directory must be an existing directory.")
        object.__setattr__(self, "working_directory", working_directory)
        if self.repository_root is not None:
            if not isinstance(self.repository_root, Path):
                raise ContextError("Repository root must be a path.")
            try:
                repository_root = self.repository_root.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ContextError(
                    "Repository root must be an existing absolute path."
                ) from exc
            if not repository_root.is_dir():
                raise ContextError("Repository root must be an existing directory.")
            object.__setattr__(self, "repository_root", repository_root)
            try:
                working_directory.relative_to(repository_root)
            except ValueError as exc:
                raise ContextError(
                    "Working directory is outside the repository root."
                ) from exc
        if self.started_at.tzinfo is None:
            raise ContextError("Request start time must include a timezone.")
        if self.terminal_identity is not None and (
            not isinstance(self.terminal_identity, str)
            or len(self.terminal_identity) > 256
        ):
            raise ContextError("Terminal identity is invalid.")
        if self.stdin is not None and not isinstance(self.stdin, StdinContext):
            raise ContextError("Stdin context is invalid.")
        if not isinstance(self.options, RequestOptions):
            raise ContextError("Request options are invalid.")

    @classmethod
    def create(
        cls,
        request: str,
        working_directory: Path,
        terminal_identity: Optional[str] = None,
        stdin: Optional[StdinContext] = None,
        options: Optional[RequestOptions] = None,
        request_id: Optional[str] = None,
        started_at: Optional[datetime] = None,
    ) -> "RequestEnvelope":
        if not isinstance(request, str) or not request.strip():
            raise ContextError("Request must be a non-empty string.")
        try:
            working_directory = Path(working_directory).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ContextError("Working directory does not exist.") from exc
        if not working_directory.is_dir():
            raise ContextError("Working directory is not a directory.")
        if terminal_identity is not None:
            terminal_identity = " ".join(terminal_identity.split())
            if not terminal_identity:
                terminal_identity = None
            elif len(terminal_identity) > 256:
                terminal_identity = terminal_identity[:256]
        timestamp = started_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return cls(
            request_id=request_id or uuid.uuid4().hex,
            request=request,
            working_directory=working_directory,
            repository_root=detect_repository_root(working_directory),
            started_at=timestamp.astimezone(timezone.utc),
            terminal_identity=terminal_identity,
            stdin=stdin,
            options=options or RequestOptions(),
        )


@dataclass(frozen=True)
class ContextSegment:
    source_type: ContextSource
    source_reference: str
    trust_level: TrustLevel
    priority: int
    content: str
    reason_selected: str
    truncated: bool = False
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, ContextSource):
            raise TypeError("context source type must be a ContextSource")
        if not isinstance(self.trust_level, TrustLevel):
            raise TypeError("context trust level must be a TrustLevel")
        expected_trust = _CONTEXT_SOURCE_TRUST[self.source_type]
        if self.trust_level != expected_trust:
            raise ValueError(
                "context trust level does not match its host-owned source type"
            )
        if not 0 <= self.priority <= 100:
            raise ValueError("context priority must be between 0 and 100")
        if (
            not isinstance(self.source_reference, str)
            or len(self.source_reference) > 2048
            or "\x00" in self.source_reference
        ):
            raise ValueError("context source reference is invalid")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("context segment content must not be empty")
        if (
            not isinstance(self.reason_selected, str)
            or not self.reason_selected.strip()
            or len(self.reason_selected) > 512
        ):
            raise ValueError("context selection reason is invalid")
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
            )
        elif len(self.content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_hash
        ):
            raise ValueError("context content hash must be lowercase SHA-256")


@dataclass(frozen=True)
class ContextManifestEntry:
    source_type: ContextSource
    source_reference: str
    trust_level: TrustLevel
    priority: int
    estimated_tokens: int
    truncated: bool
    content_hash: str
    reason_selected: str
    included: bool
    omission_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, ContextSource) or not isinstance(
            self.trust_level, TrustLevel
        ):
            raise TypeError("context manifest source and trust must be enums")
        if self.trust_level != _CONTEXT_SOURCE_TRUST[self.source_type]:
            raise ValueError("context manifest trust does not match its source")
        if (
            not isinstance(self.priority, int)
            or isinstance(self.priority, bool)
            or not 0 <= self.priority <= 100
        ):
            raise ValueError("context manifest priority is invalid")
        if (
            not isinstance(self.estimated_tokens, int)
            or isinstance(self.estimated_tokens, bool)
            or self.estimated_tokens < 0
        ):
            raise ValueError("context manifest token estimate is invalid")
        if not isinstance(self.truncated, bool) or not isinstance(self.included, bool):
            raise TypeError("context manifest flags must be booleans")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise ValueError("context manifest content hash is invalid")
        if (
            not isinstance(self.source_reference, str)
            or len(self.source_reference) > 2048
            or "\x00" in self.source_reference
        ):
            raise ValueError("context manifest source reference is invalid")
        if (
            not isinstance(self.reason_selected, str)
            or not self.reason_selected.strip()
            or len(self.reason_selected) > 512
        ):
            raise ValueError("context manifest selection reason is invalid")
        if self.omission_reason is not None and (
            not isinstance(self.omission_reason, str) or len(self.omission_reason) > 512
        ):
            raise ValueError("context manifest omission reason is invalid")


@dataclass(frozen=True)
class ContextManifest:
    request_id: str
    budget_tokens: int
    estimated_prompt_tokens: int
    reserved_output_tokens: int
    reserved_tool_tokens: int
    entries: Tuple[ContextManifestEntry, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.request_id):
            raise ValueError("context manifest request ID is invalid")
        token_values = (
            self.budget_tokens,
            self.estimated_prompt_tokens,
            self.reserved_output_tokens,
            self.reserved_tool_tokens,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in token_values
        ):
            raise ValueError("context manifest token values are invalid")
        if self.estimated_prompt_tokens > self.budget_tokens:
            raise ValueError("context manifest exceeds its prompt budget")
        entries = tuple(self.entries)
        if any(not isinstance(entry, ContextManifestEntry) for entry in entries):
            raise TypeError("context manifest entries are invalid")
        object.__setattr__(self, "entries", entries)

    def to_mapping(self) -> Dict[str, object]:
        return {
            "request_id": self.request_id,
            "budget_tokens": self.budget_tokens,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "reserved_tool_tokens": self.reserved_tool_tokens,
            "entries": [
                {
                    "source_type": entry.source_type.value,
                    "source_reference": entry.source_reference,
                    "trust_level": entry.trust_level.value,
                    "priority": entry.priority,
                    "estimated_tokens": entry.estimated_tokens,
                    "truncated": entry.truncated,
                    "content_hash": entry.content_hash,
                    "reason_selected": entry.reason_selected,
                    "included": entry.included,
                    "omission_reason": entry.omission_reason,
                }
                for entry in self.entries
            ],
        }


@dataclass(frozen=True)
class ContextLimits:
    context_window_tokens: int = 12000
    max_prompt_tokens: int = 8000
    reserved_output_tokens: int = 2000
    reserved_tool_tokens: int = 1000
    memory_tokens: int = 300
    continuation_tokens: int = 300
    file_tokens: int = 4000
    stdin_tokens: int = 2000
    max_segments: int = 64

    def __post_init__(self) -> None:
        values = (
            self.context_window_tokens,
            self.max_prompt_tokens,
            self.reserved_output_tokens,
            self.reserved_tool_tokens,
            self.memory_tokens,
            self.continuation_tokens,
            self.file_tokens,
            self.stdin_tokens,
            self.max_segments,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise ValueError("context limits must be non-negative integers")
        if (
            self.context_window_tokens <= 0
            or self.max_prompt_tokens <= 0
            or self.max_segments <= 0
        ):
            raise ValueError("context token and segment limits must be positive")


class TokenEstimator:
    """Conservative tokenizer fallback for providers without a tokenizer."""

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(math.ceil(len(text.encode("utf-8")) / 3.0)))

    def truncate(self, text: str, max_tokens: int) -> Tuple[str, bool]:
        if max_tokens <= 0:
            return "", bool(text)
        if self.estimate(text) <= max_tokens:
            return text, False
        marker = "\n[... context truncated ...]"
        if self.estimate(marker) >= max_tokens:
            marker = "[...]"
        low = 0
        high = len(text)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle].rstrip() + marker
            if self.estimate(candidate) <= max_tokens:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        if best:
            return best, True
        # Very small synthetic budgets are useful in tests. Preserve the bound
        # even when a visible truncation marker cannot fit.
        low = 0
        high = len(text)
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle]
            if self.estimate(candidate) <= max_tokens:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best, True


def estimate_tool_schema_tokens(
    tools: Sequence[ProviderToolDefinition],
    estimator: Optional[TokenEstimator] = None,
) -> int:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        }
        for tool in tools
    ]
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (estimator or TokenEstimator()).estimate(serialized)


@dataclass(frozen=True)
class BuiltContext:
    messages: Tuple[ProviderMessage, ...]
    selected_segments: Tuple[ContextSegment, ...]
    manifest: ContextManifest


class ContextBuilder:
    def __init__(
        self,
        limits: Optional[ContextLimits] = None,
        estimator: Optional[TokenEstimator] = None,
        system_prompt: str = CORE_SYSTEM_PROMPT,
    ) -> None:
        self.limits = limits or ContextLimits()
        self.estimator = estimator or TokenEstimator()
        self.system_prompt = system_prompt

    def build(
        self,
        envelope: RequestEnvelope,
        candidates: Iterable[ContextSegment] = (),
        tool_schema_tokens: int = 0,
    ) -> BuiltContext:
        if (
            not isinstance(tool_schema_tokens, int)
            or isinstance(tool_schema_tokens, bool)
            or tool_schema_tokens < 0
        ):
            raise ContextError(
                "Tool schema token estimate must be a non-negative integer."
            )
        reserved_tools = max(self.limits.reserved_tool_tokens, tool_schema_tokens)
        budget = min(
            self.limits.max_prompt_tokens,
            self.limits.context_window_tokens
            - self.limits.reserved_output_tokens
            - reserved_tools,
        )
        if budget <= 0:
            raise ContextError("No prompt budget remains after reserved tokens.")

        candidate_list = list(islice(candidates, self.limits.max_segments + 1))
        if any(not isinstance(item, ContextSegment) for item in candidate_list):
            raise ContextError("Context candidates must be validated segments.")
        if envelope.stdin is not None and envelope.stdin.excerpt:
            candidate_list.append(
                ContextSegment(
                    source_type=ContextSource.STDIN,
                    source_reference=envelope.stdin.artifact_id or "stdin:inline",
                    trust_level=TrustLevel.UNTRUSTED_DATA,
                    priority=90,
                    content=envelope.stdin.excerpt,
                    reason_selected="Piped input supplied with the current request.",
                    truncated=envelope.stdin.truncated,
                    content_hash=envelope.stdin.sha256 or "",
                )
            )
        if len(candidate_list) > self.limits.max_segments:
            raise ContextError("Too many context segments were supplied.")

        filtered = []
        pre_entries: List[ContextManifestEntry] = []
        for candidate in candidate_list:
            omitted_reason = None
            if (
                envelope.options.no_memory
                and candidate.source_type == ContextSource.MEMORY
            ):
                omitted_reason = "Memory disabled for this invocation."
            elif (
                envelope.options.fresh
                and candidate.source_type == ContextSource.CONTINUATION
            ):
                omitted_reason = "Continuation disabled by fresh context."
            if omitted_reason is None:
                filtered.append(candidate)
            else:
                pre_entries.append(
                    self._manifest_entry(
                        candidate,
                        estimated_tokens=0,
                        included=False,
                        omission_reason=omitted_reason,
                    )
                )

        indexed = list(enumerate(filtered))
        indexed.sort(key=lambda item: (-item[1].priority, item[0]))
        selected: List[ContextSegment] = []
        manifest_entries = pre_entries
        category_usage: Dict[str, int] = {}

        base_payload = self._payload(envelope, selected)
        base_tokens = self._estimate_payload(base_payload)
        if base_tokens > budget:
            raise ContextError(
                "The user request and required metadata exceed the prompt budget."
            )

        for _, candidate in indexed:
            category, category_limit = self._category_limit(candidate.source_type)
            used = category_usage.get(category, 0)
            remaining_category = max(0, category_limit - used)
            current_tokens = self._estimate_payload(self._payload(envelope, selected))
            remaining_global = max(0, budget - current_tokens - 24)
            allowed = min(remaining_category, remaining_global)
            if allowed <= 0:
                manifest_entries.append(
                    self._manifest_entry(
                        candidate,
                        estimated_tokens=0,
                        included=False,
                        omission_reason="Context budget exhausted.",
                    )
                )
                continue

            content, clipped = self.estimator.truncate(candidate.content, allowed)
            if not content:
                manifest_entries.append(
                    self._manifest_entry(
                        candidate,
                        estimated_tokens=0,
                        included=False,
                        omission_reason="Context budget too small for this segment.",
                    )
                )
                continue
            selected_candidate = replace(
                candidate,
                content=content,
                truncated=candidate.truncated or clipped,
                content_hash=candidate.content_hash,
            )
            selected.append(selected_candidate)

            # JSON labels and escaping have overhead beyond raw content. Reduce
            # the last segment until the complete serialized prompt fits.
            while self._estimate_payload(self._payload(envelope, selected)) > budget:
                overflow = (
                    self._estimate_payload(self._payload(envelope, selected)) - budget
                )
                reduced_budget = max(
                    0,
                    self.estimator.estimate(selected_candidate.content) - overflow - 2,
                )
                reduced, _ = self.estimator.truncate(
                    selected_candidate.content, reduced_budget
                )
                if not reduced:
                    selected.pop()
                    selected_candidate = None
                    break
                selected_candidate = replace(
                    selected_candidate, content=reduced, truncated=True
                )
                selected[-1] = selected_candidate

            if selected_candidate is None:
                manifest_entries.append(
                    self._manifest_entry(
                        candidate,
                        estimated_tokens=0,
                        included=False,
                        omission_reason="Serialized context exceeded the budget.",
                    )
                )
                continue
            estimated = self.estimator.estimate(selected_candidate.content)
            category_usage[category] = used + estimated
            manifest_entries.append(
                self._manifest_entry(
                    selected_candidate,
                    estimated_tokens=estimated,
                    included=True,
                )
            )

        payload = self._payload(envelope, selected)
        user_content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        estimated_prompt = self._estimate_payload(payload)
        if estimated_prompt > budget:
            raise ContextError("Assembled context exceeds its prompt budget.")
        manifest = ContextManifest(
            request_id=envelope.request_id,
            budget_tokens=budget,
            estimated_prompt_tokens=estimated_prompt,
            reserved_output_tokens=self.limits.reserved_output_tokens,
            reserved_tool_tokens=reserved_tools,
            entries=tuple(manifest_entries),
        )
        return BuiltContext(
            messages=(
                ProviderMessage(MessageRole.SYSTEM, self.system_prompt),
                ProviderMessage(MessageRole.USER, user_content),
            ),
            selected_segments=tuple(selected),
            manifest=manifest,
        )

    def _estimate_payload(self, payload: Dict[str, object]) -> int:
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        return (
            self.estimator.estimate(self.system_prompt)
            + self.estimator.estimate(content)
            + 16
        )

    @staticmethod
    def _payload(
        envelope: RequestEnvelope, selected: Sequence[ContextSegment]
    ) -> Dict[str, object]:
        stdin_metadata = None
        if envelope.stdin is not None:
            stdin_metadata = {
                "present": envelope.stdin.present,
                "media_type": envelope.stdin.media_type,
                "bytes_captured": envelope.stdin.bytes_captured,
                "complete": envelope.stdin.complete,
                "binary": envelope.stdin.binary,
                "truncated": envelope.stdin.truncated,
                "artifact_id": envelope.stdin.artifact_id,
            }
        return {
            "USER_REQUEST": {
                "trust": TrustLevel.CURRENT_USER_INTENT.value,
                "content": envelope.request,
            },
            "HOST_METADATA": {
                "trust": TrustLevel.HOST_METADATA.value,
                "request_id": envelope.request_id,
                "working_directory": str(envelope.working_directory),
                "repository_root": None
                if envelope.repository_root is None
                else str(envelope.repository_root),
                "started_at": envelope.started_at.isoformat(),
                "terminal_identity": envelope.terminal_identity,
                "options": {
                    "dry_run": envelope.options.dry_run,
                    "fresh": envelope.options.fresh,
                    "no_memory": envelope.options.no_memory,
                },
                "stdin": stdin_metadata,
            },
            "CONTEXT_SEGMENTS": [
                {
                    "source_type": segment.source_type.value,
                    "source_reference": segment.source_reference,
                    "trust": segment.trust_level.value,
                    "content": segment.content,
                }
                for segment in selected
            ],
        }

    def _category_limit(self, source: ContextSource) -> Tuple[str, int]:
        if source == ContextSource.MEMORY:
            return "memory", self.limits.memory_tokens
        if source == ContextSource.CONTINUATION:
            return "continuation", self.limits.continuation_tokens
        if source == ContextSource.STDIN:
            return "stdin", self.limits.stdin_tokens
        if source in {ContextSource.FILE, ContextSource.PROJECT_MANIFEST}:
            return "file", self.limits.file_tokens
        return "other", self.limits.max_prompt_tokens

    @staticmethod
    def _manifest_entry(
        candidate: ContextSegment,
        estimated_tokens: int,
        included: bool,
        omission_reason: Optional[str] = None,
    ) -> ContextManifestEntry:
        return ContextManifestEntry(
            source_type=candidate.source_type,
            source_reference=candidate.source_reference,
            trust_level=candidate.trust_level,
            priority=candidate.priority,
            estimated_tokens=estimated_tokens,
            truncated=candidate.truncated,
            content_hash=candidate.content_hash,
            reason_selected=candidate.reason_selected,
            included=included,
            omission_reason=omission_reason,
        )


def render_context_manifest(manifest: ContextManifest) -> str:
    lines = [
        "Context: {0}/{1} estimated prompt tokens; {2} segment(s).".format(
            manifest.estimated_prompt_tokens,
            manifest.budget_tokens,
            len(manifest.entries),
        )
    ]
    for entry in manifest.entries:
        disposition = "included" if entry.included else "omitted"
        suffix = " truncated" if entry.truncated else ""
        reference = json.dumps(entry.source_reference, ensure_ascii=True)
        reason = entry.reason_selected
        if entry.omission_reason:
            reason = "{0} {1}".format(reason, entry.omission_reason)
        lines.append(
            "- {0} {1} trust={2} {3} tokens={4}{5}: {6}".format(
                disposition,
                entry.source_type.value,
                entry.trust_level.value,
                reference,
                entry.estimated_tokens,
                suffix,
                json.dumps(reason, ensure_ascii=True),
            )
        )
    return "\n".join(lines)


class ProjectContextCollector:
    def __init__(self, max_explicit_files: int = 5, max_lines_per_file: int = 120):
        if max_explicit_files <= 0 or max_lines_per_file <= 0:
            raise ValueError("project context limits must be positive")
        self.max_explicit_files = max_explicit_files
        self.max_lines_per_file = max_lines_per_file

    def collect(
        self,
        envelope: RequestEnvelope,
        explicit_paths: Iterable[str] = (),
        include_manifests: bool = False,
    ) -> Tuple[ContextSegment, ...]:
        root = envelope.repository_root or envelope.working_directory
        retriever = FileRetriever(root)
        segments: List[ContextSegment] = []
        seen = set()

        for requested in islice(explicit_paths, self.max_explicit_files):
            path = Path(requested)
            if not path.is_absolute() and envelope.repository_root is not None:
                try:
                    prefix = envelope.working_directory.relative_to(root)
                    path = prefix / path
                except ValueError:
                    pass
            try:
                excerpt = retriever.read_range(path, 1, self.max_lines_per_file)
            except (PolicyDeniedError, ToolError):
                continue
            if excerpt.relative_path in seen or not excerpt.content:
                continue
            seen.add(excerpt.relative_path)
            segments.append(
                ContextSegment(
                    source_type=ContextSource.FILE,
                    source_reference=excerpt.relative_path,
                    trust_level=TrustLevel.UNTRUSTED_DATA,
                    priority=100,
                    content=excerpt.content,
                    reason_selected="Path explicitly referenced by the current user.",
                    truncated=excerpt.truncated,
                    content_hash=excerpt.sha256,
                )
            )

        if include_manifests:
            for excerpt in retriever.manifest_excerpts(
                max_lines=self.max_lines_per_file
            ):
                if excerpt.relative_path in seen or not excerpt.content:
                    continue
                seen.add(excerpt.relative_path)
                segments.append(
                    ContextSegment(
                        source_type=ContextSource.PROJECT_MANIFEST,
                        source_reference=excerpt.relative_path,
                        trust_level=TrustLevel.UNTRUSTED_DATA,
                        priority=70,
                        content=excerpt.content,
                        reason_selected=(
                            "Project manifest selected for workspace context."
                        ),
                        truncated=excerpt.truncated,
                        content_hash=excerpt.sha256,
                    )
                )
        return tuple(segments)
