from pathlib import Path

import pytest

from nage.core.policy import PolicyDecisionKind, PolicyEngine
from nage.errors import PolicyDeniedError, ToolError
from nage.paths import AppPaths
from nage.storage import ArtifactStore, Database
from nage.tools import RiskLevel, ToolContext
from nage.tools.files import build_file_tools


def specs():
    return {spec.name: spec for spec in build_file_tools()}


def artifact_store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths(tmp_path / ".nage-test-data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return ArtifactStore(database, paths)


def test_file_tool_names_are_unique_and_complete() -> None:
    available = specs()

    assert len(available) == 11
    assert {
        "files.list",
        "files.metadata",
        "files.find_name",
        "files.find_large",
        "files.search_text",
        "files.read_range",
        "files.create_directory",
        "files.create_text",
        "files.copy",
        "files.move",
        "files.trash",
    } == set(available)


def test_list_hides_ignored_sensitive_and_symlink_entries(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(tmp_path / "visible.txt")
    except (NotImplementedError, OSError):
        link = None
    spec = specs()["files.list"]

    result = spec.execute(
        spec.validate_arguments({"path": ".", "include_hidden": True}),
        ToolContext("request-1", tmp_path),
    )

    names = {entry["name"] for entry in result.data["entries"]}
    assert "visible.txt" in names
    assert "ignored.txt" not in names
    assert ".env" not in names
    if link is not None:
        assert "link.txt" not in names


def test_list_spills_results_above_requested_limit(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / "file-{0}.txt".format(index)).write_text(
            "content", encoding="utf-8"
        )
    store = artifact_store(tmp_path)
    context = ToolContext("request-1", tmp_path, artifact_writer=store)
    spec = specs()["files.list"]

    result = spec.execute(spec.validate_arguments({"limit": 1}), context)

    assert len(result.data["entries"]) == 1
    assert result.data["truncated"] is True
    assert result.artifact_ids


def test_find_name_spills_additional_results(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / "match-{0}.txt".format(index)).write_text(
            "content", encoding="utf-8"
        )
    store = artifact_store(tmp_path)
    spec = specs()["files.find_name"]

    result = spec.execute(
        spec.validate_arguments({"query": "match", "limit": 1}),
        ToolContext("request-1", tmp_path, artifact_writer=store),
    )

    assert len(result.data["matches"]) == 1
    assert result.data["truncated"] is True
    assert result.artifact_ids


def test_metadata_and_search_refuse_likely_secret_path(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    available = specs()
    context = ToolContext("request-1", tmp_path)

    metadata = available["files.metadata"]
    with pytest.raises(PolicyDeniedError, match="secret"):
        metadata.execute(metadata.validate_arguments({"path": ".env"}), context)

    read_range = available["files.read_range"]
    with pytest.raises(PolicyDeniedError, match="secret"):
        read_range.execute(
            read_range.validate_arguments(
                {"path": ".env", "start_line": 1, "end_line": 1}
            ),
            context,
        )


def test_find_large_returns_descending_sizes(tmp_path: Path) -> None:
    (tmp_path / "small.bin").write_bytes(b"x" * 5)
    (tmp_path / "large.bin").write_bytes(b"x" * 20)
    spec = specs()["files.find_large"]

    result = spec.execute(
        spec.validate_arguments({"minimum_size_bytes": 1}),
        ToolContext("request-1", tmp_path),
    )

    assert [item["path"] for item in result.data["files"][:2]] == [
        "large.bin",
        "small.bin",
    ]


def test_create_text_requires_permission_and_confirmation(tmp_path: Path) -> None:
    spec = specs()["files.create_text"]
    arguments = spec.validate_arguments({"path": "new.txt", "content": "hello"})
    context = ToolContext("request-1", tmp_path)

    assert (
        PolicyEngine().evaluate(spec, arguments, context).kind
        == PolicyDecisionKind.DENY
    )
    decision = PolicyEngine(("filesystem.write",)).evaluate(spec, arguments, context)
    assert decision.kind == PolicyDecisionKind.CONFIRM
    assert decision.risk == RiskLevel.LOCAL_WRITE


def test_create_preview_lists_all_missing_parent_paths(tmp_path: Path) -> None:
    spec = specs()["files.create_text"]
    arguments = spec.validate_arguments(
        {
            "path": "one/two/new.txt",
            "content": "hello",
            "create_parents": True,
        }
    )

    preview = spec.preview(arguments, ToolContext("request-1", tmp_path))

    assert preview.affected_paths == (
        "one",
        "one/two",
        "one/two/new.txt",
    )


def test_create_text_is_exclusive_and_bounded(tmp_path: Path) -> None:
    spec = specs()["files.create_text"]
    arguments = spec.validate_arguments({"path": "new.txt", "content": "hello"})
    context = ToolContext("request-1", tmp_path)

    result = spec.execute(arguments, context)

    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"
    assert result.data["sha256"]
    with pytest.raises(ToolError, match="already exists"):
        spec.execute(arguments, context)
    with pytest.raises(ToolError, match="65536 bytes"):
        spec.validate_arguments({"path": "large.txt", "content": "数" * 30000})


def test_copy_and_move_regular_files_without_shell(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("copy me", encoding="utf-8")
    available = specs()
    context = ToolContext("request-1", tmp_path)
    copy_spec = available["files.copy"]
    copy_arguments = copy_spec.validate_arguments(
        {"source": "source.txt", "destination": "copy.txt"}
    )

    copy_result = copy_spec.execute(copy_arguments, context)

    assert (tmp_path / "copy.txt").read_text(encoding="utf-8") == "copy me"
    assert copy_result.data["size_bytes"] == 7

    move_spec = available["files.move"]
    move_arguments = move_spec.validate_arguments(
        {"source": "copy.txt", "destination": "renamed.txt"}
    )
    move_spec.execute(move_arguments, context)

    assert not (tmp_path / "copy.txt").exists()
    assert (tmp_path / "renamed.txt").read_text(encoding="utf-8") == "copy me"


def test_overwrite_raises_risk_and_unapproved_existing_target_is_denied(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    (tmp_path / "destination.txt").write_text("destination", encoding="utf-8")
    spec = specs()["files.copy"]
    context = ToolContext("request-1", tmp_path)
    engine = PolicyEngine(("filesystem.read", "filesystem.write"))

    denied = engine.evaluate(
        spec,
        spec.validate_arguments(
            {"source": "source.txt", "destination": "destination.txt"}
        ),
        context,
    )
    destructive = engine.evaluate(
        spec,
        spec.validate_arguments(
            {
                "source": "source.txt",
                "destination": "destination.txt",
                "overwrite": True,
            }
        ),
        context,
    )

    assert denied.kind == PolicyDecisionKind.DENY
    assert destructive.risk == RiskLevel.DESTRUCTIVE
    assert destructive.kind == PolicyDecisionKind.CONFIRM


def test_mutation_risk_rejects_workspace_escape(tmp_path: Path) -> None:
    spec = specs()["files.create_directory"]
    arguments = spec.validate_arguments({"path": "../outside"})

    decision = PolicyEngine(("filesystem.write",)).evaluate(
        spec, arguments, ToolContext("request-1", tmp_path)
    )

    assert decision.kind == PolicyDecisionKind.DENY


def test_file_mutations_cannot_modify_nage_or_git_internal_state(
    tmp_path: Path,
) -> None:
    (tmp_path / ".nage").mkdir()
    (tmp_path / ".nage" / "config.json").write_text("secret", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    available = specs()
    context = ToolContext("request-1", tmp_path)
    policy = PolicyEngine(("filesystem.read", "filesystem.write"))

    create = available["files.create_text"]
    create_arguments = create.validate_arguments(
        {"path": ".nage/new.txt", "content": "blocked"}
    )
    copy = available["files.copy"]
    copy_arguments = copy.validate_arguments(
        {
            "source": ".nage/config.json",
            "destination": "copied-config.json",
        }
    )

    assert (
        policy.evaluate(create, create_arguments, context).kind
        == PolicyDecisionKind.DENY
    )
    assert (
        policy.evaluate(copy, copy_arguments, context).kind == PolicyDecisionKind.DENY
    )
    with pytest.raises(PolicyDeniedError, match="internal state"):
        create.execute(create_arguments, context)


def test_file_mutations_honor_injected_protected_paths(tmp_path: Path) -> None:
    protected = tmp_path / "custom-state"
    protected.mkdir()
    spec = specs()["files.create_text"]
    arguments = spec.validate_arguments(
        {"path": "custom-state/config.json", "content": "blocked"}
    )
    context = ToolContext(
        "request-1",
        tmp_path,
        protected_paths=(protected,),
    )

    decision = PolicyEngine(("filesystem.write",)).evaluate(spec, arguments, context)

    assert decision.kind == PolicyDecisionKind.DENY
    with pytest.raises(PolicyDeniedError, match="protected"):
        spec.execute(arguments, context)


def test_trash_uses_recoverable_backend(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "old.txt"
    target.write_text("old", encoding="utf-8")
    calls = []

    def fake_trash(path):
        calls.append(Path(path))
        Path(path).unlink()

    monkeypatch.setattr("nage.tools.files.send2trash", fake_trash)
    spec = specs()["files.trash"]
    arguments = spec.validate_arguments({"paths": ["old.txt"]})

    result = spec.execute(arguments, ToolContext("request-1", tmp_path))

    assert calls == [target.resolve()]
    assert not target.exists()
    assert "system trash" in result.summary
