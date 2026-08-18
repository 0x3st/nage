import json
import os
from pathlib import Path

import pytest

from nage.config import AppConfig, ConfigStore
from nage.errors import ConfigurationError
from nage.paths import AppPaths


def test_config_store_returns_defaults_when_missing(tmp_path: Path) -> None:
    config = ConfigStore(AppPaths(tmp_path / "data")).load()

    assert config.model == "deepseek-chat"
    assert config.endpoint == "https://api.deepseek.com/v1"
    assert config.api_key == ""
    assert config.memory_enabled is True


def test_config_store_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "data")
    store = ConfigStore(paths)
    expected = AppConfig(
        model="test-model",
        endpoint="https://example.test/v1",
        api_key="test-secret",
        memory_enabled=False,
    )

    store.save(expected)

    assert store.load() == expected
    assert list(paths.root.glob(".config-*.tmp")) == []
    if os.name != "nt":
        assert paths.config_file.stat().st_mode & 0o777 == 0o600


def test_config_store_uses_separate_config_root(tmp_path: Path) -> None:
    paths = AppPaths(
        root=tmp_path / "data",
        config_root=tmp_path / "config",
        cache_root=tmp_path / "cache",
    )
    store = ConfigStore(paths)

    store.save(AppConfig(api_key="test-key"))

    assert paths.config_file.is_file()
    assert not (paths.root / "config.json").exists()
    assert list(paths.config_dir.glob(".config-*.tmp")) == []


def test_config_redacts_api_key() -> None:
    config = AppConfig(api_key="secret")

    assert config.redacted_mapping()["api_key"] == "***"
    assert "secret" not in repr(config)


@pytest.mark.parametrize(
    "endpoint",
    ["", "api.example.test/v1", "ftp://example.test", "https://u:p@example.test"],
)
def test_config_rejects_invalid_endpoint(endpoint: str) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(endpoint=endpoint)


def test_config_store_rejects_malformed_json(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "data").ensure()
    paths.config_file.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Unable to read"):
        ConfigStore(paths).load()


def test_config_store_rejects_non_object_root(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "data").ensure()
    paths.config_file.write_text(json.dumps(["wrong"]), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="JSON object"):
        ConfigStore(paths).load()
