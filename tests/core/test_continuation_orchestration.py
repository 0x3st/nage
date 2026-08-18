from pathlib import Path

from nage.core import (
    ConfirmationService,
    ContinuationInvocation,
    ContinuationMatchKind,
    ContinuationService,
    OrchestrationStatus,
    Orchestrator,
    PolicyEngine,
    ResultRenderer,
)
from nage.core.context import ContextBuilder, RequestEnvelope
from nage.protocol import (
    AssistantDecision,
    ContinuationProposal,
    DecisionStatus,
)
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)
from nage.storage import ContinuationStore, Database
from nage.tools import ToolContext, ToolRegistry


def result(decision: AssistantDecision) -> ProviderResult:
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
        ),
    )


def runner(provider, service):
    return Orchestrator(
        provider=provider,
        registry=ToolRegistry().snapshot(),
        policy=PolicyEngine(),
        confirmation=ConfirmationService(lambda plan: False),
        continuation_service=service,
    )


def test_needs_input_persists_capsule_and_later_final_completes_it(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    store = ContinuationStore(database)
    continuations = ContinuationService(store)
    first_envelope = RequestEnvelope.create(
        "send a project update email",
        tmp_path,
        request_id="request-1",
        terminal_identity="terminal-1",
    )
    first_provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.NEEDS_INPUT,
                    message="Who should receive it?",
                    continuation=ContinuationProposal(
                        label="Send project update email",
                        intent="mail.send",
                        known={"subject": "Project update"},
                        missing=["recipient", "body"],
                    ),
                )
            )
        ]
    )

    first = runner(first_provider, continuations).run(
        ProviderRequest(
            model="test-model",
            messages=(
                ProviderMessage(MessageRole.USER, "send a project update email"),
            ),
        ),
        ToolContext("request-1", tmp_path),
        continuation_invocation=ContinuationInvocation(first_envelope),
    )

    assert first.status == OrchestrationStatus.NEEDS_INPUT
    assert "Saved continuation" in first.continuation_notices[0]
    task_id = first.continuation_notices[0].split()[2].rstrip(":")
    assert store.get(task_id) is not None
    assert "[nage] continuation:" in ResultRenderer().render(first).stderr

    second_envelope = RequestEnvelope.create(
        "alice@example.test",
        tmp_path,
        request_id="request-2",
        terminal_identity="terminal-1",
    )
    match = continuations.match(second_envelope)
    assert match.kind == ContinuationMatchKind.MATCH
    segment = continuations.context_segment(match)
    built = ContextBuilder().build(second_envelope, [segment])
    continuation_invocation = ContinuationInvocation.from_built_context(
        second_envelope, built
    )
    second_provider = FakeProvider(
        [
            result(
                AssistantDecision(
                    status=DecisionStatus.FINAL,
                    message="Draft is complete.",
                )
            )
        ]
    )

    second = runner(second_provider, continuations).run(
        ProviderRequest(model="test-model", messages=built.messages),
        ToolContext("request-2", tmp_path),
        continuation_invocation=continuation_invocation,
    )

    assert second.status == OrchestrationStatus.COMPLETED
    assert "Completed continuation" in second.continuation_notices[0]
    assert store.get(task_id) is None
