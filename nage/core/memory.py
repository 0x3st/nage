import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Tuple

from ..memory import (
    contains_highly_sensitive_data,
    contains_secret,
    is_temporary_statement,
    normalize_memory_content,
    significant_tokens,
)
from ..protocol import (
    EvidenceType,
    MemoryOperation,
    MemoryProposal,
    MemoryScope,
    Sensitivity,
)
from ..storage import MemoryRecord, MemoryStore
from .context import (
    BuiltContext,
    ContextSegment,
    ContextSource,
    RequestEnvelope,
    TrustLevel,
)

_USER_MEMORY_EVIDENCE = re.compile(
    r"(?:\b(?:i|we)\s+(?:(?:always|generally|usually)\s+)?"
    r"(?:prefer|use|want|like|work)|"
    r"\bmy\s+[^.!?]{1,80}\s+(?:is|uses|has)\b|"
    r"\b(?:our|this|the)\s+(?:project|repository|repo)\s+"
    r"(?:uses|is|has)\b|"
    r"\b(?:always\s+)?(?:keep|make)\s+(?:answers|responses)\b|"
    r"\b(?:always|by default)\s+(?:answer|respond|format|use)\b)",
    re.IGNORECASE,
)


class MemoryDisposition(str, Enum):
    ACTIVATED = "activated"
    CANDIDATE = "candidate"
    MERGED = "merged"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    DISABLED = "disabled"


@dataclass(frozen=True)
class MemoryDecision:
    disposition: MemoryDisposition
    message: str
    memory_id: Optional[str] = None


@dataclass(frozen=True)
class MemoryInvocation:
    request_id: str
    user_request: str
    working_directory: Path
    repository_root: Optional[Path]
    supplied_memory_ids: Tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.request_id):
            raise ValueError("memory invocation request ID is invalid")
        if not isinstance(self.user_request, str) or not self.user_request.strip():
            raise ValueError("memory invocation user request is invalid")
        try:
            working_directory = Path(self.working_directory).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("memory invocation workspace is invalid") from exc
        if not working_directory.is_dir():
            raise ValueError("memory invocation workspace is invalid")
        object.__setattr__(self, "working_directory", working_directory)
        if self.repository_root is not None:
            try:
                repository_root = Path(self.repository_root).resolve(strict=True)
                if not repository_root.is_dir():
                    raise ValueError("repository root is not a directory")
                working_directory.relative_to(repository_root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError("memory invocation repository is invalid") from exc
            object.__setattr__(self, "repository_root", repository_root)
        memory_ids = tuple(self.supplied_memory_ids)
        if len(memory_ids) != len(set(memory_ids)) or any(
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", memory_id)
            for memory_id in memory_ids
        ):
            raise ValueError("supplied memory IDs are invalid")
        if not isinstance(self.enabled, bool):
            raise TypeError("memory invocation enabled flag must be boolean")
        object.__setattr__(self, "supplied_memory_ids", memory_ids)

    @classmethod
    def from_built_context(
        cls,
        envelope: RequestEnvelope,
        context: BuiltContext,
        enabled: bool = True,
    ) -> "MemoryInvocation":
        supplied_ids = tuple(
            segment.source_reference
            for segment in context.selected_segments
            if segment.source_type == ContextSource.MEMORY
        )
        return cls(
            request_id=envelope.request_id,
            user_request=envelope.request,
            working_directory=envelope.working_directory,
            repository_root=envelope.repository_root,
            supplied_memory_ids=supplied_ids,
            enabled=enabled and not envelope.options.no_memory,
        )


@dataclass(frozen=True)
class MemorySelection:
    segments: Tuple[ContextSegment, ...]
    memory_ids: Tuple[str, ...]


class MemoryContextService:
    def __init__(
        self,
        store: MemoryStore,
        globally_enabled: bool = True,
        maximum_items: int = 5,
    ) -> None:
        if (
            not isinstance(maximum_items, int)
            or isinstance(maximum_items, bool)
            or maximum_items <= 0
            or maximum_items > 20
        ):
            raise ValueError("memory context item limit is invalid")
        if not isinstance(globally_enabled, bool):
            raise TypeError("global memory flag must be boolean")
        self.store = store
        self.globally_enabled = globally_enabled
        self.maximum_items = maximum_items

    def select(self, envelope: RequestEnvelope) -> MemorySelection:
        if not self.globally_enabled or envelope.options.no_memory:
            return MemorySelection(segments=(), memory_ids=())
        workspace_key = str(envelope.working_directory)
        repository_key = (
            None if envelope.repository_root is None else str(envelope.repository_root)
        )
        records = self.store.retrieve(
            envelope.request,
            workspace_key=workspace_key,
            repository_key=repository_key,
            limit=self.maximum_items,
        )
        segments = tuple(self._segment(record) for record in records)
        return MemorySelection(
            segments=segments,
            memory_ids=tuple(record.id for record in records),
        )

    @staticmethod
    def _segment(record: MemoryRecord) -> ContextSegment:
        content = json.dumps(
            {
                "memory_id": record.id,
                "kind": record.kind,
                "scope": record.scope_type,
                "content": record.content,
                "source_type": record.source_type,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        priority = 85 if record.kind == "instruction" else 75
        if record.scope_type in {"workspace", "repository"}:
            priority += 5
        return ContextSegment(
            source_type=ContextSource.MEMORY,
            source_reference=record.id,
            trust_level=TrustLevel.POLICY_SELECTED,
            priority=min(priority, 100),
            content=content,
            reason_selected=("Active memory selected by scoped lexical retrieval."),
            content_hash=hashlib.sha256(record.content.encode("utf-8")).hexdigest(),
        )


class MemoryProposalPolicy:
    def __init__(self, store: MemoryStore, globally_enabled: bool = True) -> None:
        if not isinstance(globally_enabled, bool):
            raise TypeError("global memory flag must be boolean")
        self.store = store
        self.globally_enabled = globally_enabled

    def process(
        self,
        proposals: Iterable[MemoryProposal],
        invocation: MemoryInvocation,
    ) -> Tuple[MemoryDecision, ...]:
        proposal_list = tuple(proposals)
        if not self.globally_enabled or not invocation.enabled:
            return tuple(
                MemoryDecision(
                    MemoryDisposition.DISABLED,
                    "Memory proposal ignored because memory is disabled.",
                )
                for _ in proposal_list
            )
        decisions = []
        for proposal in proposal_list:
            decisions.append(self._process_one(proposal, invocation))
        return tuple(decisions)

    def _process_one(
        self,
        proposal: MemoryProposal,
        invocation: MemoryInvocation,
    ) -> MemoryDecision:
        content = normalize_memory_content(proposal.content)
        rejection = self._rejection_reason(proposal, content, invocation)
        if rejection is not None:
            return MemoryDecision(MemoryDisposition.REJECTED, rejection)

        scope_key = self._scope_key(proposal.scope, invocation)
        if scope_key is None:
            return MemoryDecision(
                MemoryDisposition.REJECTED,
                "Memory proposal rejected because its scope is unavailable.",
            )
        source_hash = hashlib.sha256(
            invocation.user_request.encode("utf-8")
        ).hexdigest()

        if proposal.operation == MemoryOperation.SUPERSEDE:
            result = self.store.supersede(
                proposal.supersedes,
                content=content,
                kind=proposal.kind.value,
                scope_type=proposal.scope.value,
                scope_key=scope_key,
                source_type=proposal.evidence.value,
                confidence=proposal.confidence,
                source_reference=invocation.request_id,
                source_hash=source_hash,
            )
            return MemoryDecision(
                MemoryDisposition.SUPERSEDED,
                "Updated remembered information: {0}".format(content),
                result.record.id,
            )

        status = self._initial_status(proposal)
        if status is None:
            return MemoryDecision(
                MemoryDisposition.REJECTED,
                "Memory proposal rejected because evidence was too weak or temporary.",
            )
        result = self.store.upsert(
            content=content,
            kind=proposal.kind.value,
            scope_type=proposal.scope.value,
            scope_key=scope_key,
            source_type=proposal.evidence.value,
            confidence=proposal.confidence,
            sensitivity="normal",
            status=status,
            source_reference=invocation.request_id,
            source_hash=source_hash,
        )
        if (
            proposal.evidence == EvidenceType.REPEATED_USER_EVIDENCE
            and result.record.status == "candidate"
            and result.record.evidence_count >= 2
            and proposal.confidence >= 0.85
        ):
            activated = self.store.activate(result.record.id)
            return MemoryDecision(
                MemoryDisposition.ACTIVATED,
                "Remembered after repeated evidence: {0}".format(content),
                activated.id,
            )
        if result.disposition == "merged":
            disposition = MemoryDisposition.MERGED
            message = "Reinforced remembered information: {0}".format(content)
        elif status == "active":
            disposition = MemoryDisposition.ACTIVATED
            message = "Remembered: {0}".format(content)
        else:
            disposition = MemoryDisposition.CANDIDATE
            message = "Saved an inactive memory candidate: {0}".format(content)
        return MemoryDecision(disposition, message, result.record.id)

    def _rejection_reason(
        self,
        proposal: MemoryProposal,
        content: str,
        invocation: MemoryInvocation,
    ) -> Optional[str]:
        if proposal.operation == MemoryOperation.FORGET:
            return (
                "Automatic memory deletion is not allowed; use a confirmed memory tool."
            )
        if proposal.sensitivity != Sensitivity.NORMAL:
            return (
                "Memory proposal rejected because sensitive data is not "
                "stored automatically."
            )
        if contains_secret(content):
            return "Memory proposal rejected because it appears to contain a secret."
        if contains_highly_sensitive_data(content):
            return (
                "Memory proposal rejected because it contains highly "
                "sensitive information."
            )
        if is_temporary_statement(content):
            return "Memory proposal rejected because it appears temporary."
        if proposal.evidence in {
            EvidenceType.UNTRUSTED_CONTEXT,
            EvidenceType.MODEL_INFERENCE,
        }:
            return (
                "Memory proposal rejected because its evidence is not a "
                "current user statement."
            )
        if is_temporary_statement(
            invocation.user_request
        ) or not _USER_MEMORY_EVIDENCE.search(invocation.user_request):
            return (
                "Memory proposal rejected because the current request does not "
                "contain a stable user statement."
            )
        if not self._grounded_in_user_request(content, invocation.user_request):
            return (
                "Memory proposal rejected because it is not grounded in "
                "the current user request."
            )
        if proposal.operation == MemoryOperation.SUPERSEDE:
            supplied = set(invocation.supplied_memory_ids)
            targets = set(proposal.supersedes)
            if not targets or not targets.issubset(supplied):
                return (
                    "Memory supersede rejected because the targets were not "
                    "supplied in context."
                )
            if proposal.evidence != EvidenceType.EXPLICIT_USER_STATEMENT:
                return "Memory supersede requires an explicit current user statement."
            if proposal.confidence < 0.8:
                return "Memory supersede rejected because confidence is too low."
        return None

    @staticmethod
    def _initial_status(proposal: MemoryProposal) -> Optional[str]:
        if proposal.evidence == EvidenceType.EXPLICIT_USER_STATEMENT:
            if proposal.confidence >= 0.8:
                return "active"
            if proposal.confidence >= 0.6:
                return "candidate"
            return None
        if proposal.evidence == EvidenceType.STRONG_IMPLICIT_PREFERENCE:
            if proposal.confidence >= 0.92:
                return "active"
            return "candidate" if proposal.confidence >= 0.7 else None
        if proposal.evidence == EvidenceType.REPEATED_USER_EVIDENCE:
            return "candidate" if proposal.confidence >= 0.7 else None
        return None

    @staticmethod
    def _scope_key(
        scope: MemoryScope,
        invocation: MemoryInvocation,
    ) -> Optional[str]:
        if scope == MemoryScope.GLOBAL:
            return ""
        if scope == MemoryScope.WORKSPACE:
            return str(invocation.working_directory)
        if scope == MemoryScope.REPOSITORY:
            return (
                None
                if invocation.repository_root is None
                else str(invocation.repository_root)
            )
        return None

    @staticmethod
    def _grounded_in_user_request(content: str, user_request: str) -> bool:
        content_tokens = significant_tokens(content)
        request_tokens = significant_tokens(user_request)
        if content_tokens.intersection(request_tokens):
            return True
        normalized_request = normalize_memory_content(user_request).casefold()
        return any(
            token in normalized_request for token in content_tokens if len(token) >= 4
        )
