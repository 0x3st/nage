from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nage.core import ContinuationMatchKind, ContinuationService
from nage.core.context import ContextSource, RequestEnvelope, RequestOptions, TrustLevel
from nage.errors import StorageError
from nage.protocol import (
    AssistantDecision,
    ContinuationProposal,
    DecisionStatus,
    Sensitivity,
)
from nage.storage import ContinuationStore, Database


def service(tmp_path: Path):
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    store = ContinuationStore(database)
    return store, ContinuationService(store)


def envelope(tmp_path: Path, request: str, **changes) -> RequestEnvelope:
    return RequestEnvelope.create(
        request,
        tmp_path,
        request_id=changes.pop("request_id", "request-1"),
        terminal_identity=changes.pop("terminal_identity", "terminal-1"),
        **changes,
    )


def create(store: ContinuationStore, tmp_path: Path, **changes):
    values = {
        "label": "Send project update email",
        "intent": "mail.send",
        "known": {"subject": "Project update"},
        "missing": ("recipient", "body"),
        "scope_type": "workspace",
        "scope_key": str(tmp_path.resolve()),
        "terminal_key": "terminal-1",
        "sensitivity": "normal",
    }
    values.update(changes)
    return store.create(**values)


def needs_input(**changes) -> AssistantDecision:
    values = {
        "label": "Send project update email",
        "intent": "mail.send",
        "known": {"subject": "Project update"},
        "missing": ["recipient", "body"],
        "sensitivity": Sensitivity.NORMAL,
    }
    values.update(changes)
    return AssistantDecision(
        status=DecisionStatus.NEEDS_INPUT,
        message="Who should receive it?",
        continuation=ContinuationProposal(**values),
    )


def test_email_slot_and_scope_produce_strong_match(tmp_path: Path) -> None:
    store, continuations = service(tmp_path)
    record = create(store, tmp_path)

    match = continuations.match(envelope(tmp_path, "alice@example.test"))

    assert match.kind == ContinuationMatchKind.MATCH
    assert match.record.task_id == record.task_id
    segment = continuations.context_segment(match)
    assert segment.source_type == ContextSource.CONTINUATION
    assert segment.trust_level == TrustLevel.POLICY_SELECTED
    assert record.task_id in segment.content


def test_unrelated_request_does_not_inherit_hot_task(tmp_path: Path) -> None:
    store, continuations = service(tmp_path)
    create(store, tmp_path)

    match = continuations.match(envelope(tmp_path, "show disk usage"))

    assert match.kind == ContinuationMatchKind.NONE


def test_fresh_option_bypasses_matching(tmp_path: Path) -> None:
    store, continuations = service(tmp_path)
    create(store, tmp_path)

    match = continuations.match(
        envelope(
            tmp_path,
            "alice@example.test",
            options=RequestOptions(fresh=True),
        )
    )

    assert match.kind == ContinuationMatchKind.NONE


def test_ambiguous_candidates_are_not_attached(tmp_path: Path) -> None:
    store, continuations = service(tmp_path)
    first = create(store, tmp_path, label="Send sales email")
    second = create(store, tmp_path, label="Send project email")

    match = continuations.match(envelope(tmp_path, "alice@example.test"))

    assert match.kind == ContinuationMatchKind.AMBIGUOUS
    assert match.record is None
    assert {item[0] for item in match.candidates} == {
        first.task_id,
        second.task_id,
    }
    assert all("subject" not in label for _, label in match.candidates)
    prompt = continuations.ambiguity_prompt(match)
    assert first.task_id in prompt
    assert second.task_id in prompt
    assert "Project update" not in prompt


def test_dormant_task_requires_strong_semantic_match_and_eventually_expires(
    tmp_path: Path,
) -> None:
    store, continuations = service(tmp_path)
    now = datetime.now(timezone.utc)
    record = create(store, tmp_path, now=now)
    store.cleanup(now=now + timedelta(hours=3))

    unrelated = continuations.match(envelope(tmp_path, "show disk usage"))
    related = continuations.match(envelope(tmp_path, "send project update email"))

    assert unrelated.kind == ContinuationMatchKind.NONE
    assert related.kind == ContinuationMatchKind.MATCH
    assert related.record.task_id == record.task_id

    store.cleanup(now=now + timedelta(days=31))
    assert (
        continuations.match(envelope(tmp_path, "send project update email")).kind
        == ContinuationMatchKind.NONE
    )


def test_explicit_task_reference_matches_without_guessing(tmp_path: Path) -> None:
    store, continuations = service(tmp_path)
    record = create(store, tmp_path)

    match = continuations.match(
        envelope(tmp_path, "continue {0}".format(record.task_id))
    )

    assert match.kind == ContinuationMatchKind.MATCH
    assert match.record.task_id == record.task_id


def test_needs_input_creates_and_updates_capsule_then_final_completes(
    tmp_path: Path,
) -> None:
    store, continuations = service(tmp_path)
    request = envelope(tmp_path, "send a project update email")

    created = continuations.handle_decision(needs_input(), request)
    record = store.get(created.task_id)
    assert record is not None

    updated = continuations.handle_decision(
        needs_input(
            known={
                "subject": "Project update",
                "recipient": "alice@example.test",
            },
            missing=["body"],
        ),
        request,
        attached_task_id=created.task_id,
    )
    assert updated.task_id == created.task_id
    assert store.get(created.task_id).missing == ("body",)

    completed = continuations.handle_decision(
        AssistantDecision(status=DecisionStatus.FINAL, message="Sent."),
        request,
        attached_task_id=created.task_id,
    )
    assert "Completed" in completed.message
    assert store.get(created.task_id) is None


def test_secret_and_approval_state_are_never_persisted(tmp_path: Path) -> None:
    store, continuations = service(tmp_path)
    request = envelope(tmp_path, "send an email")

    secret = continuations.handle_decision(
        needs_input(
            known={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
            sensitivity=Sensitivity.SECRET,
        ),
        request,
    )
    assert "not saved" in secret.message

    with pytest.raises(StorageError, match="approval"):
        continuations.handle_decision(
            needs_input(known={"user_approved": True}),
            request,
        )
    assert store.candidates("workspace", str(tmp_path.resolve())) == ()
