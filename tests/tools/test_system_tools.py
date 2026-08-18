from pathlib import Path
from types import SimpleNamespace

from nage.core.policy import PolicyDecisionKind, PolicyEngine
from nage.paths import AppPaths
from nage.storage import ArtifactStore, Database
from nage.tools import ToolContext
from nage.tools.system import build_system_tools


def specs():
    return {spec.name: spec for spec in build_system_tools()}


def artifact_store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths(tmp_path / "data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return ArtifactStore(database, paths)


def test_system_tool_permissions_are_host_owned(tmp_path: Path) -> None:
    spec = specs()["system.processes"]
    arguments = spec.validate_arguments({})
    context = ToolContext("request-1", tmp_path)

    assert (
        PolicyEngine().evaluate(spec, arguments, context).kind
        == PolicyDecisionKind.DENY
    )
    assert (
        PolicyEngine(("process.read",)).evaluate(spec, arguments, context).kind
        == PolicyDecisionKind.ALLOW
    )


def test_platform_and_disk_tools_return_factual_values(tmp_path: Path) -> None:
    available = specs()
    context = ToolContext("request-1", tmp_path)

    platform_result = available["system.platform"].execute(
        available["system.platform"].validate_arguments({}), context
    )
    disk_result = available["system.disk_usage"].execute(
        available["system.disk_usage"].validate_arguments({"path": "."}),
        context,
    )

    assert platform_result.data["system"]
    assert platform_result.data["python_version"]
    assert disk_result.data["total_bytes"] >= disk_result.data["free_bytes"]
    assert disk_result.data["path"] == "."


def test_process_tool_sorts_by_memory_and_spills_extra_results(
    tmp_path: Path, monkeypatch
) -> None:
    class Process:
        def __init__(self, pid, name, rss):
            self.info = {
                "pid": pid,
                "name": name,
                "status": "running",
                "memory_info": SimpleNamespace(rss=rss),
                "create_time": 1000.0 + pid,
            }

    monkeypatch.setattr(
        "nage.tools.system.psutil.process_iter",
        lambda attrs: [
            Process(1, "small", 10),
            Process(2, "large", 100),
            Process(3, "medium", 50),
        ],
    )
    store = artifact_store(tmp_path)
    context = ToolContext("request-1", tmp_path, artifact_writer=store)
    spec = specs()["system.processes"]

    result = spec.execute(spec.validate_arguments({"limit": 2}), context)

    assert [item["pid"] for item in result.data["processes"]] == [2, 3]
    assert result.data["truncated"] is True
    assert result.artifact_ids
    assert "cmdline" not in str(result.data)
