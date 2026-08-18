import hashlib
import re
from dataclasses import dataclass
from typing import BinaryIO, List, Optional, Set, Tuple

from ..errors import ContextError
from ..storage.artifacts import ArtifactStore
from .context import StdinContext

_ERROR_PATTERN = re.compile(
    r"(?:\berror\b|\bexception\b|\bfailed\b|\bfatal\b|traceback|panic)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StdinLimits:
    inline_bytes: int = 32768
    maximum_bytes: int = 1048576
    excerpt_bytes: int = 16384
    maximum_excerpt_lines: int = 80
    tail_lines: int = 40
    artifact_ttl_seconds: int = 86400

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in (
                self.inline_bytes,
                self.maximum_bytes,
                self.excerpt_bytes,
                self.maximum_excerpt_lines,
                self.tail_lines,
                self.artifact_ttl_seconds,
            )
        ):
            raise ValueError("stdin limits must be positive")
        if self.inline_bytes > self.maximum_bytes:
            raise ValueError("stdin inline limit cannot exceed maximum bytes")
        if self.tail_lines > self.maximum_excerpt_lines:
            raise ValueError("stdin tail lines cannot exceed excerpt line limit")


class StdinReader:
    def __init__(
        self,
        artifact_store: Optional[ArtifactStore] = None,
        limits: Optional[StdinLimits] = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.limits = limits or StdinLimits()

    def read(self, stream: BinaryIO) -> StdinContext:
        data, complete = self._read_bounded(stream)
        digest = self._sha256(data)
        binary = self._is_binary(data)
        artifact_id = None

        should_store = binary or len(data) > self.limits.inline_bytes or not complete
        if should_store and self.artifact_store is not None:
            record = self.artifact_store.create_bytes(
                data,
                media_type=(
                    "application/octet-stream"
                    if binary
                    else "text/plain; charset=utf-8"
                ),
                sensitivity="sensitive",
                ttl_seconds=self.limits.artifact_ttl_seconds,
            )
            artifact_id = record.artifact_id
            digest = record.sha256

        if binary:
            return StdinContext(
                present=True,
                media_type="application/octet-stream",
                bytes_captured=len(data),
                complete=complete,
                binary=True,
                excerpt="",
                truncated=not complete,
                artifact_id=artifact_id,
                sha256=digest,
            )

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            # _is_binary performs the same check, but retain a fail-closed path
            # if its implementation changes later.
            raise ContextError("Piped input is not valid UTF-8 text.") from exc

        if len(data) <= self.limits.inline_bytes and complete:
            excerpt = text
            truncated = False
        else:
            excerpt = self._select_excerpt(text)
            truncated = not complete or excerpt != text
        return StdinContext(
            present=True,
            media_type="text/plain; charset=utf-8",
            bytes_captured=len(data),
            complete=complete,
            binary=False,
            excerpt=excerpt,
            truncated=truncated,
            artifact_id=artifact_id,
            sha256=digest,
        )

    def _read_bounded(self, stream: BinaryIO) -> Tuple[bytes, bool]:
        captured = bytearray()
        ceiling = self.limits.maximum_bytes
        while len(captured) <= ceiling:
            remaining = ceiling + 1 - len(captured)
            chunk = stream.read(min(65536, remaining))
            if chunk is None:
                raise ContextError("Piped input stream did not return data.")
            if not isinstance(chunk, bytes):
                raise ContextError("Piped input stream must provide bytes.")
            if not chunk:
                return bytes(captured), True
            captured.extend(chunk[:remaining])
        return bytes(captured[:ceiling]), False

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _is_binary(data: bytes) -> bool:
        if b"\x00" in data:
            return True
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False

    def _select_excerpt(self, text: str) -> str:
        lines = text.splitlines()
        if not lines:
            return ""
        selected: Set[int] = set()
        context_budget = self.limits.maximum_excerpt_lines - self.limits.tail_lines
        error_indexes = [
            index for index, line in enumerate(lines) if _ERROR_PATTERN.search(line)
        ]
        error_context: Set[int] = set()
        for index in error_indexes:
            error_context.update(range(max(0, index - 2), min(len(lines), index + 3)))
            if len(error_context) >= context_budget:
                break
        if error_context:
            selected.update(sorted(error_context)[:context_budget])
        else:
            selected.update(range(min(context_budget, 10, len(lines))))
        selected.update(range(max(0, len(lines) - self.limits.tail_lines), len(lines)))
        ordered = sorted(selected)
        rendered: List[str] = []
        previous = None
        for index in ordered:
            if previous is not None and index > previous + 1:
                rendered.append("[... omitted lines ...]")
            rendered.append("{0}: {1}".format(index + 1, lines[index]))
            previous = index
        excerpt = "\n".join(rendered)
        encoded = excerpt.encode("utf-8")
        if len(encoded) > self.limits.excerpt_bytes:
            marker = "\n[... excerpt byte limit reached ...]"
            marker_bytes = marker.encode("utf-8")
            if len(marker_bytes) >= self.limits.excerpt_bytes:
                return encoded[: self.limits.excerpt_bytes].decode(
                    "utf-8", errors="ignore"
                )
            available = self.limits.excerpt_bytes - len(marker_bytes)
            excerpt = encoded[:available].decode("utf-8", errors="ignore") + marker
        return excerpt
