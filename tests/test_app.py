import io
import json
from pathlib import Path

from nage.app import NageApp
from nage.config import AppConfig
from nage.core import ConfirmationService, OrchestrationStatus, RequestOptions
from nage.paths import AppPaths
from nage.protocol import (
    AssistantDecision,
    DecisionStatus,
    EvidenceType,
    MemoryKind,
    MemoryOperation,
    MemoryProposal,
)
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderResult,
)


def final_result(message="done") -> ProviderResult:
    decision = AssistantDecision(status=DecisionStatus.FINAL, message=message)
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
        ),
    )


def app(tmp_path: Path, provider: FakeProvider) -> NageApp:
    value = NageApp(
        paths=AppPaths(tmp_path / "data"),
        provider=provider,
        confirmation=ConfirmationService(lambda plan: False),
    )
    value.prepare()
    return value


def test_app_composes_scoped_memory_into_bounded_provider_context(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([final_result()])
    application = app(tmp_path, provider)
    application.memories.upsert(
        content="Keep answers concise.",
        kind="instruction",
        scope_type="global",
        scope_key="",
        source_type="explicit_user_statement",
        confidence=1.0,
    )

    result = application.execute(
        "answer this question",
        AppConfig(api_key="test-key"),
        tmp_path,
    )

    payload = json.loads(provider.requests[0].messages[1].content)
    assert any(
        segment["source_type"] == "memory"
        and "Keep answers concise" in segment["content"]
        for segment in payload["CONTEXT_SEGMENTS"]
    )
    assert (
        result.context_manifest.estimated_prompt_tokens
        <= result.context_manifest.budget_tokens
    )
    assert len(provider.requests[0].tools) == 26


def test_no_memory_option_removes_memory_from_prompt_and_proposals(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([final_result()])
    application = app(tmp_path, provider)
    application.memories.upsert(
        content="Keep answers concise.",
        kind="instruction",
        scope_type="global",
        scope_key="",
        source_type="explicit_user_statement",
        confidence=1.0,
    )

    application.execute(
        "answer this question",
        AppConfig(api_key="test-key"),
        tmp_path,
        options=RequestOptions(no_memory=True),
    )

    payload = json.loads(provider.requests[0].messages[1].content)
    assert all(
        segment["source_type"] != "memory" for segment in payload["CONTEXT_SEGMENTS"]
    )


def test_no_memory_option_rejects_automatic_memory_proposal(
    tmp_path: Path,
) -> None:
    decision = AssistantDecision(
        status=DecisionStatus.FINAL,
        message="Okay.",
        memory_proposals=[
            MemoryProposal(
                operation=MemoryOperation.UPSERT,
                content="The user prefers pnpm.",
                kind=MemoryKind.PREFERENCE,
                confidence=0.99,
                evidence=EvidenceType.EXPLICIT_USER_STATEMENT,
            )
        ],
    )
    provider = FakeProvider(
        [
            ProviderResult(
                decision=decision,
                assistant_message=ProviderMessage(
                    MessageRole.ASSISTANT,
                    decision.model_dump_json(),
                ),
            )
        ]
    )
    application = app(tmp_path, provider)

    result = application.execute(
        "I prefer pnpm.",
        AppConfig(api_key="test-key"),
        tmp_path,
        options=RequestOptions(no_memory=True),
    )

    assert application.memories.list() == ()
    assert "disabled" in result.orchestration.memory_notices[0]


def test_ambiguous_continuation_returns_local_needs_input_without_provider(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([])
    application = app(tmp_path, provider)
    for label in ("Send sales email", "Send project email"):
        application.continuations.create(
            label=label,
            intent="mail.send",
            known={"subject": label},
            missing=("recipient",),
            scope_type="workspace",
            scope_key=str(tmp_path.resolve()),
            terminal_key="terminal-1",
            sensitivity="normal",
        )

    result = application.execute(
        "alice@example.test",
        AppConfig(api_key="test-key"),
        tmp_path,
        terminal_identity="terminal-1",
    )

    assert result.orchestration.status == OrchestrationStatus.NEEDS_INPUT
    assert "Multiple pending tasks" in result.orchestration.decision.message
    assert provider.requests == []


def test_large_stdin_uses_artifact_and_manifest_without_full_prompt(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([final_result()])
    application = app(tmp_path, provider)
    source = b"line\n" * 10000

    result = application.execute(
        "summarize stdin",
        AppConfig(api_key="test-key"),
        tmp_path,
        stdin_stream=io.BytesIO(source),
    )

    entries = [
        entry
        for entry in result.context_manifest.entries
        if entry.source_type.value == "stdin"
    ]
    assert len(entries) == 1
    assert entries[0].truncated is True
    prompt = provider.requests[0].messages[1].content
    assert len(prompt.encode("utf-8")) < len(source)
    with application.database.connection() as connection:
        artifact_count = connection.execute(
            "SELECT COUNT(*) FROM artifacts"
        ).fetchone()[0]
        manifest_count = connection.execute(
            "SELECT COUNT(*) FROM context_manifests"
        ).fetchone()[0]
    assert artifact_count == 1
    assert manifest_count == 1
