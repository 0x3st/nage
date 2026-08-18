from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nage.errors import MigrationError
from nage.storage import LATEST_SCHEMA_VERSION, Database

EXPECTED_TABLES = {
    "actions",
    "artifacts",
    "context_manifest_entries",
    "context_manifests",
    "continuation_events",
    "continuations",
    "legacy_migrations",
    "memories",
    "memory_events",
    "project_files",
    "requests",
    "schema_migrations",
}


def test_database_initializes_expected_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "nage.sqlite3")

    assert database.initialize() == LATEST_SCHEMA_VERSION
    assert database.schema_version() == LATEST_SCHEMA_VERSION

    with database.connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert EXPECTED_TABLES <= tables


def test_database_initialization_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "nage.sqlite3")

    assert database.initialize() == LATEST_SCHEMA_VERSION
    assert database.initialize() == LATEST_SCHEMA_VERSION

    with database.connection() as connection:
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]

    assert migration_count == LATEST_SCHEMA_VERSION


def test_database_transaction_rolls_back(tmp_path: Path) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()

    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO requests(
                    request_id, started_at, status
                ) VALUES ('request-1', 'now', 'running')
                """
            )
            raise RuntimeError("stop")

    with database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 0


def test_database_rejects_newer_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()

    with database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (999, 'now')"
        )

    with pytest.raises(MigrationError, match="newer than supported"):
        database.initialize()


def test_concurrent_initialization_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "nage.sqlite3"

    def initialize() -> int:
        return Database(path, timeout_seconds=10).initialize()

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(lambda _: initialize(), range(2)))

    assert versions == [LATEST_SCHEMA_VERSION, LATEST_SCHEMA_VERSION]
    assert Database(path).schema_version() == LATEST_SCHEMA_VERSION
