import json
from pathlib import Path

from nage.core import (
    ConfirmationService,
    OrchestrationStatus,
    Orchestrator,
    PolicyEngine,
)
from nage.protocol import (
    AssistantDecision,
    DecisionStatus,
    ToolCall,
    ToolResultStatus,
)
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)
from nage.storage import ActionStore, Database, RequestStore
from nage.tools import ToolContext, ToolRegistry
from nage.tools.files import build_file_tools
from nage.tools.system import build_system_tools


def result(decision: AssistantDecision) -> ProviderResult:
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
            tool_calls=tuple(decision.tool_calls),
        ),
    )


def request() -> ProviderRequest:
    return ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "perform the request"),),
    )


def test_file_state_change_after_approval_stops_mutation(tmp_path: Path) -> None:
    spec = {item.name: item for item in build_file_tools()}["files.create_text"]
    registry = ToolRegistry()
    registry.register(spec)
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.TOOL_CALL,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="files.create_text",
                            arguments={"path": "target.txt", "content": "planned"},
                        )
                    ],
                )
            )
        ]
    )

    def race_after_preview(plan):
        (tmp_path / "target.txt").write_text("racer", encoding="utf-8")
        return True

    runner = Orchestrator(
        provider=provider,
        registry=registry.snapshot(),
        policy=PolicyEngine(("filesystem.write",)),
        confirmation=ConfirmationService(race_after_preview),
    )

    orchestration = runner.run(request(), ToolContext("request-1", tmp_path))

    assert orchestration.status == OrchestrationStatus.POLICY_DENIED
    assert orchestration.tool_results[0].status == ToolResultStatus.FAILURE
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "racer"


def test_confirmed_file_creation_executes_and_is_audited(tmp_path: Path) -> None:
    spec = {item.name: item for item in build_file_tools()}["files.create_text"]
    registry = ToolRegistry()
    registry.register(spec)
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.TOOL_CALL,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="files.create_text",
                            arguments={"path": "created.txt", "content": "created"},
                        )
                    ],
                )
            ),
            result(
                AssistantDecision(
                    status=DecisionStatus.FINAL,
                    message="Created created.txt.",
                )
            ),
        ]
    )
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    requests = RequestStore(database)
    actions = ActionStore(database)
    plans = []
    runner = Orchestrator(
        provider=provider,
        registry=registry.snapshot(),
        policy=PolicyEngine(("filesystem.write",)),
        confirmation=ConfirmationService(lambda plan: plans.append(plan) or True),
        request_store=requests,
        action_store=actions,
    )

    orchestration = runner.run(request(), ToolContext("request-1", tmp_path))

    assert orchestration.status == OrchestrationStatus.COMPLETED
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created"
    assert len(plans) == 1
    with database.connection() as connection:
        action = connection.execute("SELECT * FROM actions").fetchone()
    assert action["execution_status"] == "success"
    assert action["confirmation_status"] == "approved"
    audited_arguments = json.loads(action["arguments_redacted_json"])
    assert audited_arguments["content"] == "***"


def test_generated_source_file_does_not_change_live_registry(tmp_path: Path) -> None:
    specs = {item.name: item for item in build_file_tools()}
    registry = ToolRegistry()
    registry.register(specs["files.create_text"])
    snapshot = registry.snapshot()
    arguments = specs["files.create_text"].validate_arguments(
        {
            "path": "generated_plugin.py",
            "content": "def register():\n    return 'generated.live'\n",
        }
    )

    specs["files.create_text"].execute(arguments, ToolContext("request-1", tmp_path))

    assert snapshot.names() == ("files.create_text",)
    assert (tmp_path / "generated_plugin.py").is_file()


def test_registered_read_tools_compose_with_per_step_validation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("sample", encoding="utf-8")
    selected = []
    for spec in build_system_tools() + build_file_tools():
        if spec.name in {"system.platform", "files.metadata"}:
            selected.append(spec)
    registry = ToolRegistry()
    for spec in selected:
        registry.register(spec)
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.TOOL_CALL,
                    tool_calls=[
                        ToolCall(id="call_1", name="system.platform", arguments={}),
                        ToolCall(
                            id="call_2",
                            name="files.metadata",
                            arguments={"path": "sample.txt"},
                        ),
                    ],
                )
            ),
            result(
                AssistantDecision(
                    status=DecisionStatus.FINAL,
                    message="Inspected platform and sample.txt.",
                )
            ),
        ]
    )
    runner = Orchestrator(
        provider=provider,
        registry=registry.snapshot(),
        policy=PolicyEngine(("system.read", "filesystem.read")),
        confirmation=ConfirmationService(lambda plan: False),
    )

    orchestration = runner.run(request(), ToolContext("request-1", tmp_path))

    assert orchestration.status == OrchestrationStatus.COMPLETED
    assert [item.status for item in orchestration.tool_results] == [
        ToolResultStatus.SUCCESS,
        ToolResultStatus.SUCCESS,
    ]
    assert len(provider.requests) == 2
