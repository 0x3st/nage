import json
from pathlib import Path

import pytest

from nage.errors import StorageError
from nage.protocol import Usage
from nage.storage import ActionStore, Database, RequestStore
from nage.tools import RiskLevel


def stores(tmp_path: Path):
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    return RequestStore(database), ActionStore(database)


def test_request_and_action_lifecycle_redacts_secrets(tmp_path: Path) -> None:
    requests, actions = stores(tmp_path)
    request = requests.begin(
        request_id="request-1",
        scope_key=str(tmp_path),
        provider="FakeProvider",
        model="test-model",
    )
    action = actions.begin(
        request_id=request.request_id,
        tool_name="sample.write",
        arguments={
            "path": "file.txt",
            "access_token": "secret-value",
            "content": "large private file body",
            "nested": {"password": "also-secret", "safe": "visible"},
        },
        plan_hash="abc123",
        risk=RiskLevel.LOCAL_WRITE,
        confirmation_status="approved",
    )

    stored_arguments = json.loads(action.arguments_redacted_json)
    assert stored_arguments["access_token"] == "***"
    assert stored_arguments["content"] == "***"
    assert stored_arguments["nested"]["password"] == "***"
    assert stored_arguments["nested"]["safe"] == "visible"

    finished_action = actions.finish(
        action.action_id,
        execution_status="success",
        result_summary="Created file.txt",
    )
    finished_request = requests.finish(
        request.request_id,
        status="completed",
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )

    assert finished_action.finished_at is not None
    assert finished_request.input_tokens == 10
    assert finished_request.output_tokens == 5


def test_action_cannot_be_finished_twice(tmp_path: Path) -> None:
    _, actions = stores(tmp_path)
    action = actions.begin(
        request_id=None,
        tool_name="sample.read",
        arguments={"value": "hello"},
        plan_hash="abc123",
        risk=RiskLevel.READ_ONLY,
        confirmation_status="not_required",
    )
    actions.finish(action.action_id, execution_status="success")

    with pytest.raises(StorageError, match="already finished"):
        actions.finish(action.action_id, execution_status="success")


def test_request_cannot_be_finished_twice(tmp_path: Path) -> None:
    requests, _ = stores(tmp_path)
    request = requests.begin(
        request_id="request-1",
        scope_key=None,
        provider="FakeProvider",
        model="test-model",
    )
    requests.finish(request.request_id, status="completed")

    with pytest.raises(StorageError, match="already finished"):
        requests.finish(request.request_id, status="completed")
