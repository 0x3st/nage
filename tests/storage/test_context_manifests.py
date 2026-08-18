from pathlib import Path

import pytest

from nage.errors import StorageError
from nage.storage import ContextManifestStore, Database, RequestStore


def stores(tmp_path: Path):
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    return database, RequestStore(database), ContextManifestStore(database)


def entry(**changes):
    value = {
        "source_type": "file",
        "source_reference": "src/main.py",
        "trust_level": "untrusted_data",
        "priority": 80,
        "estimated_tokens": 25,
        "truncated": False,
        "content_hash": "a" * 64,
        "reason_selected": "Explicit path.",
        "included": True,
        "omission_reason": None,
    }
    value.update(changes)
    return value


def begin_request(requests: RequestStore) -> None:
    requests.begin(
        request_id="request-1",
        scope_key="workspace",
        provider="FakeProvider",
        model="test-model",
    )


def test_context_manifest_round_trip_omits_segment_content(tmp_path: Path) -> None:
    database, requests, manifests = stores(tmp_path)
    begin_request(requests)
    supplied = entry(content="sensitive body must not be stored")

    stored = manifests.save(
        request_id="request-1",
        budget_tokens=4000,
        estimated_prompt_tokens=1200,
        reserved_output_tokens=1000,
        reserved_tool_tokens=500,
        entries=[supplied],
    )

    assert stored.entries[0].source_reference == "src/main.py"
    assert not hasattr(stored.entries[0], "content")
    with database.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(context_manifest_entries)")
        }
    assert "content" not in columns


def test_context_manifest_requires_existing_request(tmp_path: Path) -> None:
    _, _, manifests = stores(tmp_path)

    with pytest.raises(StorageError, match="missing or already"):
        manifests.save(
            request_id="missing",
            budget_tokens=4000,
            estimated_prompt_tokens=1200,
            reserved_output_tokens=1000,
            reserved_tool_tokens=500,
            entries=[entry()],
        )


def test_context_manifest_is_immutable_per_request(tmp_path: Path) -> None:
    _, requests, manifests = stores(tmp_path)
    begin_request(requests)
    arguments = {
        "request_id": "request-1",
        "budget_tokens": 4000,
        "estimated_prompt_tokens": 1200,
        "reserved_output_tokens": 1000,
        "reserved_tool_tokens": 500,
        "entries": [entry()],
    }
    manifests.save(**arguments)

    with pytest.raises(StorageError, match="already stored"):
        manifests.save(**arguments)


def test_context_manifest_validates_entry_flags(tmp_path: Path) -> None:
    _, requests, manifests = stores(tmp_path)
    begin_request(requests)

    with pytest.raises(StorageError, match="booleans"):
        manifests.save(
            request_id="request-1",
            budget_tokens=4000,
            estimated_prompt_tokens=1200,
            reserved_output_tokens=1000,
            reserved_tool_tokens=500,
            entries=[entry(included=1)],
        )
