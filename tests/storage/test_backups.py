import json
import shutil
from pathlib import Path

import pytest

import nage.storage.backups as backups_module
from nage.errors import StorageError
from nage.paths import AppPaths
from nage.storage.backups import LegacyBackupManager


def copy_legacy_fixtures(source: Path, paths: AppPaths) -> None:
    paths.ensure()
    for name in ("SETT", "MEMO", "HIST"):
        shutil.copyfile(str(source / name), str(paths.root / name))


def test_backup_returns_none_without_legacy_files(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "data")

    assert LegacyBackupManager(paths).create() is None


def test_backup_copies_and_verifies_legacy_files(
    tmp_path: Path, legacy_fixture_dir: Path
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_legacy_fixtures(legacy_fixture_dir, paths)
    manager = LegacyBackupManager(paths)

    backup = manager.create()

    assert backup is not None
    assert backup.files == ("HIST", "MEMO", "SETT")
    assert manager.verify(backup.path) == backup
    manifest = json.loads((backup.path / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {"SETT", "MEMO", "HIST"}


def test_backup_detects_tampering(tmp_path: Path, legacy_fixture_dir: Path) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_legacy_fixtures(legacy_fixture_dir, paths)
    manager = LegacyBackupManager(paths)
    backup = manager.create()
    assert backup is not None
    (backup.path / "MEMO").write_text("changed", encoding="utf-8")

    with pytest.raises(StorageError, match="verification failed"):
        manager.verify(backup.path)


def test_backup_can_restore_legacy_files(
    tmp_path: Path, legacy_fixture_dir: Path
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_legacy_fixtures(legacy_fixture_dir, paths)
    manager = LegacyBackupManager(paths)
    backup = manager.create()
    assert backup is not None
    original_memory = (paths.root / "MEMO").read_bytes()
    (paths.root / "MEMO").write_text("[]\n", encoding="utf-8")

    manager.restore(backup.path, overwrite=True)

    assert (paths.root / "MEMO").read_bytes() == original_memory


def test_restore_refuses_to_overwrite_by_default(
    tmp_path: Path, legacy_fixture_dir: Path
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_legacy_fixtures(legacy_fixture_dir, paths)
    manager = LegacyBackupManager(paths)
    backup = manager.create()
    assert backup is not None

    with pytest.raises(StorageError, match="already exists"):
        manager.restore(backup.path)


def test_failed_restore_rolls_back_installed_files(
    tmp_path: Path, legacy_fixture_dir: Path, monkeypatch
) -> None:
    paths = AppPaths(tmp_path / "data")
    copy_legacy_fixtures(legacy_fixture_dir, paths)
    manager = LegacyBackupManager(paths)
    backup = manager.create()
    assert backup is not None
    originals = {
        name: (paths.root / name).read_bytes() for name in ("SETT", "MEMO", "HIST")
    }

    real_replace = backups_module.os.replace
    failure_injected = False

    def fail_once_during_install(source: str, destination: str) -> None:
        nonlocal failure_injected
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failure_injected
            and source_path.name.startswith(".restore-")
            and destination_path.name == "MEMO"
        ):
            failure_injected = True
            raise OSError("simulated install failure")
        real_replace(source, destination)

    monkeypatch.setattr(backups_module.os, "replace", fail_once_during_install)

    with pytest.raises(StorageError, match="simulated install failure"):
        manager.restore(backup.path, overwrite=True)

    assert {
        name: (paths.root / name).read_bytes() for name in ("SETT", "MEMO", "HIST")
    } == originals
