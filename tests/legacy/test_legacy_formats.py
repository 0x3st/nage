import json
from pathlib import Path


def test_legacy_fixture_matches_v1_shapes(legacy_fixture_dir: Path) -> None:
    settings = json.loads((legacy_fixture_dir / "SETT").read_text(encoding="utf-8"))
    memories = json.loads((legacy_fixture_dir / "MEMO").read_text(encoding="utf-8"))
    history = json.loads((legacy_fixture_dir / "HIST").read_text(encoding="utf-8"))

    assert set(settings) == {"model", "key", "endpoint"}
    assert isinstance(memories, list)
    assert all(isinstance(item, str) for item in memories)
    assert isinstance(history, list)
    assert all(isinstance(item, str) for item in history)


def test_legacy_fixture_preserves_migration_edge_cases(
    legacy_fixture_dir: Path,
) -> None:
    memories = json.loads((legacy_fixture_dir / "MEMO").read_text(encoding="utf-8"))

    assert "" in memories
    assert len(memories) != len(set(memories))
