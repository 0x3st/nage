import sqlite3
from typing import Callable, Sequence, Tuple

from ..errors import MigrationError

Migration = Tuple[int, Callable[[sqlite3.Connection], None]]
LATEST_SCHEMA_VERSION = 4


def _migration_001_initial_schema(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            normalized_content TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_reference TEXT,
            confidence REAL NOT NULL,
            evidence_count INTEGER NOT NULL DEFAULT 1,
            sensitivity TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            source_hash TEXT
        )
        """,
        """
        CREATE UNIQUE INDEX memories_active_identity
        ON memories(normalized_content, kind, scope_type, scope_key)
        WHERE status = 'active'
        """,
        """
        CREATE INDEX memories_scope_status
        ON memories(scope_type, scope_key, status)
        """,
        """
        CREATE TABLE continuations (
            task_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            intent TEXT NOT NULL,
            known_json TEXT NOT NULL,
            missing_json TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            terminal_key TEXT,
            sensitivity TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            implicit_until TEXT NOT NULL,
            retain_until TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX continuations_lookup
        ON continuations(status, scope_type, scope_key, implicit_until, retain_until)
        """,
        """
        CREATE TABLE requests (
            request_id TEXT PRIMARY KEY,
            request_summary TEXT,
            scope_key TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER
        )
        """,
        """
        CREATE TABLE actions (
            action_id TEXT PRIMARY KEY,
            request_id TEXT,
            tool_name TEXT NOT NULL,
            arguments_redacted_json TEXT NOT NULL,
            plan_hash TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            confirmation_status TEXT NOT NULL,
            execution_status TEXT NOT NULL,
            result_summary TEXT,
            error_code TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(request_id) REFERENCES requests(request_id)
                ON DELETE SET NULL
        )
        """,
        """
        CREATE INDEX actions_request
        ON actions(request_id, started_at)
        """,
        """
        CREATE TABLE artifacts (
            artifact_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX artifacts_expiry
        ON artifacts(expires_at)
        """,
        """
        CREATE TABLE project_files (
            scope_key TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            content_hash TEXT,
            language TEXT,
            index_status TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY(scope_key, relative_path)
        )
        """,
    )

    for statement in statements:
        connection.execute(statement)


def _migration_002_context_manifests(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE context_manifests (
            request_id TEXT PRIMARY KEY,
            budget_tokens INTEGER NOT NULL,
            estimated_prompt_tokens INTEGER NOT NULL,
            reserved_output_tokens INTEGER NOT NULL,
            reserved_tool_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES requests(request_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE context_manifest_entries (
            request_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_reference TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            priority INTEGER NOT NULL,
            estimated_tokens INTEGER NOT NULL,
            truncated INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            reason_selected TEXT NOT NULL,
            included INTEGER NOT NULL,
            omission_reason TEXT,
            PRIMARY KEY(request_id, position),
            FOREIGN KEY(request_id) REFERENCES context_manifests(request_id)
                ON DELETE CASCADE
        )
        """
    )


def _migration_003_memory_lifecycle(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE memories ADD COLUMN supersedes_id TEXT
            REFERENCES memories(id) ON DELETE SET NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX memories_status_updated
        ON memories(status, updated_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE memory_events (
            event_id TEXT PRIMARY KEY,
            memory_id TEXT,
            operation TEXT NOT NULL,
            previous_status TEXT,
            new_status TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(id)
                ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX memory_events_memory
        ON memory_events(memory_id, created_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE legacy_migrations (
            name TEXT PRIMARY KEY,
            backup_path TEXT NOT NULL,
            report_json TEXT NOT NULL,
            completed_at TEXT NOT NULL
        )
        """
    )


def _migration_004_continuation_events(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE continuation_events (
            event_id TEXT PRIMARY KEY,
            task_id TEXT,
            operation TEXT NOT NULL,
            previous_status TEXT,
            new_status TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX continuation_events_task
        ON continuation_events(task_id, created_at)
        """
    )


MIGRATIONS: Sequence[Migration] = (
    (1, _migration_001_initial_schema),
    (2, _migration_002_context_manifests),
    (3, _migration_003_memory_lifecycle),
    (4, _migration_004_continuation_events),
)


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def current_schema_version(connection: sqlite3.Connection) -> int:
    _ensure_migration_table(connection)
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])


def apply_migrations(connection: sqlite3.Connection) -> int:
    _ensure_migration_table(connection)
    current = current_schema_version(connection)
    if current > LATEST_SCHEMA_VERSION:
        raise MigrationError(
            "Database schema version {0} is newer than supported version {1}.".format(
                current, LATEST_SCHEMA_VERSION
            )
        )

    for version, migration in MIGRATIONS:
        if version <= current:
            continue

        try:
            connection.execute("BEGIN IMMEDIATE")
            already_applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if already_applied is not None:
                connection.commit()
                current = max(current, version)
                continue

            migration(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (version,),
            )
            connection.commit()
            current = version
        except sqlite3.Error as exc:
            connection.rollback()
            raise MigrationError(
                "Unable to apply database migration {0}: {1}".format(version, exc)
            ) from exc

    return current
