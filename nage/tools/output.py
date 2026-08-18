import json
from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple, TypeVar

from .base import ToolContext

T = TypeVar("T")


@dataclass(frozen=True)
class BoundedText:
    text: str
    truncated: bool
    artifact_ids: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


def _clip_utf8(text: str, maximum_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return text
    marker = "\n[... output truncated ...]"
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= maximum_bytes:
        return encoded[:maximum_bytes].decode("utf-8", errors="ignore")
    available = maximum_bytes - len(marker_bytes)
    return encoded[:available].decode("utf-8", errors="ignore") + marker


def bound_text(
    context: ToolContext,
    text: str,
    inline_bytes: int,
    media_type: str = "text/plain; charset=utf-8",
    sensitivity: str = "normal",
    artifact_ttl_seconds: int = 86400,
    complete: bool = True,
) -> BoundedText:
    if not isinstance(text, str):
        raise TypeError("bounded output must be text")
    if not isinstance(inline_bytes, int) or isinstance(inline_bytes, bool):
        raise ValueError("inline output limit must be an integer")
    if inline_bytes <= 0:
        raise ValueError("inline output limit must be positive")
    if len(text.encode("utf-8")) <= inline_bytes:
        return BoundedText(text=text, truncated=False)

    artifact_ids = ()
    warnings = []
    if context.artifact_writer is not None:
        try:
            artifact = context.artifact_writer.create_text(
                text,
                media_type=media_type,
                sensitivity=sensitivity,
                ttl_seconds=artifact_ttl_seconds,
            )
            artifact_ids = (artifact.artifact_id,)
            warnings.append(
                (
                    "Complete output was stored as a temporary artifact."
                    if complete
                    else "Captured partial output was stored as a temporary artifact."
                )
            )
        except Exception:
            warnings.append("Output could not be stored as a temporary artifact.")
    else:
        warnings.append(
            (
                (
                    "Complete output was not retained because no artifact "
                    "store is available."
                )
                if complete
                else (
                    "Captured partial output was not retained because no "
                    "artifact store is available."
                )
            )
        )
    return BoundedText(
        text=_clip_utf8(text, inline_bytes),
        truncated=True,
        artifact_ids=artifact_ids,
        warnings=tuple(warnings),
    )


def bound_json_items(
    values: Sequence[T],
    item_limit: int,
    inline_bytes: int,
) -> Tuple[List[T], bool]:
    if item_limit <= 0 or inline_bytes <= 2:
        raise ValueError("JSON output limits must be positive")
    selected: List[T] = []
    used_bytes = 2
    for value in values[:item_limit]:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        additional = len(encoded) + (1 if selected else 0)
        if used_bytes + additional > inline_bytes:
            break
        selected.append(value)
        used_bytes += additional
    return selected, len(selected) < len(values)


def store_json_artifact(
    context: ToolContext,
    value: Any,
    sensitivity: str = "normal",
    artifact_ttl_seconds: int = 86400,
    complete: bool = True,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    if context.artifact_writer is None:
        return (), (
            "Additional results were not retained because no artifact store "
            "is available.",
        )
    try:
        artifact = context.artifact_writer.create_text(
            serialized,
            media_type="application/json; charset=utf-8",
            sensitivity=sensitivity,
            ttl_seconds=artifact_ttl_seconds,
        )
    except Exception:
        return (), ("Additional results could not be stored as an artifact.",)
    return (artifact.artifact_id,), (
        (
            "Complete results were stored as a temporary artifact."
            if complete
            else "Captured partial results were stored as a temporary artifact."
        ),
    )
