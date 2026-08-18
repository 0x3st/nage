from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nage.errors import StorageError
from nage.storage import Database, MemoryStore


def store(tmp_path: Path, enable_fts: bool = True) -> MemoryStore:
    database = Database(tmp_path / "nage.sqlite3", timeout_seconds=10)
    database.initialize()
    return MemoryStore(database, enable_fts=enable_fts)


def upsert(memory_store: MemoryStore, content: str, **changes):
    values = {
        "content": content,
        "kind": "preference",
        "scope_type": "global",
        "scope_key": "",
        "source_type": "explicit_user_statement",
        "confidence": 0.95,
        "source_reference": "request-1",
        "source_hash": "a" * 64,
    }
    values.update(changes)
    return memory_store.upsert(**values)


def test_memory_upsert_normalizes_and_merges_exact_duplicate(tmp_path: Path) -> None:
    memories = store(tmp_path)

    created = upsert(memories, "  The user prefers   pnpm. ")
    merged = upsert(memories, "the user prefers pnpm.", confidence=0.99)

    assert created.disposition == "created"
    assert merged.disposition == "merged"
    assert merged.record.id == created.record.id
    assert merged.record.evidence_count == 2
    assert merged.record.confidence == 0.99
    assert len(memories.list()) == 1


def test_memory_identity_includes_scope(tmp_path: Path) -> None:
    memories = store(tmp_path)

    global_memory = upsert(memories, "Use pytest.")
    workspace_memory = upsert(
        memories,
        "Use pytest.",
        scope_type="workspace",
        scope_key="/workspace",
    )

    assert global_memory.record.id != workspace_memory.record.id


def test_fts_and_fallback_search_return_active_scoped_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    fts_store = MemoryStore(database)
    project = upsert(
        fts_store,
        "This repository uses pytest for tests.",
        kind="project",
        scope_type="repository",
        scope_key="/repo",
    )
    upsert(
        fts_store,
        "Candidate pytest observation.",
        status="candidate",
    )

    fts_results = fts_store.search("pytest tests", repository_key="/repo")
    fallback_results = MemoryStore(database, enable_fts=False).search(
        "pytest tests", repository_key="/repo"
    )

    assert [item.id for item in fts_results] == [project.record.id]
    assert [item.id for item in fallback_results] == [project.record.id]


def test_sensitive_memory_is_never_indexed_or_retrieved(tmp_path: Path) -> None:
    memories = store(tmp_path)
    upsert(
        memories,
        "api_key = sk-abcdefghijklmnopqrstuvwxyz",
        sensitivity="sensitive",
    )

    assert memories.search("sk abcdefghijklmnopqrstuvwxyz") == ()
    assert memories.retrieve("api key", None, None) == ()


def test_retrieval_prioritizes_global_instruction_and_excludes_candidate(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)
    instruction = upsert(
        memories,
        "Keep answers concise.",
        kind="instruction",
    )
    relevant = upsert(
        memories,
        "The project uses pnpm.",
        kind="project",
        scope_type="repository",
        scope_key="/repo",
    )
    upsert(memories, "Candidate pnpm note.", status="candidate")

    selected = memories.retrieve(
        "Which package manager should I use? pnpm",
        workspace_key="/workspace",
        repository_key="/repo",
        limit=5,
    )

    assert selected[0].id == instruction.record.id
    assert relevant.record.id in {item.id for item in selected}
    assert all(item.status == "active" for item in selected)


def test_forget_and_restore_are_reversible_and_update_search(tmp_path: Path) -> None:
    memories = store(tmp_path)
    created = upsert(memories, "Prefer uv for Python environments.")

    forgotten = memories.forget(created.record.id)
    assert forgotten.status == "forgotten"
    assert memories.search("uv Python") == ()

    restored = memories.restore(created.record.id)
    assert restored.status == "active"
    assert memories.search("uv Python")[0].id == created.record.id


def test_correct_supersedes_old_memory(tmp_path: Path) -> None:
    memories = store(tmp_path)
    original = upsert(memories, "Prefer npm.")

    corrected = memories.correct(original.record.id, "Prefer pnpm.")

    assert memories.get(original.record.id).status == "superseded"
    assert corrected.record.status == "active"
    assert corrected.record.supersedes_id == original.record.id
    assert memories.search("pnpm")[0].id == corrected.record.id


def test_supersede_multiple_active_memories_atomically(tmp_path: Path) -> None:
    memories = store(tmp_path)
    first = upsert(memories, "Prefer npm.")
    second = upsert(memories, "Use npm workspaces.")

    replacement = memories.supersede(
        [first.record.id, second.record.id],
        content="Prefer pnpm workspaces.",
        kind="preference",
        scope_type="global",
        scope_key="",
        source_type="explicit_user_statement",
        confidence=0.98,
        source_reference="request-2",
        source_hash="b" * 64,
    )

    assert memories.get(first.record.id).status == "superseded"
    assert memories.get(second.record.id).status == "superseded"
    assert replacement.record.status == "active"


def test_clear_uses_exact_state_digest_and_is_reversible_per_item(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)
    first = upsert(memories, "Prefer one.")
    upsert(memories, "Prefer two.")
    before = memories.state_digest()

    assert before["count"] == 2
    assert memories.clear() == 2
    assert memories.list() == ()
    memories.restore(first.record.id)
    assert memories.get(first.record.id).status == "active"


def test_expired_memory_is_not_retrieved(tmp_path: Path) -> None:
    memories = store(tmp_path)
    expired = (
        (datetime.now(timezone.utc) - timedelta(minutes=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    upsert(memories, "Old preference for yarn.", expires_at=expired)

    assert memories.search("yarn") == ()


def test_concurrent_duplicate_upsert_merges_without_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "nage.sqlite3"
    database = Database(path, timeout_seconds=10)
    database.initialize()

    def insert():
        return upsert(
            MemoryStore(Database(path, timeout_seconds=10)),
            "Prefer deterministic tests.",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: insert(), range(2)))

    memories = MemoryStore(Database(path, timeout_seconds=10))
    assert len({item.record.id for item in results}) == 1
    assert memories.list()[0].evidence_count == 2


def test_memory_validation_rejects_invalid_scope_and_transition(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)

    with pytest.raises(StorageError, match="scope key"):
        upsert(
            memories,
            "Project preference.",
            scope_type="repository",
            scope_key="",
        )
    created = upsert(memories, "Prefer ruff.")
    memories.forget(created.record.id)
    with pytest.raises(StorageError, match="cannot transition"):
        memories.forget(created.record.id)
