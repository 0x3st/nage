import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple

from ..errors import StorageError
from .database import Database

_SOURCE_TRUST = {
    "continuation": "policy_selected",
    "file": "untrusted_data",
    "memory": "policy_selected",
    "prior_model": "prior_model_output",
    "project_manifest": "untrusted_data",
    "stdin": "untrusted_data",
    "tool_result": "untrusted_data",
    "workspace": "host_metadata",
}


@dataclass(frozen=True)
class StoredContextEntry:
    request_id: str
    position: int
    source_type: str
    source_reference: str
    trust_level: str
    priority: int
    estimated_tokens: int
    truncated: bool
    content_hash: str
    reason_selected: str
    included: bool
    omission_reason: Optional[str]


@dataclass(frozen=True)
class StoredContextManifest:
    request_id: str
    budget_tokens: int
    estimated_prompt_tokens: int
    reserved_output_tokens: int
    reserved_tool_tokens: int
    created_at: str
    entries: Tuple[StoredContextEntry, ...]


class ContextManifestStore:
    """Persist context diagnostics without storing segment bodies."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def save(
        self,
        request_id: str,
        budget_tokens: int,
        estimated_prompt_tokens: int,
        reserved_output_tokens: int,
        reserved_tool_tokens: int,
        entries: Sequence[Mapping[str, Any]],
    ) -> StoredContextManifest:
        token_values = (
            budget_tokens,
            estimated_prompt_tokens,
            reserved_output_tokens,
            reserved_tool_tokens,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in token_values
        ):
            raise StorageError("Context manifest token values must be non-negative.")
        created_at = self._now()
        normalized_entries = [
            self._normalize_entry(request_id, position, entry)
            for position, entry in enumerate(entries)
        ]
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO context_manifests(
                        request_id, budget_tokens, estimated_prompt_tokens,
                        reserved_output_tokens, reserved_tool_tokens, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        budget_tokens,
                        estimated_prompt_tokens,
                        reserved_output_tokens,
                        reserved_tool_tokens,
                        created_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO context_manifest_entries(
                        request_id, position, source_type, source_reference,
                        trust_level, priority, estimated_tokens, truncated,
                        content_hash, reason_selected, included, omission_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            entry.request_id,
                            entry.position,
                            entry.source_type,
                            entry.source_reference,
                            entry.trust_level,
                            entry.priority,
                            entry.estimated_tokens,
                            int(entry.truncated),
                            entry.content_hash,
                            entry.reason_selected,
                            int(entry.included),
                            entry.omission_reason,
                        )
                        for entry in normalized_entries
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                "Context manifest request is missing or already stored."
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError("Unable to store context manifest.") from exc
        stored = self.get(request_id)
        if stored is None:
            raise StorageError("Context manifest was not persisted.")
        return stored

    def get(self, request_id: str) -> Optional[StoredContextManifest]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM context_manifests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            entry_rows = connection.execute(
                """
                SELECT * FROM context_manifest_entries
                WHERE request_id = ? ORDER BY position
                """,
                (request_id,),
            ).fetchall()
        return StoredContextManifest(
            request_id=row["request_id"],
            budget_tokens=row["budget_tokens"],
            estimated_prompt_tokens=row["estimated_prompt_tokens"],
            reserved_output_tokens=row["reserved_output_tokens"],
            reserved_tool_tokens=row["reserved_tool_tokens"],
            created_at=row["created_at"],
            entries=tuple(
                StoredContextEntry(
                    request_id=entry["request_id"],
                    position=entry["position"],
                    source_type=entry["source_type"],
                    source_reference=entry["source_reference"],
                    trust_level=entry["trust_level"],
                    priority=entry["priority"],
                    estimated_tokens=entry["estimated_tokens"],
                    truncated=bool(entry["truncated"]),
                    content_hash=entry["content_hash"],
                    reason_selected=entry["reason_selected"],
                    included=bool(entry["included"]),
                    omission_reason=entry["omission_reason"],
                )
                for entry in entry_rows
            ),
        )

    @staticmethod
    def _normalize_entry(
        request_id: str,
        position: int,
        entry: Mapping[str, Any],
    ) -> StoredContextEntry:
        required_text = (
            "source_type",
            "source_reference",
            "trust_level",
            "content_hash",
            "reason_selected",
        )
        values = {}
        for key in required_text:
            value = entry.get(key)
            if not isinstance(value, str) or (key != "source_reference" and not value):
                raise StorageError(
                    "Context manifest entry has an invalid {0}.".format(key)
                )
            values[key] = value
        if (
            len(values["source_reference"]) > 2048
            or "\x00" in values["source_reference"]
            or len(values["reason_selected"]) > 512
        ):
            raise StorageError("Context manifest entry text is too large.")
        expected_trust = _SOURCE_TRUST.get(values["source_type"])
        if expected_trust is None:
            raise StorageError("Context manifest entry has an invalid source type.")
        if values["trust_level"] != expected_trust:
            raise StorageError("Context manifest entry has an invalid trust level.")
        if not re.fullmatch(r"[0-9a-f]{64}", values["content_hash"]):
            raise StorageError("Context manifest entry has an invalid content hash.")
        priority = entry.get("priority")
        estimated = entry.get("estimated_tokens")
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or not 0 <= priority <= 100
        ):
            raise StorageError("Context manifest entry has an invalid priority.")
        if (
            not isinstance(estimated, int)
            or isinstance(estimated, bool)
            or estimated < 0
        ):
            raise StorageError("Context manifest entry has invalid token usage.")
        truncated = entry.get("truncated")
        included = entry.get("included")
        if not isinstance(truncated, bool) or not isinstance(included, bool):
            raise StorageError("Context manifest entry flags must be booleans.")
        omission_reason = entry.get("omission_reason")
        if omission_reason is not None and (
            not isinstance(omission_reason, str) or len(omission_reason) > 512
        ):
            raise StorageError("Context omission reason must be bounded text or null.")
        return StoredContextEntry(
            request_id=request_id,
            position=position,
            source_type=values["source_type"],
            source_reference=values["source_reference"],
            trust_level=values["trust_level"],
            priority=priority,
            estimated_tokens=estimated,
            truncated=truncated,
            content_hash=values["content_hash"],
            reason_selected=values["reason_selected"],
            included=included,
            omission_reason=omission_reason,
        )
