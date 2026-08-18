import os
from pathlib import Path

import pytest

from nage.errors import ConfigurationError
from nage.paths import AppPaths


def test_app_paths_are_derived_from_injected_home(tmp_path: Path) -> None:
    paths = AppPaths.from_home(tmp_path)

    assert paths.root == tmp_path / ".nage"
    assert paths.config_file == paths.root / "config.json"
    assert paths.database_file == paths.root / "nage.sqlite3"
    assert paths.legacy_settings_file == paths.root / "SETT"
    assert paths.legacy_memory_file == paths.root / "MEMO"
    assert paths.legacy_history_file == paths.root / "HIST"


def test_ensure_creates_private_directories(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "data")

    assert paths.ensure() is paths
    assert paths.root.is_dir()
    assert paths.artifacts_dir.is_dir()
    assert paths.backups_dir.is_dir()

    if os.name != "nt":
        assert paths.root.stat().st_mode & 0o777 == 0o700
        assert paths.artifacts_dir.stat().st_mode & 0o777 == 0o700


def test_paths_support_separate_config_data_and_cache_roots(tmp_path: Path) -> None:
    paths = AppPaths(
        root=tmp_path / "data",
        config_root=tmp_path / "config",
        cache_root=tmp_path / "cache",
    ).ensure()

    assert paths.database_file.parent == tmp_path / "data"
    assert paths.config_file.parent == tmp_path / "config"
    assert paths.cache_dir == tmp_path / "cache"
    assert paths.config_dir.is_dir()
    assert paths.cache_dir.is_dir()


def test_ensure_rejects_file_as_data_root(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("content", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not a directory"):
        AppPaths(root).ensure()
