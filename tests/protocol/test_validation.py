import json

import pytest

from nage.errors import ProtocolError
from nage.protocol import DecisionStatus, decision_json_schema
from nage.protocol.validation import parse_assistant_decision


def test_parse_valid_json_decision() -> None:
    decision = parse_assistant_decision(
        json.dumps(
            {
                "protocol_version": "2.0",
                "status": "final",
                "message": "Hello",
                "tool_calls": [],
                "memory_proposals": [],
                "continuation": None,
            }
        )
    )

    assert decision.status == DecisionStatus.FINAL


def test_invalid_json_does_not_echo_payload() -> None:
    secret_payload = '{"secret": "do-not-echo"'

    with pytest.raises(ProtocolError) as caught:
        parse_assistant_decision(secret_payload)

    assert "do-not-echo" not in str(caught.value)


def test_wrong_protocol_version_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="protocol_version"):
        parse_assistant_decision({"protocol_version": "1.0", "status": "final"})


def test_non_object_root_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="JSON object"):
        parse_assistant_decision("[]")


def test_response_size_limit_is_enforced() -> None:
    with pytest.raises(ProtocolError, match="size limit"):
        parse_assistant_decision("{}" * 100, max_bytes=10)


def test_model_cannot_use_legacy_clear_or_configuration_fields() -> None:
    with pytest.raises(ProtocolError, match="clear_memory"):
        parse_assistant_decision(
            {
                "protocol_version": "2.0",
                "status": "final",
                "clear_memory": True,
                "endpoint": "https://attacker.example",
            }
        )


def test_schema_forbids_unknown_top_level_fields() -> None:
    schema = decision_json_schema()

    assert schema["additionalProperties"] is False
    assert "status" in schema["properties"]
