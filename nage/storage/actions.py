import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from ..errors import StorageError
from ..tools import RiskLevel
from .database import Database

_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "key",
    "password",
    "payload",
    "private_key",
    "secret",
    "token",
}


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    request_id: Optional[str]
    tool_name: str
    arguments_redacted_json: str
    plan_hash: str
    risk_level: str
    confirmation_status: str
    execution_status: str
    result_summary: Optional[str]
    error_code: Optional[str]
    started_at: str
    finished_at: Optional[str]


class ActionStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def begin(
        self,
        request_id: Optional[str],
        tool_name: str,
        arguments: Mapping[str, Any],
        plan_hash: str,
        risk: RiskLevel,
        confirmation_status: str,
    ) -> ActionRecord:
        action_id = uuid.uuid4().hex
        started_at = self._now()
        try:
            redacted = json.dumps(
                self._redact(dict(arguments)),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise StorageError("Action arguments are not canonical JSON.") from exc
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO actions(
                        action_id, request_id, tool_name,
                        arguments_redacted_json, plan_hash, risk_level,
                        confirmation_status, execution_status, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id,
                        request_id,
                        tool_name,
                        redacted,
                        plan_hash,
                        risk.value,
                        confirmation_status,
                        "pending",
                        started_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("Unable to create action record.") from exc
        record = self.get(action_id)
        if record is None:
            raise StorageError("Action record was not persisted.")
        return record

    def finish(
        self,
        action_id: str,
        execution_status: str,
        result_summary: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> ActionRecord:
        finished_at = self._now()
        try:
            with self.database.transaction(immediate=True) as connection:
                cursor = connection.execute(
                    """
                    UPDATE actions
                    SET execution_status = ?, result_summary = ?,
                        error_code = ?, finished_at = ?
                    WHERE action_id = ? AND finished_at IS NULL
                    """,
                    (
                        execution_status,
                        result_summary,
                        error_code,
                        finished_at,
                        action_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StorageError(
                        "Action is missing or already finished: {0}".format(action_id)
                    )
        except sqlite3.Error as exc:
            raise StorageError("Unable to finish action record.") from exc
        record = self.get(action_id)
        if record is None:
            raise StorageError("Finished action record is missing.")
        return record

    def get(self, action_id: str) -> Optional[ActionRecord]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        return None if row is None else ActionRecord(**dict(row))

    @classmethod
    def _redact(cls, value: Any, key: Optional[str] = None) -> Any:
        if key is not None:
            normalized_key = key.lower().replace("-", "_")
            parts = set(normalized_key.split("_"))
            if normalized_key in _SENSITIVE_KEYS or parts.intersection(_SENSITIVE_KEYS):
                return "***"
        if isinstance(value, dict):
            return {
                str(item_key): cls._redact(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._redact(item) for item in value]
        return value
