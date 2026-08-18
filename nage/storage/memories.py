import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..errors import StorageError
from ..memory import normalize_memory_content, significant_tokens
from .database import Database

_ALLOWED_KINDS = {
    "preference",
    "instruction",
    "profile",
    "project",
    "fact",
    "uncategorized",
}
_ALLOWED_SCOPES = {"global", "workspace", "repository"}
_ALLOWED_SENSITIVITIES = {"normal", "sensitive"}
_ALLOWED_STATUSES = {"active", "candidate", "superseded", "forgotten"}
_ALLOWED_SOURCE_TYPES = {
    "explicit_user_statement",
    "strong_implicit_preference",
    "repeated_user_evidence",
    "legacy_import",
    "user_tool",
}


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    kind: str
    content: str
    normalized_content: str
    scope_type: str
    scope_key: str
    source_type: str
    source_reference: Optional[str]
    confidence: float
    evidence_count: int
    sensitivity: str
    status: str
    created_at: str
    updated_at: str
    expires_at: Optional[str]
    source_hash: Optional[str]
    supersedes_id: Optional[str]


@dataclass(frozen=True)
class MemoryWriteResult:
    record: MemoryRecord
    disposition: str


class MemoryStore:
    def __init__(self, database: Database, enable_fts: bool = True) -> None:
        self.database = database
        self.fts_available = False
        if enable_fts:
            self.fts_available = self._initialize_fts()

    @staticmethod
    def _now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _initialize_fts(self) -> bool:
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                        memory_id UNINDEXED,
                        content,
                        normalized_content,
                        tokenize = 'unicode61'
                    )
                    """
                )
                connection.execute("DELETE FROM memories_fts")
                connection.execute(
                    """
                    INSERT INTO memories_fts(memory_id, content, normalized_content)
                    SELECT id, content, normalized_content FROM memories
                    WHERE status = 'active' AND sensitivity = 'normal'
                    """
                )
            return True
        except sqlite3.Error:
            return False

    def upsert(
        self,
        content: str,
        kind: str,
        scope_type: str,
        scope_key: str,
        source_type: str,
        confidence: float,
        sensitivity: str = "normal",
        status: str = "active",
        source_reference: Optional[str] = None,
        expires_at: Optional[str] = None,
        source_hash: Optional[str] = None,
        supersedes_id: Optional[str] = None,
    ) -> MemoryWriteResult:
        values = self._validated_values(
            content=content,
            kind=kind,
            scope_type=scope_type,
            scope_key=scope_key,
            source_type=source_type,
            confidence=confidence,
            sensitivity=sensitivity,
            status=status,
            source_reference=source_reference,
            expires_at=expires_at,
            source_hash=source_hash,
            supersedes_id=supersedes_id,
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                memory_id, disposition = self._upsert_connection(connection, values)
        except sqlite3.Error as exc:
            raise StorageError("Unable to store memory.") from exc
        record = self.get(memory_id)
        if record is None:
            raise StorageError("Stored memory is missing.")
        return MemoryWriteResult(record=record, disposition=disposition)

    def _upsert_connection(
        self,
        connection: sqlite3.Connection,
        values: Mapping[str, Any],
    ) -> Tuple[str, str]:
        existing = connection.execute(
            """
            SELECT * FROM memories
            WHERE normalized_content = ? AND kind = ?
              AND scope_type = ? AND scope_key = ?
              AND status IN ('active', 'candidate')
            ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, updated_at DESC
            LIMIT 1
            """,
            (
                values["normalized_content"],
                values["kind"],
                values["scope_type"],
                values["scope_key"],
            ),
        ).fetchone()
        now = self._now()
        if existing is not None:
            memory_id = existing["id"]
            new_status = existing["status"]
            if existing["status"] == "candidate" and values["status"] == "active":
                new_status = "active"
            connection.execute(
                """
                UPDATE memories
                SET confidence = ?, evidence_count = evidence_count + 1,
                    updated_at = ?, source_type = ?, source_reference = ?,
                    source_hash = ?, status = ?, expires_at = ?
                WHERE id = ?
                """,
                (
                    max(float(existing["confidence"]), values["confidence"]),
                    now,
                    values["source_type"],
                    values["source_reference"],
                    values["source_hash"],
                    new_status,
                    values["expires_at"],
                    memory_id,
                ),
            )
            self._event(
                connection,
                memory_id,
                "merge",
                existing["status"],
                new_status,
            )
            self._sync_fts(connection, memory_id)
            return memory_id, "merged"

        memory_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO memories(
                id, kind, content, normalized_content, scope_type, scope_key,
                source_type, source_reference, confidence, evidence_count,
                sensitivity, status, created_at, updated_at, expires_at,
                source_hash, supersedes_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                values["kind"],
                values["content"],
                values["normalized_content"],
                values["scope_type"],
                values["scope_key"],
                values["source_type"],
                values["source_reference"],
                values["confidence"],
                values["sensitivity"],
                values["status"],
                now,
                now,
                values["expires_at"],
                values["source_hash"],
                values["supersedes_id"],
            ),
        )
        self._event(connection, memory_id, "create", None, values["status"])
        self._sync_fts(connection, memory_id)
        return memory_id, "created"

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return None if row is None else MemoryRecord(**dict(row))

    def list(
        self,
        statuses: Sequence[str] = ("active",),
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[MemoryRecord, ...]:
        if not statuses or any(status not in _ALLOWED_STATUSES for status in statuses):
            raise StorageError("Unsupported memory status filter.")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 1000
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise StorageError("Memory listing bounds are invalid.")
        placeholders = ",".join("?" for _ in statuses)
        clauses = ["status IN ({0})".format(placeholders)]
        parameters: List[Any] = list(statuses)
        if scope_type is not None:
            if scope_type not in _ALLOWED_SCOPES:
                raise StorageError("Unsupported memory scope.")
            clauses.append("scope_type = ?")
            parameters.append(scope_type)
        if scope_key is not None:
            clauses.append("scope_key = ?")
            parameters.append(scope_key)
        parameters.extend([limit, offset])
        sql = (
            "SELECT * FROM memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?"
        )
        with self.database.connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return tuple(MemoryRecord(**dict(row)) for row in rows)

    def search(
        self,
        query: str,
        workspace_key: Optional[str] = None,
        repository_key: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[MemoryRecord, ...]:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise StorageError("Memory search limit is invalid.")
        normalized_query = normalize_memory_content(query)
        if not normalized_query:
            return self._recent_in_scope(workspace_key, repository_key, limit)
        if self.fts_available:
            try:
                records = self._search_fts(
                    normalized_query, workspace_key, repository_key, limit
                )
                if records:
                    return records
            except sqlite3.Error:
                self.fts_available = False
        return self._search_fallback(
            normalized_query, workspace_key, repository_key, limit
        )

    def retrieve(
        self,
        query: str,
        workspace_key: Optional[str],
        repository_key: Optional[str],
        limit: int = 5,
    ) -> Tuple[MemoryRecord, ...]:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 20
        ):
            raise StorageError("Memory retrieval limit is invalid.")
        with self.database.connection() as connection:
            global_rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE status = 'active' AND sensitivity = 'normal'
                  AND scope_type = 'global'
                  AND kind IN ('instruction', 'preference')
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY CASE kind WHEN 'instruction' THEN 0 ELSE 1 END,
                         updated_at DESC
                LIMIT 2
                """,
                (self._now(),),
            ).fetchall()
        selected = [MemoryRecord(**dict(row)) for row in global_rows]
        seen = {record.id for record in selected}
        for record in self.search(
            query,
            workspace_key=workspace_key,
            repository_key=repository_key,
            limit=limit * 2,
        ):
            if record.id not in seen:
                selected.append(record)
                seen.add(record.id)
            if len(selected) >= limit:
                break
        return tuple(selected[:limit])

    def forget(self, memory_id: str) -> MemoryRecord:
        return self._transition(memory_id, "forgotten", {"active", "candidate"})

    def restore(self, memory_id: str) -> MemoryRecord:
        return self._transition(memory_id, "active", {"forgotten", "superseded"})

    def activate(self, memory_id: str) -> MemoryRecord:
        return self._transition(memory_id, "active", {"candidate"})

    def _transition(
        self,
        memory_id: str,
        new_status: str,
        allowed_previous: Iterable[str],
    ) -> MemoryRecord:
        allowed = set(allowed_previous)
        try:
            with self.database.transaction(immediate=True) as connection:
                row = connection.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    raise StorageError("Memory does not exist: {0}".format(memory_id))
                if row["status"] not in allowed:
                    raise StorageError(
                        "Memory cannot transition from {0} to {1}.".format(
                            row["status"], new_status
                        )
                    )
                if new_status == "active":
                    duplicate = connection.execute(
                        """
                        SELECT id FROM memories
                        WHERE normalized_content = ? AND kind = ?
                          AND scope_type = ? AND scope_key = ?
                          AND status = 'active' AND id != ?
                        LIMIT 1
                        """,
                        (
                            row["normalized_content"],
                            row["kind"],
                            row["scope_type"],
                            row["scope_key"],
                            memory_id,
                        ),
                    ).fetchone()
                    if duplicate is not None:
                        raise StorageError(
                            "An equivalent active memory already exists."
                        )
                now = self._now()
                connection.execute(
                    "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, memory_id),
                )
                self._event(
                    connection,
                    memory_id,
                    "transition",
                    row["status"],
                    new_status,
                )
                self._sync_fts(connection, memory_id)
        except sqlite3.Error as exc:
            raise StorageError("Unable to update memory status.") from exc
        record = self.get(memory_id)
        if record is None:
            raise StorageError("Updated memory is missing.")
        return record

    def correct(
        self,
        memory_id: str,
        content: str,
        kind: Optional[str] = None,
        source_reference: Optional[str] = None,
        source_hash: Optional[str] = None,
    ) -> MemoryWriteResult:
        original = self.get(memory_id)
        if original is None or original.status not in {"active", "candidate"}:
            raise StorageError("Memory is unavailable for correction.")
        values = self._validated_values(
            content=content,
            kind=kind or original.kind,
            scope_type=original.scope_type,
            scope_key=original.scope_key,
            source_type="user_tool",
            confidence=1.0,
            sensitivity="normal",
            status="active",
            source_reference=source_reference,
            expires_at=original.expires_at,
            source_hash=source_hash,
            supersedes_id=memory_id,
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                current = connection.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if current is None or current["status"] not in {"active", "candidate"}:
                    raise StorageError("Memory changed before correction.")
                connection.execute(
                    "UPDATE memories SET status = 'superseded', updated_at = ? "
                    "WHERE id = ?",
                    (self._now(), memory_id),
                )
                self._sync_fts(connection, memory_id)
                new_id, disposition = self._upsert_connection(connection, values)
                self._event(
                    connection,
                    memory_id,
                    "supersede",
                    current["status"],
                    "superseded",
                )
        except sqlite3.Error as exc:
            raise StorageError("Unable to correct memory.") from exc
        record = self.get(new_id)
        if record is None:
            raise StorageError("Corrected memory is missing.")
        return MemoryWriteResult(record=record, disposition=disposition)

    def supersede(
        self,
        memory_ids: Sequence[str],
        content: str,
        kind: str,
        scope_type: str,
        scope_key: str,
        source_type: str,
        confidence: float,
        source_reference: Optional[str],
        source_hash: Optional[str],
    ) -> MemoryWriteResult:
        unique_ids = tuple(dict.fromkeys(memory_ids))
        if not unique_ids or len(unique_ids) > 16:
            raise StorageError("Supersede targets are invalid.")
        values = self._validated_values(
            content=content,
            kind=kind,
            scope_type=scope_type,
            scope_key=scope_key,
            source_type=source_type,
            confidence=confidence,
            sensitivity="normal",
            status="active",
            source_reference=source_reference,
            expires_at=None,
            source_hash=source_hash,
            supersedes_id=unique_ids[0],
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                placeholders = ",".join("?" for _ in unique_ids)
                rows = connection.execute(
                    "SELECT * FROM memories WHERE id IN ({0})".format(placeholders),
                    unique_ids,
                ).fetchall()
                if len(rows) != len(unique_ids) or any(
                    row["status"] != "active" for row in rows
                ):
                    raise StorageError("Superseded memories are not all active.")
                if any(
                    row["scope_type"] != scope_type or row["scope_key"] != scope_key
                    for row in rows
                ):
                    raise StorageError(
                        "Superseded memories must share the replacement scope."
                    )
                now = self._now()
                connection.execute(
                    "UPDATE memories SET status = 'superseded', updated_at = ? "
                    "WHERE id IN ({0})".format(placeholders),
                    (now,) + unique_ids,
                )
                for row in rows:
                    self._sync_fts(connection, row["id"])
                    self._event(
                        connection,
                        row["id"],
                        "supersede",
                        "active",
                        "superseded",
                    )
                new_id, disposition = self._upsert_connection(connection, values)
        except sqlite3.Error as exc:
            raise StorageError("Unable to supersede memories.") from exc
        record = self.get(new_id)
        if record is None:
            raise StorageError("Superseding memory is missing.")
        return MemoryWriteResult(record=record, disposition=disposition)

    def clear(
        self,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
    ) -> int:
        clauses = ["status IN ('active', 'candidate')"]
        parameters: List[Any] = []
        if scope_type is not None:
            if scope_type not in _ALLOWED_SCOPES:
                raise StorageError("Unsupported memory scope.")
            clauses.append("scope_type = ?")
            parameters.append(scope_type)
        if scope_key is not None:
            clauses.append("scope_key = ?")
            parameters.append(scope_key)
        try:
            with self.database.transaction(immediate=True) as connection:
                rows = connection.execute(
                    "SELECT id, status FROM memories WHERE " + " AND ".join(clauses),
                    tuple(parameters),
                ).fetchall()
                now = self._now()
                connection.execute(
                    "UPDATE memories SET status = 'forgotten', updated_at = ? WHERE "
                    + " AND ".join(clauses),
                    (now,) + tuple(parameters),
                )
                for row in rows:
                    self._sync_fts(connection, row["id"])
                    self._event(
                        connection,
                        row["id"],
                        "clear",
                        row["status"],
                        "forgotten",
                    )
        except sqlite3.Error as exc:
            raise StorageError("Unable to clear memories.") from exc
        return len(rows)

    def state_digest(
        self,
        scope_type: Optional[str] = None,
        scope_key: Optional[str] = None,
    ) -> Mapping[str, Any]:
        import hashlib

        clauses = ["status IN ('active', 'candidate')"]
        parameters: List[Any] = []
        if scope_type is not None:
            if scope_type not in _ALLOWED_SCOPES:
                raise StorageError("Unsupported memory scope.")
            clauses.append("scope_type = ?")
            parameters.append(scope_type)
        if scope_key is not None:
            clauses.append("scope_key = ?")
            parameters.append(scope_key)
        digest = hashlib.sha256()
        count = 0
        with self.database.connection() as connection:
            cursor = connection.execute(
                "SELECT id, status, updated_at FROM memories WHERE "
                + " AND ".join(clauses)
                + " ORDER BY id",
                tuple(parameters),
            )
            while True:
                rows = cursor.fetchmany(256)
                if not rows:
                    break
                for row in rows:
                    digest.update(
                        "{0}\x00{1}\x00{2}\n".format(
                            row["id"], row["status"], row["updated_at"]
                        ).encode("utf-8")
                    )
                    count += 1
        return {"count": count, "sha256": digest.hexdigest()}

    def import_legacy(
        self,
        entries: Sequence[Mapping[str, Any]],
        backup_path: str,
        report: Mapping[str, Any],
    ) -> bool:
        marker_name = "legacy_v1_memory"
        try:
            with self.database.transaction(immediate=True) as connection:
                marker = connection.execute(
                    "SELECT 1 FROM legacy_migrations WHERE name = ?",
                    (marker_name,),
                ).fetchone()
                if marker is not None:
                    return False
                for entry in entries:
                    values = self._validated_values(**dict(entry))
                    self._upsert_connection(connection, values)
                connection.execute(
                    """
                    INSERT INTO legacy_migrations(
                        name, backup_path, report_json, completed_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        marker_name,
                        backup_path,
                        json.dumps(report, ensure_ascii=False, sort_keys=True),
                        self._now(),
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("Unable to import legacy memories.") from exc
        return True

    def legacy_report(self) -> Optional[Mapping[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM legacy_migrations WHERE name = 'legacy_v1_memory'"
            ).fetchone()
        if row is None:
            return None
        try:
            report = json.loads(row["report_json"])
        except json.JSONDecodeError as exc:
            raise StorageError("Legacy memory report is corrupt.") from exc
        report["backup_path"] = row["backup_path"]
        report["completed_at"] = row["completed_at"]
        return report

    def _search_fts(
        self,
        query: str,
        workspace_key: Optional[str],
        repository_key: Optional[str],
        limit: int,
    ) -> Tuple[MemoryRecord, ...]:
        tokens = sorted(significant_tokens(query))[:12]
        if not tokens:
            return ()
        match_query = " OR ".join(
            '"{0}"'.format(token.replace('"', '""')) for token in tokens
        )
        scope_sql, scope_parameters = self._scope_sql(workspace_key, repository_key)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT m.*, bm25(memories_fts) AS rank
                FROM memories_fts
                JOIN memories m ON m.id = memories_fts.memory_id
                WHERE memories_fts MATCH ? AND m.status = 'active'
                  AND m.sensitivity = 'normal'
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                  AND {0}
                ORDER BY rank, m.updated_at DESC, m.id
                LIMIT ?
                """.format(scope_sql),
                (match_query, self._now()) + scope_parameters + (limit,),
            ).fetchall()
        return tuple(
            MemoryRecord(**{key: row[key] for key in MemoryRecord.__annotations__})
            for row in rows
        )

    def _search_fallback(
        self,
        query: str,
        workspace_key: Optional[str],
        repository_key: Optional[str],
        limit: int,
    ) -> Tuple[MemoryRecord, ...]:
        scope_sql, scope_parameters = self._scope_sql(workspace_key, repository_key)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE status = 'active' AND sensitivity = 'normal'
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND {0}
                ORDER BY updated_at DESC
                LIMIT 500
                """.format(scope_sql),
                (self._now(),) + scope_parameters,
            ).fetchall()
        query_tokens = significant_tokens(query)
        ranked = []
        for row in rows:
            record = MemoryRecord(**dict(row))
            content_tokens = significant_tokens(record.normalized_content)
            overlap = len(query_tokens.intersection(content_tokens))
            if (
                overlap == 0
                and query.casefold() not in record.normalized_content.casefold()
            ):
                continue
            scope_bonus = 2 if record.scope_type != "global" else 0
            kind_bonus = 1 if record.kind in {"instruction", "preference"} else 0
            try:
                updated_timestamp = datetime.fromisoformat(
                    record.updated_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                updated_timestamp = 0.0
            ranked.append(
                (
                    -(overlap * 10 + scope_bonus + kind_bonus),
                    -updated_timestamp,
                    record,
                )
            )
        ranked.sort(key=lambda item: (item[0], item[1], item[2].id))
        return tuple(record for _, _, record in ranked[:limit])

    def _recent_in_scope(
        self,
        workspace_key: Optional[str],
        repository_key: Optional[str],
        limit: int,
    ) -> Tuple[MemoryRecord, ...]:
        scope_sql, scope_parameters = self._scope_sql(workspace_key, repository_key)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE status = 'active' AND sensitivity = 'normal'
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND {0}
                ORDER BY updated_at DESC, id LIMIT ?
                """.format(scope_sql),
                (self._now(),) + scope_parameters + (limit,),
            ).fetchall()
        return tuple(MemoryRecord(**dict(row)) for row in rows)

    @staticmethod
    def _scope_sql(
        workspace_key: Optional[str],
        repository_key: Optional[str],
    ) -> Tuple[str, Tuple[str, ...]]:
        clauses = ["scope_type = 'global'"]
        parameters = []
        if workspace_key is not None:
            clauses.append("(scope_type = 'workspace' AND scope_key = ?)")
            parameters.append(workspace_key)
        if repository_key is not None:
            clauses.append("(scope_type = 'repository' AND scope_key = ?)")
            parameters.append(repository_key)
        return "(" + " OR ".join(clauses) + ")", tuple(parameters)

    def _sync_fts(self, connection: sqlite3.Connection, memory_id: str) -> None:
        if not self.fts_available:
            return
        connection.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
        row = connection.execute(
            "SELECT id, content, normalized_content, status, sensitivity "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if (
            row is not None
            and row["status"] == "active"
            and row["sensitivity"] == "normal"
        ):
            connection.execute(
                """
                INSERT INTO memories_fts(memory_id, content, normalized_content)
                VALUES (?, ?, ?)
                """,
                (row["id"], row["content"], row["normalized_content"]),
            )

    def _event(
        self,
        connection: sqlite3.Connection,
        memory_id: str,
        operation: str,
        previous_status: Optional[str],
        new_status: Optional[str],
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_events(
                event_id, memory_id, operation, previous_status,
                new_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                memory_id,
                operation,
                previous_status,
                new_status,
                self._now(),
            ),
        )

    @staticmethod
    def _validated_values(
        content: str,
        kind: str,
        scope_type: str,
        scope_key: str,
        source_type: str,
        confidence: float,
        sensitivity: str,
        status: str,
        source_reference: Optional[str],
        expires_at: Optional[str],
        source_hash: Optional[str],
        supersedes_id: Optional[str],
    ) -> Dict[str, Any]:
        normalized = normalize_memory_content(content)
        if not normalized or len(normalized) > 2000:
            raise StorageError("Memory content must contain 1 to 2000 characters.")
        if kind not in _ALLOWED_KINDS:
            raise StorageError("Unsupported memory kind.")
        if scope_type not in _ALLOWED_SCOPES:
            raise StorageError("Unsupported memory scope.")
        if not isinstance(scope_key, str) or len(scope_key) > 4096:
            raise StorageError("Memory scope key is invalid.")
        if (scope_type == "global" and scope_key) or (
            scope_type != "global" and not scope_key
        ):
            raise StorageError("Memory scope key does not match its scope type.")
        if source_type not in _ALLOWED_SOURCE_TYPES:
            raise StorageError("Unsupported memory source type.")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0.0 <= confidence <= 1.0
        ):
            raise StorageError("Memory confidence must be between 0 and 1.")
        if sensitivity not in _ALLOWED_SENSITIVITIES:
            raise StorageError("Unsupported memory sensitivity.")
        if status not in _ALLOWED_STATUSES:
            raise StorageError("Unsupported memory status.")
        if source_reference is not None and (
            not isinstance(source_reference, str) or len(source_reference) > 512
        ):
            raise StorageError("Memory source reference is invalid.")
        if expires_at is not None:
            if not isinstance(expires_at, str):
                raise StorageError("Memory expiration is invalid.")
            try:
                expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiration.tzinfo is None:
                    raise ValueError("timezone is required")
            except ValueError as exc:
                raise StorageError("Memory expiration is invalid.") from exc
        if source_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise StorageError("Memory source hash is invalid.")
        if supersedes_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", supersedes_id
        ):
            raise StorageError("Superseded memory ID is invalid.")
        return {
            "content": normalized,
            "normalized_content": normalized.casefold(),
            "kind": kind,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "source_type": source_type,
            "confidence": float(confidence),
            "sensitivity": sensitivity,
            "status": status,
            "source_reference": source_reference,
            "expires_at": expires_at,
            "source_hash": source_hash,
            "supersedes_id": supersedes_id,
        }
