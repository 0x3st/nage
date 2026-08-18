import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nage.errors import StorageError
from nage.paths import AppPaths
from nage.storage.artifacts import ArtifactStore
from nage.storage.database import Database


def make_store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths(tmp_path / "data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return ArtifactStore(database, paths)


def test_artifact_round_trip(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    record = store.create_text("hello", ttl_seconds=60)

    assert store.get(record.artifact_id) == record
    assert store.read_bytes(record.artifact_id, max_bytes=5) == b"hello"
    artifact_path = store.paths.root / record.relative_path
    if os.name != "nt":
        assert artifact_path.stat().st_mode & 0o777 == 0o600


def test_artifact_read_limit_is_enforced(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.create_bytes(b"12345")

    with pytest.raises(StorageError, match="above"):
        store.read_bytes(record.artifact_id, max_bytes=4)


def test_artifact_hash_is_verified(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.create_text("original")
    (store.paths.root / record.relative_path).write_text("changed", encoding="utf-8")

    with pytest.raises(StorageError, match="hash"):
        store.read_bytes(record.artifact_id, max_bytes=100)


def test_artifact_delete_removes_metadata_and_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.create_text("temporary")
    path = store.paths.root / record.relative_path

    assert store.delete(record.artifact_id) is True
    assert path.exists() is False
    assert store.get(record.artifact_id) is None
    assert store.delete(record.artifact_id) is False


def test_artifact_cleanup_uses_expiration(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = store.create_text("temporary", ttl_seconds=1)

    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert store.cleanup_expired(now=future) == 1
    assert store.get(record.artifact_id) is None
