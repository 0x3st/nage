from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a HOME that cannot touch the developer's real Nage data."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def legacy_fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "legacy" / "v1"


@pytest.fixture
def malformed_legacy_fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "legacy" / "malformed"
