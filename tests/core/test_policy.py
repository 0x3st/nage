from pathlib import Path

import pytest

from nage.core.policy import PolicyDecisionKind, PolicyEngine
from nage.errors import ToolError
from nage.tools import RiskLevel, ToolContext
from tests.tool_helpers import make_tool


def context(tmp_path: Path) -> ToolContext:
    return ToolContext("request-1", tmp_path)


def test_read_only_tool_is_allowed(tmp_path: Path) -> None:
    spec = make_tool(risk=RiskLevel.READ_ONLY)
    arguments = spec.validate_arguments({"value": "hello"})

    decision = PolicyEngine().evaluate(spec, arguments, context(tmp_path))

    assert decision.kind == PolicyDecisionKind.ALLOW
    assert decision.risk == RiskLevel.READ_ONLY


def test_mutating_tool_requires_confirmation(tmp_path: Path) -> None:
    spec = make_tool(risk=RiskLevel.LOCAL_WRITE)
    arguments = spec.validate_arguments({"value": "hello"})

    decision = PolicyEngine().evaluate(spec, arguments, context(tmp_path))

    assert decision.kind == PolicyDecisionKind.CONFIRM


def test_missing_permission_is_denied(tmp_path: Path) -> None:
    spec = make_tool(required_permissions=("filesystem.read",))
    arguments = spec.validate_arguments({"value": "hello"})

    decision = PolicyEngine().evaluate(spec, arguments, context(tmp_path))

    assert decision.kind == PolicyDecisionKind.DENY
    assert decision.risk == RiskLevel.DENIED
    assert "filesystem.read" in decision.reason


def test_dynamic_risk_can_raise_but_not_lower_minimum(tmp_path: Path) -> None:
    raised = make_tool(
        risk=RiskLevel.READ_ONLY,
        risk_evaluator=lambda arguments, tool_context: RiskLevel.DESTRUCTIVE,
    )
    lowered = make_tool(
        risk=RiskLevel.DESTRUCTIVE,
        risk_evaluator=lambda arguments, tool_context: RiskLevel.READ_ONLY,
    )
    arguments = raised.validate_arguments({"value": "hello"})
    engine = PolicyEngine()

    assert (
        engine.evaluate(raised, arguments, context(tmp_path)).risk
        == RiskLevel.DESTRUCTIVE
    )
    assert (
        engine.evaluate(lowered, arguments, context(tmp_path)).risk
        == RiskLevel.DESTRUCTIVE
    )


def test_risk_evaluator_failure_is_a_tool_error(tmp_path: Path) -> None:
    def fail(arguments, tool_context):
        raise RuntimeError("private implementation detail")

    spec = make_tool(risk_evaluator=fail)
    arguments = spec.validate_arguments({"value": "hello"})

    with pytest.raises(ToolError, match="Risk evaluation failed") as caught:
        PolicyEngine().evaluate(spec, arguments, context(tmp_path))

    assert "private implementation detail" not in str(caught.value)


def test_risk_evaluator_must_return_risk_enum(tmp_path: Path) -> None:
    spec = make_tool(risk_evaluator=lambda arguments, tool_context: "read_only")
    arguments = spec.validate_arguments({"value": "hello"})

    with pytest.raises(ToolError, match="invalid result"):
        PolicyEngine().evaluate(spec, arguments, context(tmp_path))
