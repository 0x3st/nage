from pathlib import Path

import pytest

from nage.errors import ToolError
from nage.tools import ToolContext, ToolRegistry
from tests.tool_helpers import make_tool


def test_registry_snapshot_is_immutable_and_isolated() -> None:
    registry = ToolRegistry()
    registry.register(make_tool("sample.first"))
    snapshot = registry.snapshot()

    registry.register(make_tool("sample.second"))

    assert snapshot.names() == ("sample.first",)
    assert registry.snapshot().names() == ("sample.first", "sample.second")
    with pytest.raises(ToolError, match="not available"):
        snapshot.resolve("sample.second")


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(make_tool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_tool())


def test_tool_arguments_are_strictly_validated(tmp_path: Path) -> None:
    spec = make_tool()

    arguments = spec.validate_arguments({"value": "hello"})
    assert arguments.value == "hello"

    with pytest.raises(ToolError, match="extra_forbidden"):
        spec.validate_arguments({"value": "hello", "command": "rm -rf /"})


def test_preview_failure_is_normalized(tmp_path: Path) -> None:
    spec = make_tool()
    object.__setattr__(
        spec,
        "preview_builder",
        lambda arguments, context: (_ for _ in ()).throw(
            RuntimeError("private detail")
        ),
    )
    arguments = spec.validate_arguments({"value": "hello"})

    with pytest.raises(ToolError, match="Preview generation failed") as caught:
        spec.preview(arguments, ToolContext("request-1", tmp_path))

    assert "private detail" not in str(caught.value)


def test_tool_context_enforces_deadline(tmp_path: Path) -> None:
    context = ToolContext(
        request_id="request-1",
        working_directory=tmp_path,
        deadline_monotonic=0,
    )

    with pytest.raises(ToolError, match="deadline"):
        context.ensure_within_deadline()
