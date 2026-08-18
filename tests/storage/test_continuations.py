from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nage.errors import StorageError
from nage.storage import ContinuationStore, Database

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def store(tmp_path: Path):
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    return database, ContinuationStore(database)


def create(continuations: ContinuationStore, **changes):
    values = {
        "label": "Send project update",
        "intent": "mail.send",
        "known": {"subject": "Project update"},
        "missing": ("recipient", "body"),
        "scope_type": "workspace",
        "scope_key": "/workspace",
        "terminal_key": "terminal-1",
        "sensitivity": "normal",
        "now": NOW,
    }
    values.update(changes)
    return continuations.create(**values)


def test_continuation_round_trip_has_host_owned_identity_and_lifecycle(
    tmp_path: Path,
) -> None:
    _, continuations = store(tmp_path)

    record = create(continuations)

    assert record.task_id.startswith("task-")
    assert record.status == "hot"
    assert record.known == {"subject": "Project update"}
    assert record.missing == ("recipient", "body")
    assert record.implicit_until > record.created_at
    assert record.retain_until > record.implicit_until


def test_continuation_rejects_secrets_and_approval_state(tmp_path: Path) -> None:
    _, continuations = store(tmp_path)

    with pytest.raises(StorageError, match="secrets"):
        create(
            continuations,
            known={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
        )
    with pytest.raises(StorageError, match="approval"):
        create(
            continuations,
            known={"nested": {"user_approved": True}},
        )
    with pytest.raises(StorageError, match="approval"):
        create(
            continuations,
            known={"plan_hash": "a" * 64},
        )


def test_sensitive_continuation_uses_short_retention(tmp_path: Path) -> None:
    _, continuations = store(tmp_path)

    record = create(continuations, sensitivity="sensitive")

    implicit = datetime.fromisoformat(record.implicit_until.replace("Z", "+00:00"))
    retained = datetime.fromisoformat(record.retain_until.replace("Z", "+00:00"))
    assert implicit - NOW == timedelta(minutes=30)
    assert retained - NOW == timedelta(days=1)


def test_cleanup_moves_hot_to_dormant_then_expires(tmp_path: Path) -> None:
    database, continuations = store(tmp_path)
    record = create(continuations)

    dormant = continuations.cleanup(now=NOW + timedelta(hours=3))
    assert dormant.dormant == 1
    assert dormant.expired == 0
    assert continuations.get(record.task_id).status == "dormant"

    expired = continuations.cleanup(now=NOW + timedelta(days=31))
    assert expired.expired == 1
    assert continuations.get(record.task_id) is None
    with database.connection() as connection:
        operations = {
            row["operation"]
            for row in connection.execute(
                "SELECT operation FROM continuation_events WHERE task_id = ?",
                (record.task_id,),
            )
        }
    assert {"create", "dormant", "expire"} <= operations


def test_update_refreshes_capsule_without_changing_identity(tmp_path: Path) -> None:
    _, continuations = store(tmp_path)
    record = create(continuations)

    updated = continuations.update(
        record.task_id,
        label="Send project update",
        intent="mail.send",
        known={"subject": "Project update", "recipient": "team@example.test"},
        missing=("body",),
        sensitivity="normal",
        now=NOW + timedelta(minutes=10),
    )

    assert updated.task_id == record.task_id
    assert updated.known["recipient"] == "team@example.test"
    assert updated.missing == ("body",)
    assert updated.status == "hot"


def test_complete_deletes_capsule_but_keeps_minimal_event(tmp_path: Path) -> None:
    database, continuations = store(tmp_path)
    record = create(continuations)

    assert continuations.complete(record.task_id, now=NOW) is True
    assert continuations.get(record.task_id) is None
    assert continuations.complete(record.task_id, now=NOW) is False
    with database.connection() as connection:
        event = connection.execute(
            "SELECT * FROM continuation_events WHERE operation = 'complete'"
        ).fetchone()
    assert event["task_id"] == record.task_id
    assert "known" not in set(event.keys())


def test_candidates_are_strictly_scoped(tmp_path: Path) -> None:
    _, continuations = store(tmp_path)
    expected = create(continuations)
    create(
        continuations,
        scope_key="/other",
        terminal_key="terminal-2",
    )

    candidates = continuations.candidates("workspace", "/workspace", now=NOW, limit=10)

    assert [item.task_id for item in candidates] == [expected.task_id]
