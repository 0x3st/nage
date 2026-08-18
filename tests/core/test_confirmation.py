from pathlib import Path

import pytest

from nage.core.confirmation import ActionPlan, ConfirmationService, TerminalConfirmer
from nage.tools import RiskLevel, ToolContext
from tests.tool_helpers import make_tool


def build_plan(tmp_path: Path, value: str = "hello", state=None) -> ActionPlan:
    spec = make_tool(risk=RiskLevel.LOCAL_WRITE)
    arguments = spec.validate_arguments({"value": value})
    return ActionPlan.build(
        call_id="call_1",
        spec=spec,
        arguments=arguments,
        risk=RiskLevel.LOCAL_WRITE,
        context=ToolContext("request-1", tmp_path),
        relevant_state=state or {},
    )


def test_plan_hash_is_stable_and_bound_to_arguments(tmp_path: Path) -> None:
    first = build_plan(tmp_path, "hello", {"version": 1})
    second = build_plan(tmp_path, "hello", {"version": 1})
    changed = build_plan(tmp_path, "goodbye", {"version": 1})

    assert first.plan_hash == second.plan_hash
    assert first.plan_hash != changed.plan_hash


def test_plan_match_detects_state_change(tmp_path: Path) -> None:
    spec = make_tool(risk=RiskLevel.LOCAL_WRITE)
    arguments = spec.validate_arguments({"value": "hello"})
    plan = ActionPlan.build(
        call_id="call_1",
        spec=spec,
        arguments=arguments,
        risk=RiskLevel.LOCAL_WRITE,
        context=ToolContext("request-1", tmp_path),
        relevant_state={"version": 1},
    )

    assert plan.matches(spec, arguments, RiskLevel.LOCAL_WRITE, {"version": 1})
    assert not plan.matches(spec, arguments, RiskLevel.LOCAL_WRITE, {"version": 2})


def test_plan_arguments_are_deeply_immutable(tmp_path: Path) -> None:
    spec = make_tool(risk=RiskLevel.LOCAL_WRITE)
    arguments = spec.validate_arguments({"value": "hello"})
    plan = ActionPlan.build(
        call_id="call_1",
        spec=spec,
        arguments=arguments,
        risk=RiskLevel.LOCAL_WRITE,
        context=ToolContext("request-1", tmp_path),
        relevant_state={"nested": {"items": [1, 2]}},
    )

    assert plan.relevant_state["nested"]["items"] == (1, 2)
    with pytest.raises(TypeError):
        plan.relevant_state["nested"]["new"] = "value"


def test_confirmation_service_does_not_cache_approval(tmp_path: Path) -> None:
    decisions = iter([True, False])
    service = ConfirmationService(lambda plan: next(decisions))
    plan = build_plan(tmp_path)

    assert service.confirm(plan) is True
    assert service.confirm(plan) is False


def test_terminal_confirmer_escapes_terminal_control_characters(
    tmp_path: Path,
) -> None:
    output = []
    confirmer = TerminalConfirmer(
        input_fn=lambda prompt: "n",
        output_fn=output.append,
    )

    confirmer(build_plan(tmp_path, value="name\x1b[31m"))

    rendered = "\n".join(output)
    assert "\x1b" not in rendered
    assert "\\u001b" in rendered


def test_terminal_confirmer_defaults_to_no_and_displays_scope(tmp_path: Path) -> None:
    output = []
    confirmer = TerminalConfirmer(
        input_fn=lambda prompt: "",
        output_fn=output.append,
    )
    plan = build_plan(tmp_path)

    assert confirmer(plan) is False
    assert any("sample.read" in line for line in output)
    assert any("local_write" in line for line in output)
