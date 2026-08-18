from pathlib import Path

import pytest

from nage.core.policy import PolicyDecisionKind, PolicyEngine
from nage.errors import PolicyDeniedError, ToolError
from nage.paths import AppPaths
from nage.storage import ArtifactStore, Database
from nage.tools import ToolContext
from nage.tools.context import build_context_tools


def specs():
    return {spec.name: spec for spec in build_context_tools()}


def artifact_store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths(tmp_path / ".artifacts").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return ArtifactStore(database, paths)


def test_context_tools_are_read_only_and_require_filesystem_permission(
    tmp_path: Path,
) -> None:
    spec = specs()["files.search_text"]
    arguments = spec.validate_arguments({"query": "needle"})
    context = ToolContext("request-1", tmp_path)

    denied = PolicyEngine().evaluate(spec, arguments, context)
    allowed = PolicyEngine(("filesystem.read",)).evaluate(spec, arguments, context)

    assert denied.kind == PolicyDecisionKind.DENY
    assert allowed.kind == PolicyDecisionKind.ALLOW


def test_search_tool_returns_bounded_structured_matches(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text(
        "first\nneedle value\nlast\n", encoding="utf-8"
    )
    spec = specs()["files.search_text"]
    arguments = spec.validate_arguments({"query": "needle", "path": ".", "limit": 5})

    result = spec.execute(arguments, ToolContext("request-1", tmp_path))

    assert result.summary == "Found 1 text match(es); returned 1."
    assert result.data["matches"][0]["path"] == "sample.txt"
    assert result.data["matches"][0]["line_number"] == 2
    assert result.data["truncated"] is False


def test_search_tool_reports_result_limit(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("needle one\nneedle two\n", encoding="utf-8")
    spec = specs()["files.search_text"]
    arguments = spec.validate_arguments({"query": "needle", "limit": 1})

    result = spec.execute(arguments, ToolContext("request-1", tmp_path))

    assert result.data["truncated"] is True
    assert result.warnings


def test_read_range_tool_returns_progressive_excerpt(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("first\nsecond\nthird\n", encoding="utf-8")
    spec = specs()["files.read_range"]
    arguments = spec.validate_arguments(
        {"path": "sample.txt", "start_line": 2, "end_line": 3}
    )

    result = spec.execute(arguments, ToolContext("request-1", tmp_path))

    assert result.data["content"] == "second\nthird"
    assert result.data["start_line"] == 2
    assert len(result.data["sha256"]) == 64


def test_read_range_spills_large_excerpt_to_artifact(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 20000, encoding="utf-8")
    store = artifact_store(tmp_path)
    spec = specs()["files.read_range"]
    arguments = spec.validate_arguments(
        {"path": "large.txt", "start_line": 1, "end_line": 1}
    )

    result = spec.execute(
        arguments,
        ToolContext("request-1", tmp_path, artifact_writer=store),
    )

    assert result.data["truncated"] is True
    assert result.artifact_ids
    assert len(store.read_bytes(result.artifact_ids[0], 30000)) == 20000


def test_read_range_schema_rejects_reversed_or_large_range(tmp_path: Path) -> None:
    spec = specs()["files.read_range"]

    with pytest.raises(ToolError, match="end_line"):
        spec.validate_arguments({"path": "sample.txt", "start_line": 3, "end_line": 2})
    with pytest.raises(ToolError, match="200"):
        spec.validate_arguments(
            {"path": "sample.txt", "start_line": 1, "end_line": 201}
        )


def test_read_range_tool_refuses_likely_secret(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    spec = specs()["files.read_range"]
    arguments = spec.validate_arguments(
        {"path": ".env", "start_line": 1, "end_line": 1}
    )

    with pytest.raises(PolicyDeniedError, match="secret"):
        spec.execute(arguments, ToolContext("request-1", tmp_path))
