from pathlib import Path

from nage.paths import AppPaths
from nage.storage import ArtifactStore, Database
from nage.tools import ToolContext
from nage.tools.output import bound_json_items, bound_text


def artifact_store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths(tmp_path / "data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return ArtifactStore(database, paths)


def test_large_text_is_clipped_and_spilled_to_artifact(tmp_path: Path) -> None:
    store = artifact_store(tmp_path)
    context = ToolContext("request-1", tmp_path, artifact_writer=store)
    original = "output line\n" * 1000

    bounded = bound_text(context, original, inline_bytes=128)

    assert len(bounded.text.encode("utf-8")) <= 128
    assert bounded.truncated is True
    assert len(bounded.artifact_ids) == 1
    assert store.read_bytes(bounded.artifact_ids[0], 100000).decode() == original


def test_large_text_without_artifact_store_is_honest(tmp_path: Path) -> None:
    bounded = bound_text(
        ToolContext("request-1", tmp_path),
        "x" * 1000,
        inline_bytes=32,
    )

    assert bounded.artifact_ids == ()
    assert "not retained" in bounded.warnings[0]


def test_json_item_bound_uses_serialized_byte_size() -> None:
    values = [{"path": "x" * 100}, {"path": "y" * 100}]

    selected, truncated = bound_json_items(values, item_limit=2, inline_bytes=120)

    assert len(selected) == 1
    assert truncated is True
