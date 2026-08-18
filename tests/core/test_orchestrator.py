from pathlib import Path

from nage.core.confirmation import ConfirmationService
from nage.core.context import (
    ContextBuilder,
    ContextSegment,
    ContextSource,
    RequestEnvelope,
    TrustLevel,
)
from nage.core.orchestrator import (
    OrchestrationStatus,
    Orchestrator,
    OrchestratorLimits,
)
from nage.core.policy import PolicyEngine
from nage.protocol import (
    AssistantDecision,
    DecisionStatus,
    ToolCall,
    ToolResultStatus,
    Usage,
)
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)
from nage.storage import (
    ActionStore,
    ContextManifestStore,
    Database,
    RequestStore,
)
from nage.tools import RiskLevel, ToolContext, ToolOutcome, ToolRegistry
from tests.tool_helpers import make_tool


def provider_result(decision: AssistantDecision) -> ProviderResult:
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            role=MessageRole.ASSISTANT,
            content=decision.model_dump_json(),
            tool_calls=tuple(decision.tool_calls),
        ),
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def tool_decision(*calls: ToolCall) -> ProviderResult:
    return provider_result(
        AssistantDecision(
            status=DecisionStatus.TOOL_CALL,
            tool_calls=list(calls),
        )
    )


def final_decision(message: str = "done") -> ProviderResult:
    return provider_result(
        AssistantDecision(status=DecisionStatus.FINAL, message=message)
    )


def request() -> ProviderRequest:
    return ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "handle hello"),),
    )


def stores(tmp_path: Path):
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    return RequestStore(database), ActionStore(database)


def orchestrator(
    tmp_path: Path,
    provider: FakeProvider,
    registry: ToolRegistry,
    confirmation,
    limits: OrchestratorLimits = None,
) -> Orchestrator:
    request_store, action_store = stores(tmp_path)
    return Orchestrator(
        provider=provider,
        registry=registry.snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(confirmation),
        request_store=request_store,
        action_store=action_store,
        limits=limits,
    )


def test_orchestrator_persists_context_manifest_without_content(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    request_store = RequestStore(database)
    manifest_store = ContextManifestStore(database)
    envelope = RequestEnvelope.create(
        "answer this",
        tmp_path,
        request_id="request-1",
    )
    built = ContextBuilder().build(
        envelope,
        [
            ContextSegment(
                source_type=ContextSource.FILE,
                source_reference="sample.txt",
                trust_level=TrustLevel.UNTRUSTED_DATA,
                priority=50,
                content="private context body",
                reason_selected="Test context.",
            )
        ],
    )
    provider = FakeProvider([final_decision("finished")])
    runner = Orchestrator(
        provider=provider,
        registry=ToolRegistry().snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(lambda plan: False),
        request_store=request_store,
        context_manifest_store=manifest_store,
    )

    result = runner.run(
        ProviderRequest(model="test-model", messages=built.messages),
        ToolContext("request-1", tmp_path),
        context_manifest=built.manifest,
    )

    stored = manifest_store.get("request-1")
    assert result.status == OrchestrationStatus.COMPLETED
    assert stored is not None
    assert stored.entries[0].content_hash
    assert not hasattr(stored.entries[0], "content")


def test_read_only_tool_runs_without_confirmation(tmp_path: Path) -> None:
    executions = []

    def execute(arguments, context):
        executions.append(arguments.value)
        return ToolOutcome("read complete", {"value": arguments.value})

    registry = ToolRegistry()
    registry.register(make_tool(executor=execute))
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.read",
                    arguments={"value": "hello"},
                )
            ),
            final_decision("finished"),
        ]
    )
    confirmations = []
    runner = orchestrator(
        tmp_path,
        provider,
        registry,
        lambda plan: confirmations.append(plan) or True,
    )

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.COMPLETED
    assert executions == ["hello"]
    assert confirmations == []
    assert result.tool_results[0].status == ToolResultStatus.SUCCESS
    assert result.usage.total_tokens == 30
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-1].role == MessageRole.TOOL


def test_mutating_tool_requires_confirmation(tmp_path: Path) -> None:
    executions = []

    def execute(arguments, context):
        executions.append(arguments.value)
        return ToolOutcome("write complete")

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="sample.write",
            risk=RiskLevel.LOCAL_WRITE,
            executor=execute,
        )
    )
    call = ToolCall(
        id="call_1",
        name="sample.write",
        arguments={"value": "hello"},
    )
    provider = FakeProvider([tool_decision(call), final_decision()])
    plans = []
    runner = orchestrator(
        tmp_path,
        provider,
        registry,
        lambda plan: plans.append(plan) or True,
    )

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.COMPLETED
    assert executions == ["hello"]
    assert len(plans) == 1
    assert plans[0].tool_name == "sample.write"


def test_declined_mutation_stops_without_execution(tmp_path: Path) -> None:
    executions = []
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="sample.write",
            risk=RiskLevel.LOCAL_WRITE,
            executor=lambda arguments, context: executions.append(True),
        )
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.write",
                    arguments={"value": "hello"},
                )
            )
        ]
    )
    runner = orchestrator(tmp_path, provider, registry, lambda plan: False)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.CANCELLED
    assert executions == []
    assert result.tool_results[0].status == ToolResultStatus.CANCELLED
    assert len(provider.requests) == 1


def test_confirmation_error_is_treated_as_cancellation(tmp_path: Path) -> None:
    executions = []
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="sample.write",
            risk=RiskLevel.LOCAL_WRITE,
            executor=lambda arguments, context: executions.append(True),
        )
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.write",
                    arguments={"value": "hello"},
                )
            )
        ]
    )

    def cancelled_confirmation(plan):
        raise EOFError

    runner = orchestrator(tmp_path, provider, registry, cancelled_confirmation)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.CANCELLED
    assert executions == []
    assert result.tool_results[0].status == ToolResultStatus.CANCELLED


def test_unknown_generated_tool_is_rejected(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(make_tool())
    snapshot_runner = orchestrator(
        tmp_path,
        FakeProvider(
            [
                tool_decision(
                    ToolCall(
                        id="call_1",
                        name="generated.install",
                        arguments={"value": "payload"},
                    )
                )
            ]
        ),
        registry,
        lambda plan: True,
    )
    registry.register(make_tool("generated.install"))

    result = snapshot_runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.POLICY_DENIED
    assert result.tool_results[0].status == ToolResultStatus.DENIED
    assert "not available" in result.tool_results[0].summary


def test_dry_run_does_not_execute_or_confirm(tmp_path: Path) -> None:
    executions = []
    confirmations = []
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="sample.write",
            risk=RiskLevel.LOCAL_WRITE,
            executor=lambda arguments, context: executions.append(True),
        )
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.write",
                    arguments={"value": "hello"},
                )
            ),
            final_decision(),
        ]
    )
    runner = orchestrator(
        tmp_path,
        provider,
        registry,
        lambda plan: confirmations.append(plan) or True,
    )

    result = runner.run(request(), ToolContext("request-1", tmp_path, dry_run=True))

    assert result.status == OrchestrationStatus.COMPLETED
    assert executions == []
    assert confirmations == []
    assert result.tool_results[0].data["dry_run"] is True


def test_state_change_after_confirmation_stops_execution(tmp_path: Path) -> None:
    versions = iter([{"version": 1}, {"version": 2}])
    executions = []
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="sample.write",
            risk=RiskLevel.LOCAL_WRITE,
            executor=lambda arguments, context: executions.append(True),
            state_reader=lambda arguments, context: next(versions),
        )
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.write",
                    arguments={"value": "hello"},
                )
            )
        ]
    )
    runner = orchestrator(tmp_path, provider, registry, lambda plan: True)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.POLICY_DENIED
    assert executions == []
    assert "state changed" in result.tool_results[0].summary


def test_second_mutation_is_not_executed(tmp_path: Path) -> None:
    executions = []

    def execute(arguments, context):
        executions.append(arguments.value)
        return ToolOutcome("wrote {0}".format(arguments.value))

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="sample.write",
            risk=RiskLevel.LOCAL_WRITE,
            executor=execute,
        )
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.write",
                    arguments={"value": "first"},
                ),
                ToolCall(
                    id="call_2",
                    name="sample.write",
                    arguments={"value": "second"},
                ),
            )
        ]
    )
    runner = orchestrator(tmp_path, provider, registry, lambda plan: True)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.LIMIT_REACHED
    assert executions == ["first"]
    assert result.tool_results[-1].status == ToolResultStatus.DENIED


def test_invalid_arguments_are_returned_without_execution(tmp_path: Path) -> None:
    executions = []
    registry = ToolRegistry()
    registry.register(
        make_tool(executor=lambda arguments, context: executions.append(True))
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.read",
                    arguments={"value": "hello", "extra": "unsafe"},
                )
            ),
            final_decision("could not run"),
        ]
    )
    runner = orchestrator(tmp_path, provider, registry, lambda plan: True)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.COMPLETED
    assert executions == []
    assert result.tool_results[0].status == ToolResultStatus.FAILURE


def test_unexpected_tool_error_does_not_leak_details(tmp_path: Path) -> None:
    def fail(arguments, context):
        raise RuntimeError("sensitive implementation detail")

    registry = ToolRegistry()
    registry.register(make_tool(executor=fail))
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.read",
                    arguments={"value": "hello"},
                )
            ),
            final_decision(),
        ]
    )
    runner = orchestrator(tmp_path, provider, registry, lambda plan: True)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.tool_results[0].status == ToolResultStatus.FAILURE
    assert "RuntimeError" in result.tool_results[0].summary
    assert "sensitive implementation detail" not in result.tool_results[0].summary


def test_tool_output_limit_is_enforced(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        make_tool(
            executor=lambda arguments, context: ToolOutcome(
                "large", {"content": "x" * 1000}
            ),
            max_output_bytes=100,
        )
    )
    provider = FakeProvider(
        [
            tool_decision(
                ToolCall(
                    id="call_1",
                    name="sample.read",
                    arguments={"value": "hello"},
                )
            ),
            final_decision(),
        ]
    )
    runner = orchestrator(tmp_path, provider, registry, lambda plan: True)

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.tool_results[0].status == ToolResultStatus.FAILURE
    assert "output limit" in result.tool_results[0].summary


def test_model_round_limit_returns_honest_local_result(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(make_tool())
    call = ToolCall(
        id="call_1",
        name="sample.read",
        arguments={"value": "hello"},
    )
    provider = FakeProvider([tool_decision(call)])
    runner = orchestrator(
        tmp_path,
        provider,
        registry,
        lambda plan: True,
        limits=OrchestratorLimits(max_model_rounds=1),
    )

    result = runner.run(request(), ToolContext("request-1", tmp_path))

    assert result.status == OrchestrationStatus.LIMIT_REACHED
    assert "Model-round limit" in result.decision.message
