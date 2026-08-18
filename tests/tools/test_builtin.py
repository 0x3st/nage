from pathlib import Path

import pytest

from nage.storage import Database, MemoryStore
from nage.tools.builtin import BUILTIN_PERMISSIONS, build_builtin_registry


def test_builtin_registry_contains_fixed_typed_tools() -> None:
    snapshot = build_builtin_registry().snapshot()

    assert len(snapshot) == 18
    assert "system.platform" in snapshot.names()
    assert "files.trash" in snapshot.names()
    assert "git.diff" in snapshot.names()
    assert "filesystem.write" in BUILTIN_PERMISSIONS


def test_builtin_registry_adds_memory_tools_only_with_approved_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    memories = MemoryStore(database)

    snapshot = build_builtin_registry(memory_store=memories).snapshot()

    assert len(snapshot) == 26
    assert "memory.inspect" in snapshot.names()
    assert "memory.clear" in snapshot.names()


def test_builtin_registry_can_only_select_known_static_names() -> None:
    snapshot = build_builtin_registry(("system.platform", "git.status")).snapshot()

    assert snapshot.names() == ("git.status", "system.platform")
    with pytest.raises(ValueError, match="Unknown built-in"):
        build_builtin_registry(("generated.live",))
