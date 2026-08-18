import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..errors import StorageError
from ..memory import contains_secret
from .database import Database

_AUTHORITY_KEYS = {
    "approval",
    "approved",
    "authorization",
    "confirmation",
    "confirmed",
    "permission",
    "plan_hash",
}
_ALLOWED_SCOPES = {"workspace", "repository"}
_ALLOWED_SENSITIVITIES = {"normal", "sensitive"}


@dataclass(frozen=True)
class ContinuationRecord:
    task_id: str
    label: str
    intent: str
    known: Mapping[str, Any]
    missing: Tuple[str, ...]
    scope_type: str
    scope_key: str
    terminal_key: Optional[str]
    sensitivity: str
    status: str
    created_at: str
    updated_at: str
    implicit_until: str
    retain_until: str


@dataclass(frozen=True)
class ContinuationCleanup:
    dormant: int
    expired: int


class ContinuationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _format_time(value: datetime) -> str:
        if value.tzinfo is None:
            raise StorageError("Continuation timestamps require a timezone.")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def create(
        self,
        label: str,
        intent: str,
        known: Mapping[str, Any],
        missing: Sequence[str],
        scope_type: str,
        scope_key: str,
        terminal_key: Optional[str],
        sensitivity: str,
        now: Optional[datetime] = None,
        hot_seconds: int = 7200,
        retain_seconds: int = 2592000,
    ) -> ContinuationRecord:
        values = self._validated_values(
            label=label,
            intent=intent,
            known=known,
            missing=missing,
            scope_type=scope_type,
            scope_key=scope_key,
            terminal_key=terminal_key,
            sensitivity=sensitivity,
        )
        if sensitivity == "sensitive":
            hot_seconds = min(hot_seconds, 1800)
            retain_seconds = min(retain_seconds, 86400)
        if hot_seconds <= 0 or retain_seconds < hot_seconds:
            raise StorageError("Continuation retention bounds are invalid.")
        created = now or self._now()
        if created.tzinfo is None:
            raise StorageError("Continuation timestamps require a timezone.")
        created = created.astimezone(timezone.utc)
        task_id = "task-{0}".format(uuid.uuid4().hex[:12])
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO continuations(
                        task_id, label, intent, known_json, missing_json,
                        scope_type, scope_key, terminal_key, sensitivity,
                        status, created_at, updated_at, implicit_until,
                        retain_until
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        values["label"],
                        values["intent"],
                        values["known_json"],
                        values["missing_json"],
                        values["scope_type"],
                        values["scope_key"],
                        values["terminal_key"],
                        values["sensitivity"],
                        "hot",
                        self._format_time(created),
                        self._format_time(created),
                        self._format_time(created + timedelta(seconds=hot_seconds)),
                        self._format_time(created + timedelta(seconds=retain_seconds)),
                    ),
                )
                self._event(connection, task_id, "create", None, "hot")
        except sqlite3.Error as exc:
            raise StorageError("Unable to create continuation capsule.") from exc
        record = self.get(task_id)
        if record is None:
            raise StorageError("Continuation capsule was not persisted.")
        return record

    def update(
        self,
        task_id: str,
        label: str,
        intent: str,
        known: Mapping[str, Any],
        missing: Sequence[str],
        sensitivity: str,
        now: Optional[datetime] = None,
        hot_seconds: int = 7200,
        retain_seconds: int = 2592000,
    ) -> ContinuationRecord:
        current = self.get(task_id)
        if current is None:
            raise StorageError("Continuation capsule does not exist.")
        values = self._validated_values(
            label=label,
            intent=intent,
            known=known,
            missing=missing,
            scope_type=current.scope_type,
            scope_key=current.scope_key,
            terminal_key=current.terminal_key,
            sensitivity=sensitivity,
        )
        if sensitivity == "sensitive":
            hot_seconds = min(hot_seconds, 1800)
            retain_seconds = min(retain_seconds, 86400)
        if hot_seconds <= 0 or retain_seconds < hot_seconds:
            raise StorageError("Continuation retention bounds are invalid.")
        updated = now or self._now()
        if updated.tzinfo is None:
            raise StorageError("Continuation timestamps require a timezone.")
        updated = updated.astimezone(timezone.utc)
        try:
            with self.database.transaction(immediate=True) as connection:
                cursor = connection.execute(
                    """
                    UPDATE continuations
                    SET label = ?, intent = ?, known_json = ?, missing_json = ?,
                        sensitivity = ?, status = 'hot', updated_at = ?,
                        implicit_until = ?, retain_until = ?
                    WHERE task_id = ?
                    """,
                    (
                        values["label"],
                        values["intent"],
                        values["known_json"],
                        values["missing_json"],
                        values["sensitivity"],
                        self._format_time(updated),
                        self._format_time(updated + timedelta(seconds=hot_seconds)),
                        self._format_time(updated + timedelta(seconds=retain_seconds)),
                        task_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StorageError("Continuation changed before update.")
                self._event(connection, task_id, "update", current.status, "hot")
        except sqlite3.Error as exc:
            raise StorageError("Unable to update continuation capsule.") from exc
        record = self.get(task_id)
        if record is None:
            raise StorageError("Updated continuation capsule is missing.")
        return record

    def get(self, task_id: str) -> Optional[ContinuationRecord]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM continuations WHERE task_id = ?", (task_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def candidates(
        self,
        scope_type: str,
        scope_key: str,
        now: Optional[datetime] = None,
        limit: int = 20,
    ) -> Tuple[ContinuationRecord, ...]:
        if scope_type not in _ALLOWED_SCOPES or not scope_key:
            raise StorageError("Continuation lookup scope is invalid.")
        if not 1 <= limit <= 100:
            raise StorageError("Continuation lookup limit is invalid.")
        threshold = self._format_time(now or self._now())
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM continuations
                WHERE status IN ('hot', 'dormant', 'pinned')
                  AND retain_until > ?
                  AND scope_type = ? AND scope_key = ?
                ORDER BY CASE status WHEN 'hot' THEN 0 WHEN 'pinned' THEN 1 ELSE 2 END,
                         updated_at DESC, task_id
                LIMIT ?
                """,
                (threshold, scope_type, scope_key, limit),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def complete(self, task_id: str, now: Optional[datetime] = None) -> bool:
        current = self.get(task_id)
        if current is None:
            return False
        try:
            with self.database.transaction(immediate=True) as connection:
                cursor = connection.execute(
                    "DELETE FROM continuations WHERE task_id = ?", (task_id,)
                )
                if cursor.rowcount != 1:
                    return False
                self._event(
                    connection,
                    task_id,
                    "complete",
                    current.status,
                    "completed",
                    now=now,
                )
        except sqlite3.Error as exc:
            raise StorageError("Unable to complete continuation capsule.") from exc
        return True

    def cleanup(self, now: Optional[datetime] = None) -> ContinuationCleanup:
        current = now or self._now()
        if current.tzinfo is None:
            raise StorageError("Continuation timestamps require a timezone.")
        current = current.astimezone(timezone.utc)
        threshold = self._format_time(current)
        dormant = 0
        expired = 0
        try:
            with self.database.transaction(immediate=True) as connection:
                rows = connection.execute(
                    """
                    SELECT task_id, status, implicit_until, retain_until
                    FROM continuations
                    WHERE status IN ('hot', 'dormant')
                    """
                ).fetchall()
                for row in rows:
                    if row["retain_until"] <= threshold:
                        connection.execute(
                            "DELETE FROM continuations WHERE task_id = ?",
                            (row["task_id"],),
                        )
                        self._event(
                            connection,
                            row["task_id"],
                            "expire",
                            row["status"],
                            "expired",
                            now=current,
                        )
                        expired += 1
                    elif row["status"] == "hot" and row["implicit_until"] <= threshold:
                        connection.execute(
                            """
                            UPDATE continuations
                            SET status = 'dormant', updated_at = ?
                            WHERE task_id = ?
                            """,
                            (threshold, row["task_id"]),
                        )
                        self._event(
                            connection,
                            row["task_id"],
                            "dormant",
                            "hot",
                            "dormant",
                            now=current,
                        )
                        dormant += 1
        except sqlite3.Error as exc:
            raise StorageError("Unable to clean continuation capsules.") from exc
        return ContinuationCleanup(dormant=dormant, expired=expired)

    @staticmethod
    def _validated_values(
        label: str,
        intent: str,
        known: Mapping[str, Any],
        missing: Sequence[str],
        scope_type: str,
        scope_key: str,
        terminal_key: Optional[str],
        sensitivity: str,
    ) -> Dict[str, Any]:
        normalized_label = " ".join(label.split()) if isinstance(label, str) else ""
        if not normalized_label or len(normalized_label) > 120:
            raise StorageError("Continuation label is invalid.")
        if not isinstance(intent, str) or not re.fullmatch(
            r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$", intent
        ):
            raise StorageError("Continuation intent is invalid.")
        if not isinstance(known, Mapping):
            raise StorageError("Continuation known fields must be an object.")
        known_data = dict(known)
        ContinuationStore._validate_known(known_data)
        missing_values = tuple(missing)
        if not missing_values or len(missing_values) > 32:
            raise StorageError("Continuation missing fields are invalid.")
        if len(missing_values) != len(set(missing_values)) or any(
            not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", value)
            for value in missing_values
        ):
            raise StorageError("Continuation missing fields are invalid.")
        if scope_type not in _ALLOWED_SCOPES or not scope_key:
            raise StorageError("Continuation scope is invalid.")
        if terminal_key is not None and (
            not isinstance(terminal_key, str) or len(terminal_key) > 256
        ):
            raise StorageError("Continuation terminal key is invalid.")
        if sensitivity not in _ALLOWED_SENSITIVITIES:
            raise StorageError("Continuation sensitivity is unsupported.")
        try:
            known_json = json.dumps(
                known_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            missing_json = json.dumps(missing_values, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise StorageError("Continuation fields are not canonical JSON.") from exc
        if len(known_json.encode("utf-8")) > 16384:
            raise StorageError("Continuation known fields exceed the size limit.")
        return {
            "label": normalized_label,
            "intent": intent,
            "known_json": known_json,
            "missing_json": missing_json,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "terminal_key": terminal_key,
            "sensitivity": sensitivity,
        }

    @staticmethod
    def _validate_known(value: Any, key: Optional[str] = None) -> None:
        if key is not None:
            normalized_key = key.casefold().replace("-", "_")
            key_parts = set(normalized_key.split("_"))
            if normalized_key in _AUTHORITY_KEYS or key_parts.intersection(
                _AUTHORITY_KEYS
            ):
                raise StorageError(
                    "Continuation capsules cannot persist approval or authorization."
                )
        if isinstance(value, Mapping):
            for item_key, item_value in value.items():
                ContinuationStore._validate_known(item_value, str(item_key))
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                ContinuationStore._validate_known(item)
            return
        if isinstance(value, str) and contains_secret(value):
            raise StorageError("Continuation capsules cannot persist secrets.")

    @staticmethod
    def _record(row: sqlite3.Row) -> ContinuationRecord:
        try:
            known = json.loads(row["known_json"])
            missing = tuple(json.loads(row["missing_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise StorageError("Continuation capsule JSON is corrupt.") from exc
        return ContinuationRecord(
            task_id=row["task_id"],
            label=row["label"],
            intent=row["intent"],
            known=known,
            missing=missing,
            scope_type=row["scope_type"],
            scope_key=row["scope_key"],
            terminal_key=row["terminal_key"],
            sensitivity=row["sensitivity"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            implicit_until=row["implicit_until"],
            retain_until=row["retain_until"],
        )

    def _event(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        operation: str,
        previous_status: Optional[str],
        new_status: Optional[str],
        now: Optional[datetime] = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO continuation_events(
                event_id, task_id, operation, previous_status,
                new_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                task_id,
                operation,
                previous_status,
                new_status,
                self._format_time(now or self._now()),
            ),
        )
