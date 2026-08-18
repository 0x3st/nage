import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nage.config import ConfigStore
from nage.errors import StorageError
from nage.paths import AppPaths
from nage.storage import (
    Database,
    LegacyMigrationCoordinator,
    MemoryStore,
)


def copy_fixture(source: Path, paths: AppPaths) -> None:
    paths.ensure()
    for name in ("SETT", "MEMO", "HIST"):
        shutil.copyfile(str(source / name), str(paths.root / name))


def coordinator(paths: AppPaths):
    database = Database(paths.database_file, timeout_seconds=10)
    database.initialize()
    memories = MemoryStore(database)
    return LegacyMigrationCoordinator(
        paths,
        database,
        ConfigStore(paths),
        memories,
    ), memories


def test_complete_legacy_migration_preserves_history_without_active_context(
    tmp_path: Path,
    legacy_fixture_dir: Path,
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(legacy_fixture_dir, paths)
    migration, memories = coordinator(paths)

    report = migration.migrate()

    assert report.settings_status == "migrated"
    assert report.history_status == "preserved_in_backup_only"
    assert report.history_entries == 4
    assert Path(report.backup_path, "HIST").is_file()
    assert len(memories.list()) == 2
    config = ConfigStore(paths).load()
    assert config.model == "deepseek-chat"
    assert config.endpoint == "https://api.deepseek.com/v1"
    assert config.api_key == "legacy-test-key"
    serialized_report = json.dumps(report.to_mapping())
    assert "legacy-test-key" not in serialized_report

    with migration.database.connection() as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "history" not in table_names
    assert "legacy_history" not in table_names


def test_complete_migration_is_idempotent(
    tmp_path: Path, legacy_fixture_dir: Path
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(legacy_fixture_dir, paths)
    migration, memories = coordinator(paths)

    first = migration.migrate()
    backup_count = len(tuple(paths.backups_dir.iterdir()))
    second = migration.migrate()

    assert first.already_migrated is False
    assert second.already_migrated is True
    assert len(tuple(paths.backups_dir.iterdir())) == backup_count
    assert len(memories.list()) == 2


def test_malformed_settings_stops_before_memory_or_config_changes(
    tmp_path: Path,
    malformed_legacy_fixture_dir: Path,
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(malformed_legacy_fixture_dir, paths)
    migration, memories = coordinator(paths)

    with pytest.raises(StorageError, match="SETT"):
        migration.migrate()

    assert tuple(paths.backups_dir.iterdir())
    assert not paths.config_file.exists()
    assert memories.list(statuses=("active", "candidate")) == ()
    assert migration.report() is None


def test_malformed_history_is_preserved_but_never_activated(
    tmp_path: Path,
    legacy_fixture_dir: Path,
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(legacy_fixture_dir, paths)
    paths.legacy_history_file.write_text("not-json", encoding="utf-8")
    migration, _ = coordinator(paths)

    report = migration.migrate()

    assert report.history_status == "malformed_preserved"
    assert Path(report.backup_path, "HIST").read_text(encoding="utf-8") == "not-json"


def test_malformed_memory_keeps_backup_and_does_not_save_config(
    tmp_path: Path,
    legacy_fixture_dir: Path,
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(legacy_fixture_dir, paths)
    paths.legacy_memory_file.write_text("not-json", encoding="utf-8")
    migration, memories = coordinator(paths)

    with pytest.raises(StorageError, match="MEMO"):
        migration.migrate()

    assert tuple(paths.backups_dir.iterdir())
    assert not paths.config_file.exists()
    assert memories.list() == ()
    assert migration.report() is None


def test_migration_uses_separate_config_root(
    tmp_path: Path, legacy_fixture_dir: Path
) -> None:
    paths = AppPaths(
        root=tmp_path / "data",
        config_root=tmp_path / "config",
        cache_root=tmp_path / "cache",
    )
    copy_fixture(legacy_fixture_dir, paths)
    migration, _ = coordinator(paths)

    migration.migrate()

    assert paths.config_file.is_file()
    assert not (paths.root / "config.json").exists()


def test_migration_rollback_restores_legacy_files(
    tmp_path: Path,
    legacy_fixture_dir: Path,
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(legacy_fixture_dir, paths)
    original = paths.legacy_memory_file.read_bytes()
    migration, _ = coordinator(paths)
    migration.migrate()
    paths.legacy_memory_file.write_text("[]", encoding="utf-8")

    migration.rollback(overwrite=True)

    assert paths.legacy_memory_file.read_bytes() == original


def test_concurrent_migration_does_not_duplicate_memories(
    tmp_path: Path,
    legacy_fixture_dir: Path,
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_fixture(legacy_fixture_dir, paths)

    def migrate():
        item, _ = coordinator(paths)
        return item.migrate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda _: migrate(), range(2)))

    _, memories = coordinator(paths)
    assert all(report is not None for report in reports)
    assert len(memories.list()) == 2
    with Database(paths.database_file).connection() as connection:
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM legacy_migrations WHERE name = 'legacy_v1_complete'"
        ).fetchone()[0]
    assert marker_count == 1
