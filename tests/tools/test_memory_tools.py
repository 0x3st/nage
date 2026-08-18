import json
from pathlib import Path

import pytest

from nage.core.policy import PolicyDecisionKind, PolicyEngine
from nage.errors import ToolError
from nage.paths import AppPaths
from nage.storage import ArtifactStore, Database, MemoryStore
from nage.tools import RiskLevel, ToolContext
from nage.tools.memory import build_memory_tools


def setup(tmp_path: Path):
    paths = AppPaths(tmp_path / "data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    memories = MemoryStore(database)
    artifacts = ArtifactStore(database, paths)
    return memories, artifacts


def specs(memories: MemoryStore):
    return {spec.name: spec for spec in build_memory_tools(memories)}


def add(memories: MemoryStore, content: str, **changes):
    values = {
        "content": content,
        "kind": "preference",
        "scope_type": "global",
        "scope_key": "",
        "source_type": "explicit_user_statement",
        "confidence": 1.0,
    }
    values.update(changes)
    return memories.upsert(**values).record


def test_memory_tool_set_contains_management_operations(tmp_path: Path) -> None:
    memories, _ = setup(tmp_path)

    assert set(specs(memories)) == {
        "memory.inspect",
        "memory.search",
        "memory.correct",
        "memory.forget",
        "memory.activate",
        "memory.restore",
        "memory.clear",
        "memory.export",
    }


def test_inspect_redacts_sensitive_candidate_content(tmp_path: Path) -> None:
    memories, _ = setup(tmp_path)
    add(
        memories,
        "api_key = sk-abcdefghijklmnopqrstuvwxyz",
        sensitivity="sensitive",
        status="candidate",
    )
    spec = specs(memories)["memory.inspect"]

    result = spec.execute(
        spec.validate_arguments({"status": "candidate"}),
        ToolContext("request-1", tmp_path),
    )

    serialized = json.dumps(dict(result.data))
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "sensitive memory content hidden" in serialized


def test_correct_requires_confirmation_and_rejects_secret_content(
    tmp_path: Path,
) -> None:
    memories, _ = setup(tmp_path)
    original = add(memories, "Prefer npm.")
    spec = specs(memories)["memory.correct"]
    context = ToolContext("request-1", tmp_path)
    engine = PolicyEngine(("memory.write",))
    safe = spec.validate_arguments(
        {"memory_id": original.id, "content": "Prefer pnpm."}
    )
    secret = spec.validate_arguments(
        {
            "memory_id": original.id,
            "content": "api_key = sk-abcdefghijklmnopqrstuvwxyz",
        }
    )

    assert engine.evaluate(spec, safe, context).kind == PolicyDecisionKind.CONFIRM
    assert engine.evaluate(spec, secret, context).kind == PolicyDecisionKind.DENY
    with pytest.raises(ToolError, match="secret"):
        spec.execute(secret, context)

    result = spec.execute(safe, context)
    assert result.data["memory"]["content"] == "Prefer pnpm."
    assert memories.get(original.id).status == "superseded"


def test_forget_clear_and_restore_are_reversible(tmp_path: Path) -> None:
    memories, _ = setup(tmp_path)
    first = add(memories, "Prefer one.")
    add(memories, "Prefer two.")
    available = specs(memories)
    context = ToolContext("request-1", tmp_path)
    engine = PolicyEngine(("memory.write",))
    forget = available["memory.forget"]
    forget_arguments = forget.validate_arguments({"memory_id": first.id})

    decision = engine.evaluate(forget, forget_arguments, context)
    assert decision.kind == PolicyDecisionKind.CONFIRM
    assert decision.risk == RiskLevel.DESTRUCTIVE
    forget.execute(forget_arguments, context)
    assert memories.get(first.id).status == "forgotten"

    restore = available["memory.restore"]
    restore.execute(restore.validate_arguments({"memory_id": first.id}), context)
    assert memories.get(first.id).status == "active"

    clear = available["memory.clear"]
    clear_arguments = clear.validate_arguments({"scope": "all"})
    assert clear.relevant_state(clear_arguments, context)["count"] == 2
    clear.execute(clear_arguments, context)
    assert memories.list() == ()


def test_reviewed_normal_candidate_can_be_activated(tmp_path: Path) -> None:
    memories, _ = setup(tmp_path)
    candidate = add(
        memories,
        "Prefer concise output.",
        status="candidate",
    )
    spec = specs(memories)["memory.activate"]
    arguments = spec.validate_arguments({"memory_id": candidate.id})
    context = ToolContext("request-1", tmp_path)

    decision = PolicyEngine(("memory.write",)).evaluate(spec, arguments, context)
    assert decision.kind == PolicyDecisionKind.CONFIRM
    spec.execute(arguments, context)
    assert memories.get(candidate.id).status == "active"


def test_sensitive_candidate_cannot_be_activated(tmp_path: Path) -> None:
    memories, _ = setup(tmp_path)
    candidate = add(
        memories,
        "api_key = sk-abcdefghijklmnopqrstuvwxyz",
        sensitivity="sensitive",
        status="candidate",
    )
    spec = specs(memories)["memory.activate"]
    arguments = spec.validate_arguments({"memory_id": candidate.id})
    context = ToolContext("request-1", tmp_path)

    assert (
        PolicyEngine(("memory.write",)).evaluate(spec, arguments, context).kind
        == PolicyDecisionKind.DENY
    )
    with pytest.raises(ToolError, match="normal-sensitivity"):
        spec.execute(arguments, context)


def test_export_uses_artifact_and_never_exports_sensitive_content(
    tmp_path: Path,
) -> None:
    memories, artifacts = setup(tmp_path)
    add(memories, "Prefer pnpm.")
    add(
        memories,
        "api_key = sk-abcdefghijklmnopqrstuvwxyz",
        sensitivity="sensitive",
        status="candidate",
    )
    spec = specs(memories)["memory.export"]

    result = spec.execute(
        spec.validate_arguments({"include_inactive": True}),
        ToolContext("request-1", tmp_path, artifact_writer=artifacts),
    )

    assert result.artifact_ids
    exported = artifacts.read_bytes(result.artifact_ids[0], 100000).decode()
    assert "Prefer pnpm." in exported
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in exported
    assert "sensitive memory content hidden" in exported
