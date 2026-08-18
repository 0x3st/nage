import pytest
from pydantic import ValidationError

from nage.protocol import (
    AssistantDecision,
    ContinuationProposal,
    DecisionStatus,
    MemoryProposal,
    ToolCall,
)


def test_final_decision_accepts_memory_proposal() -> None:
    decision = AssistantDecision.model_validate(
        {
            "protocol_version": "2.0",
            "status": "final",
            "message": "Done",
            "memory_proposals": [
                {
                    "operation": "upsert",
                    "content": "  The user prefers pnpm.  ",
                    "kind": "preference",
                    "scope": "global",
                    "confidence": 0.97,
                    "evidence": "explicit_user_statement",
                    "sensitivity": "normal",
                    "supersedes": [],
                }
            ],
        }
    )

    assert decision.status == DecisionStatus.FINAL
    assert decision.memory_proposals[0].content == "The user prefers pnpm."


def test_memory_supersede_requires_targets_and_upsert_rejects_targets() -> None:
    base = {
        "content": "The user prefers pnpm.",
        "kind": "preference",
        "scope": "global",
        "confidence": 0.9,
        "evidence": "explicit_user_statement",
    }

    with pytest.raises(ValidationError, match="requires target"):
        MemoryProposal(operation="supersede", **base)
    with pytest.raises(ValidationError, match="require the supersede"):
        MemoryProposal(operation="upsert", supersedes=["memory-1"], **base)


def test_tool_call_decision_requires_calls() -> None:
    with pytest.raises(ValidationError, match="requires at least one"):
        AssistantDecision(status=DecisionStatus.TOOL_CALL)


def test_calls_require_tool_call_status() -> None:
    with pytest.raises(ValidationError, match="require tool_call"):
        AssistantDecision(
            status=DecisionStatus.FINAL,
            tool_calls=[ToolCall(id="call_1", name="files.list", arguments={})],
        )


def test_tool_call_ids_must_be_unique() -> None:
    call = ToolCall(id="call_1", name="files.list", arguments={})

    with pytest.raises(ValidationError, match="unique"):
        AssistantDecision(
            status=DecisionStatus.TOOL_CALL,
            tool_calls=[call, call],
        )


def test_needs_input_requires_continuation() -> None:
    with pytest.raises(ValidationError, match="continuation"):
        AssistantDecision(status=DecisionStatus.NEEDS_INPUT)


def test_continuation_is_only_valid_for_needs_input() -> None:
    continuation = ContinuationProposal(
        label="Send an email",
        intent="mail.send",
        known={},
        missing=["recipient"],
    )

    with pytest.raises(ValidationError, match="only valid"):
        AssistantDecision(
            status=DecisionStatus.FINAL,
            continuation=continuation,
        )


def test_extra_protocol_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        AssistantDecision.model_validate({"status": "final", "unknown_authority": True})


@pytest.mark.parametrize("name", ["files", "Files.list", "files/list", ".list"])
def test_tool_name_requires_dotted_lowercase_identifier(name: str) -> None:
    with pytest.raises(ValidationError, match="dotted lowercase"):
        ToolCall(id="call_1", name=name)
