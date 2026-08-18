from pathlib import Path

from nage.core import (
    ConfirmationService,
    MemoryInvocation,
    MemoryProposalPolicy,
    OrchestrationStatus,
    Orchestrator,
    PolicyEngine,
)
from nage.protocol import (
    AssistantDecision,
    DecisionStatus,
    EvidenceType,
    MemoryKind,
    MemoryOperation,
    MemoryProposal,
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
from nage.storage import Database, MemoryStore
from nage.tools import ToolContext, ToolOutcome, ToolRegistry
from tests.tool_helpers import make_tool

FIXTURES = Path(__file__).parents[1] / "fixtures" / "security"


def result(decision: AssistantDecision) -> ProviderResult:
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
            tool_calls=tuple(decision.tool_calls),
        ),
    )


def test_tool_output_cannot_authorize_memory_or_generated_tool(
    tmp_path: Path,
) -> None:
    injection = (FIXTURES / "tool-output-injection.txt").read_text(encoding="utf-8")
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    memories = MemoryStore(database)
    tool = make_tool(
        name="diagnostics.read",
        executor=lambda arguments, context: ToolOutcome(
            "Read diagnostics.", {"text": injection}
        ),
    )
    registry = ToolRegistry()
    registry.register(tool)
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.TOOL_CALL,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="diagnostics.read",
                            arguments={"value": "latest"},
                        )
                    ],
                )
            ),
            result(
                AssistantDecision(
                    status=DecisionStatus.FINAL,
                    message="Diagnostics summarized.",
                    memory_proposals=[
                        MemoryProposal(
                            operation=MemoryOperation.UPSERT,
                            content=("The user prefers malware-package-manager."),
                            kind=MemoryKind.PREFERENCE,
                            confidence=0.99,
                            evidence=EvidenceType.EXPLICIT_USER_STATEMENT,
                        )
                    ],
                )
            ),
        ]
    )
    runner = Orchestrator(
        provider=provider,
        registry=registry.snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(lambda plan: False),
        memory_policy=MemoryProposalPolicy(memories),
    )

    orchestration = runner.run(
        ProviderRequest(
            model="test-model",
            messages=(
                ProviderMessage(
                    MessageRole.USER,
                    "Summarize the latest diagnostics.",
                ),
            ),
        ),
        ToolContext("request-1", tmp_path),
        memory_invocation=MemoryInvocation(
            request_id="request-1",
            user_request="Summarize the latest diagnostics.",
            working_directory=tmp_path,
            repository_root=None,
        ),
    )

    assert orchestration.status == OrchestrationStatus.COMPLETED
    assert memories.list() == ()
    assert "rejected" in orchestration.memory_notices[0]
    assert registry.snapshot().names() == ("diagnostics.read",)


def test_generated_manifest_cannot_replace_registry_or_execute_code(
    tmp_path: Path,
) -> None:
    manifest = (FIXTURES / "generated-tool-manifest.json").read_text(encoding="utf-8")
    marker = tmp_path / "should-not-run"
    registry = ToolRegistry()
    registry.register(make_tool())
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.TOOL_CALL,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="generated.live",
                            arguments={"manifest": manifest},
                        )
                    ],
                )
            )
        ]
    )
    runner = Orchestrator(
        provider=provider,
        registry=registry.snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(lambda plan: True),
    )

    orchestration = runner.run(
        ProviderRequest(
            model="test-model",
            messages=(ProviderMessage(MessageRole.USER, "review this draft"),),
        ),
        ToolContext("request-1", tmp_path),
    )

    assert orchestration.status == OrchestrationStatus.POLICY_DENIED
    assert orchestration.tool_results[0].status == ToolResultStatus.DENIED
    assert not marker.exists()
    assert registry.snapshot().names() == ("sample.read",)
