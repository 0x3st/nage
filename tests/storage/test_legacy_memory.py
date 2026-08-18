import json
import shutil
from pathlib import Path

import pytest

from nage.errors import StorageError
from nage.paths import AppPaths
from nage.storage import Database, LegacyMemoryMigrator, MemoryStore


def setup_store(tmp_path: Path):
    paths = AppPaths(tmp_path / "data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return paths, MemoryStore(database)


def copy_fixture(source: Path, paths: AppPaths) -> None:
    for name in ("SETT", "MEMO", "HIST"):
        shutil.copyfile(str(source / name), str(paths.root / name))


def test_legacy_memory_migration_is_backed_up_deduplicated_and_idempotent(
    tmp_path: Path,
    legacy_fixture_dir: Path,
) -> None:
    paths, memories = setup_store(tmp_path)
    copy_fixture(legacy_fixture_dir, paths)
    migrator = LegacyMemoryMigrator(paths, memories)

    report = migrator.migrate()

    assert report.imported == 2
    assert report.active == 2
    assert report.skipped_empty == 1
    assert report.skipped_duplicate == 1
    assert Path(report.backup_path).is_dir()
    assert len(memories.list()) == 2

    backup_count = len(tuple(paths.backups_dir.iterdir()))
    repeated = migrator.migrate()
    assert repeated.already_migrated is True
    assert len(tuple(paths.backups_dir.iterdir())) == backup_count
    assert len(memories.list()) == 2


def test_malformed_legacy_memory_preserves_backup_without_marker(
    tmp_path: Path,
) -> None:
    paths, memories = setup_store(tmp_path)
    paths.legacy_memory_file.write_text("not-json", encoding="utf-8")

    with pytest.raises(StorageError, match="malformed"):
        LegacyMemoryMigrator(paths, memories).migrate()

    assert tuple(paths.backups_dir.iterdir())
    assert memories.legacy_report() is None
    assert memories.list() == ()


def test_legacy_secret_is_preserved_only_in_backup_not_memory(tmp_path: Path) -> None:
    paths, memories = setup_store(tmp_path)
    paths.legacy_memory_file.write_text(
        json.dumps(["api_key = sk-abcdefghijklmnopqrstuvwxyz"]),
        encoding="utf-8",
    )

    report = LegacyMemoryMigrator(paths, memories).migrate()

    assert report.review_candidates == 0
    assert report.skipped_sensitive == 1
    assert report.imported == 0
    assert memories.list(statuses=("active", "candidate")) == ()
    assert Path(report.backup_path, "MEMO").is_file()


def test_legacy_import_transaction_rolls_back_on_interruption(
    tmp_path: Path,
    legacy_fixture_dir: Path,
    monkeypatch,
) -> None:
    paths, memories = setup_store(tmp_path)
    copy_fixture(legacy_fixture_dir, paths)
    original = memories._upsert_connection
    calls = 0

    def fail_after_first(connection, values):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption")
        return original(connection, values)

    monkeypatch.setattr(memories, "_upsert_connection", fail_after_first)

    with pytest.raises(RuntimeError, match="interruption"):
        LegacyMemoryMigrator(paths, memories).migrate()

    assert memories.list() == ()
    assert memories.legacy_report() is None
