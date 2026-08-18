import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..errors import StorageError
from ..protocol import Usage
from .database import Database


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    request_summary: Optional[str]
    scope_key: Optional[str]
    started_at: str
    finished_at: Optional[str]
    status: str
    provider: Optional[str]
    model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]


class RequestStore:
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
        scope_key: Optional[str],
        provider: str,
        model: str,
        request_id: Optional[str] = None,
        request_summary: Optional[str] = None,
    ) -> RequestRecord:
        identifier = request_id or uuid.uuid4().hex
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO requests(
                        request_id, request_summary, scope_key, started_at,
                        status, provider, model
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        request_summary,
                        scope_key,
                        self._now(),
                        "running",
                        provider,
                        model,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("Unable to create request record.") from exc
        record = self.get(identifier)
        if record is None:
            raise StorageError("Request record was not persisted.")
        return record

    def finish(
        self,
        request_id: str,
        status: str,
        usage: Optional[Usage] = None,
    ) -> RequestRecord:
        input_tokens = None if usage is None else usage.input_tokens
        output_tokens = None if usage is None else usage.output_tokens
        try:
            with self.database.transaction(immediate=True) as connection:
                cursor = connection.execute(
                    """
                    UPDATE requests
                    SET finished_at = ?, status = ?, input_tokens = ?,
                        output_tokens = ?
                    WHERE request_id = ? AND finished_at IS NULL
                    """,
                    (
                        self._now(),
                        status,
                        input_tokens,
                        output_tokens,
                        request_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StorageError(
                        "Request is missing or already finished: {0}".format(request_id)
                    )
        except sqlite3.Error as exc:
            raise StorageError("Unable to finish request record.") from exc
        record = self.get(request_id)
        if record is None:
            raise StorageError("Finished request record is missing.")
        return record

    def get(self, request_id: str) -> Optional[RequestRecord]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return None if row is None else RequestRecord(**dict(row))
