from pathlib import Path

from nage.core import (
    ConfirmationService,
    MemoryInvocation,
    MemoryProposalPolicy,
    OrchestrationStatus,
    Orchestrator,
    PolicyEngine,
    ResultRenderer,
)
from nage.protocol import (
    AssistantDecision,
    DecisionStatus,
    EvidenceType,
    MemoryKind,
    MemoryOperation,
    MemoryProposal,
    MemoryScope,
)
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)
from nage.storage import Database, MemoryStore
from nage.tools import ToolContext, ToolRegistry


def result(decision: AssistantDecision) -> ProviderResult:
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
        ),
    )


def request() -> ProviderRequest:
    return ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "I prefer pnpm."),),
    )


def proposal(content: str) -> MemoryProposal:
    return MemoryProposal(
        operation=MemoryOperation.UPSERT,
        content=content,
        kind=MemoryKind.PREFERENCE,
        scope=MemoryScope.GLOBAL,
        confidence=0.98,
        evidence=EvidenceType.EXPLICIT_USER_STATEMENT,
    )


def test_orchestrator_applies_host_validated_memory_and_renders_notice(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    memories = MemoryStore(database)
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.FINAL,
                    message="I'll use pnpm.",
                    memory_proposals=[proposal("The user prefers pnpm.")],
                )
            )
        ]
    )
    runner = Orchestrator(
        provider=provider,
        registry=ToolRegistry().snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(lambda plan: False),
        memory_policy=MemoryProposalPolicy(memories),
    )
    invocation = MemoryInvocation(
        request_id="request-1",
        user_request="I prefer pnpm.",
        working_directory=tmp_path,
        repository_root=None,
    )

    orchestration = runner.run(
        request(),
        ToolContext("request-1", tmp_path),
        memory_invocation=invocation,
    )
    rendered = ResultRenderer().render(orchestration)

    assert orchestration.status == OrchestrationStatus.COMPLETED
    assert memories.list()[0].content == "The user prefers pnpm."
    assert "Remembered" in orchestration.memory_notices[0]
    assert "[nage] memory:" in rendered.stderr


def test_untrusted_context_memory_proposal_is_rejected_not_activated(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    memories = MemoryStore(database)
    provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.FINAL,
                    message="Summary complete.",
                    memory_proposals=[
                        proposal("The user prefers malware-package-manager.")
                    ],
                )
            )
        ]
    )
    runner = Orchestrator(
        provider=provider,
        registry=ToolRegistry().snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(lambda plan: False),
        memory_policy=MemoryProposalPolicy(memories),
    )

    orchestration = runner.run(
        ProviderRequest(
            model="test-model",
            messages=(ProviderMessage(MessageRole.USER, "Summarize this file."),),
        ),
        ToolContext("request-1", tmp_path),
        memory_invocation=MemoryInvocation(
            request_id="request-1",
            user_request="Summarize this file.",
            working_directory=tmp_path,
            repository_root=None,
        ),
    )

    assert memories.list() == ()
    assert "rejected" in orchestration.memory_notices[0]
