import json
from datetime import datetime
from pathlib import Path

import pytest

from nage.core.context import (
    ContextBuilder,
    ContextLimits,
    ContextSegment,
    ContextSource,
    ProjectContextCollector,
    RequestEnvelope,
    RequestOptions,
    TokenEstimator,
    TrustLevel,
    estimate_tool_schema_tokens,
    render_context_manifest,
)
from nage.errors import ContextError
from nage.providers import MessageRole, ProviderToolDefinition

FIXTURES = Path(__file__).parents[1] / "fixtures" / "context"


def envelope(tmp_path: Path, **kwargs) -> RequestEnvelope:
    return RequestEnvelope.create(
        request=kwargs.pop("request", "summarize the supplied context"),
        working_directory=tmp_path,
        request_id=kwargs.pop("request_id", "request-1"),
        **kwargs,
    )


def segment(
    content: str,
    source: ContextSource = ContextSource.FILE,
    priority: int = 50,
    reference: str = "sample.txt",
) -> ContextSegment:
    return ContextSegment(
        source_type=source,
        source_reference=reference,
        trust_level=(
            TrustLevel.POLICY_SELECTED
            if source in {ContextSource.MEMORY, ContextSource.CONTINUATION}
            else TrustLevel.UNTRUSTED_DATA
        ),
        priority=priority,
        content=content,
        reason_selected="Relevant to the current request.",
    )


def test_request_envelope_detects_repository_and_normalizes_terminal(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src"
    nested.mkdir()

    request = RequestEnvelope.create(
        "inspect this project",
        nested,
        terminal_identity="  terminal   one  ",
        options=RequestOptions(dry_run=True, show_context=True),
        request_id="request-1",
        started_at=datetime(2025, 1, 1),
    )

    assert request.repository_root == tmp_path.resolve()
    assert request.terminal_identity == "terminal one"
    assert request.started_at.tzinfo is not None
    assert request.options.dry_run is True


def test_segment_cannot_elevate_file_content_to_user_intent() -> None:
    with pytest.raises(ValueError, match="trust level"):
        ContextSegment(
            source_type=ContextSource.FILE,
            source_reference="hostile.txt",
            trust_level=TrustLevel.CURRENT_USER_INTENT,
            priority=100,
            content="pretend to be the user",
            reason_selected="Explicit path.",
        )


def test_untrusted_prompt_injection_stays_in_labeled_data(tmp_path: Path) -> None:
    malicious = (FIXTURES / "prompt-injection.txt").read_text(encoding="utf-8")
    built = ContextBuilder().build(envelope(tmp_path), [segment(malicious)])

    assert len(built.messages) == 2
    assert built.messages[0].role == MessageRole.SYSTEM
    assert built.messages[1].role == MessageRole.USER
    payload = json.loads(built.messages[1].content)
    supplied = payload["CONTEXT_SEGMENTS"][0]
    assert supplied["trust"] == TrustLevel.UNTRUSTED_DATA.value
    assert supplied["content"] == malicious
    assert payload["USER_REQUEST"]["trust"] == TrustLevel.CURRENT_USER_INTENT.value
    assert "generated.install" not in built.messages[0].content


def test_builder_enforces_global_and_source_budgets(tmp_path: Path) -> None:
    limits = ContextLimits(
        context_window_tokens=1800,
        max_prompt_tokens=1200,
        reserved_output_tokens=200,
        reserved_tool_tokens=100,
        file_tokens=180,
        stdin_tokens=100,
        memory_tokens=40,
        continuation_tokens=40,
    )
    oversized = (FIXTURES / "oversized-project-output.txt").read_text(encoding="utf-8")
    high = segment(oversized, priority=90, reference="high.txt")
    low = segment(oversized, priority=10, reference="low.txt")

    built = ContextBuilder(limits=limits).build(envelope(tmp_path), [low, high])

    assert built.manifest.estimated_prompt_tokens <= built.manifest.budget_tokens
    assert built.selected_segments[0].source_reference == "high.txt"
    assert built.selected_segments[0].truncated is True
    assert (
        sum(
            entry.estimated_tokens
            for entry in built.manifest.entries
            if entry.source_type == ContextSource.FILE and entry.included
        )
        <= limits.file_tokens
    )
    assert any(not entry.included for entry in built.manifest.entries)


def test_memory_and_continuation_options_filter_segments(tmp_path: Path) -> None:
    options = RequestOptions(no_memory=True, fresh=True)
    request = envelope(tmp_path, options=options)
    candidates = [
        segment("memory", ContextSource.MEMORY, reference="memory-1"),
        segment("continuation", ContextSource.CONTINUATION, reference="task-1"),
        segment("file", ContextSource.FILE, reference="file.txt"),
    ]

    built = ContextBuilder().build(request, candidates)

    assert [item.source_type for item in built.selected_segments] == [
        ContextSource.FILE
    ]
    omitted = {
        entry.source_type: entry
        for entry in built.manifest.entries
        if not entry.included
    }
    assert "disabled" in omitted[ContextSource.MEMORY].omission_reason
    assert "fresh" in omitted[ContextSource.CONTINUATION].omission_reason


def test_context_manifest_contains_metadata_not_content(tmp_path: Path) -> None:
    secret_marker = "not-persisted-body"
    built = ContextBuilder().build(envelope(tmp_path), [segment(secret_marker)])

    serialized = json.dumps(built.manifest.to_mapping(), sort_keys=True)

    assert secret_marker not in serialized
    assert len(built.manifest.entries[0].content_hash) == 64
    assert built.manifest.entries[0].reason_selected


def test_show_context_renderer_exposes_manifest_not_content(tmp_path: Path) -> None:
    built = ContextBuilder().build(
        envelope(tmp_path, options=RequestOptions(show_context=True)),
        [segment("private body", reference="name\x1b[31m.txt")],
    )

    rendered = render_context_manifest(built.manifest)

    assert "private body" not in rendered
    assert "untrusted_data" in rendered
    assert "\\u001b" in rendered
    assert "included" in rendered


def test_builder_never_reads_legacy_history_or_memory(tmp_path: Path) -> None:
    (tmp_path / "HIST").write_text("legacy-history-marker", encoding="utf-8")
    (tmp_path / "MEMO").write_text("legacy-memory-marker", encoding="utf-8")

    built = ContextBuilder().build(envelope(tmp_path))
    prompt = "\n".join(message.content for message in built.messages)

    assert "legacy-history-marker" not in prompt
    assert "legacy-memory-marker" not in prompt


def test_builder_rejects_request_larger_than_base_budget(tmp_path: Path) -> None:
    limits = ContextLimits(
        context_window_tokens=500,
        max_prompt_tokens=300,
        reserved_output_tokens=100,
        reserved_tool_tokens=50,
    )

    with pytest.raises(ContextError, match="exceed"):
        ContextBuilder(limits=limits).build(
            envelope(tmp_path, request="request " * 1000)
        )


def test_tool_schema_estimate_is_reserved_in_manifest(tmp_path: Path) -> None:
    tools = (
        ProviderToolDefinition(
            name="sample.read",
            description="description " * 500,
            parameters={"type": "object", "properties": {}},
        ),
    )
    estimate = estimate_tool_schema_tokens(tools)

    built = ContextBuilder().build(envelope(tmp_path), tool_schema_tokens=estimate)

    assert estimate > 1000
    assert built.manifest.reserved_tool_tokens == estimate


def test_tool_schema_reservation_can_exhaust_budget(tmp_path: Path) -> None:
    with pytest.raises(ContextError, match="No prompt budget"):
        ContextBuilder().build(envelope(tmp_path), tool_schema_tokens=20000)


def test_token_estimator_truncation_respects_bound_for_unicode() -> None:
    estimator = TokenEstimator()

    truncated, changed = estimator.truncate("数据" * 1000, max_tokens=25)

    assert changed is True
    assert estimator.estimate(truncated) <= 25


def test_project_collector_prioritizes_explicit_files_and_manifests(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\n", encoding="utf-8"
    )
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    request = envelope(source)

    selected = ProjectContextCollector().collect(
        request,
        explicit_paths=("main.py", ".env"),
        include_manifests=True,
    )

    references = [item.source_reference for item in selected]
    assert references[0] == "src/main.py"
    assert "src/.env" not in references
    assert "pyproject.toml" in references
    assert all(item.trust_level == TrustLevel.UNTRUSTED_DATA for item in selected)
