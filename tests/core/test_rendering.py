from nage.core.orchestrator import OrchestrationResult, OrchestrationStatus
from nage.core.rendering import ResultRenderer
from nage.errors import ExitCode
from nage.protocol import (
    AssistantDecision,
    DecisionStatus,
    ToolResult,
    ToolResultStatus,
    Usage,
)


def orchestration(
    status=OrchestrationStatus.COMPLETED,
    message="done",
    tool_results=(),
):
    return OrchestrationResult(
        status=status,
        decision=AssistantDecision(status=DecisionStatus.FINAL, message=message),
        tool_results=tuple(tool_results),
        model_rounds=1,
        usage=Usage(),
    )


def test_renderer_keeps_final_content_on_stdout_and_diagnostics_on_stderr() -> None:
    rendered = ResultRenderer().render(
        orchestration(
            message="completed\x1b[31m",
            tool_results=(
                ToolResult(
                    call_id="call_1",
                    status=ToolResultStatus.SUCCESS,
                    summary="read complete",
                    warnings=["output was truncated"],
                    artifact_ids=["artifact-1"],
                ),
            ),
        )
    )

    assert rendered.stdout == "completed[31m"
    assert "warning" in rendered.stderr
    assert "artifact-1" in rendered.stderr
    assert rendered.exit_code == ExitCode.SUCCESS


def test_renderer_uses_tool_failure_exit_even_after_final_model_message() -> None:
    rendered = ResultRenderer().render(
        orchestration(
            tool_results=(
                ToolResult(
                    call_id="call_1",
                    status=ToolResultStatus.FAILURE,
                    summary="copy failed",
                ),
            )
        )
    )

    assert rendered.stdout == "copy failed"
    assert "done" not in rendered.stdout
    assert rendered.exit_code == ExitCode.TOOL


def test_renderer_maps_policy_and_cancellation_statuses() -> None:
    renderer = ResultRenderer()

    denied = renderer.render(orchestration(status=OrchestrationStatus.POLICY_DENIED))
    cancelled = renderer.render(orchestration(status=OrchestrationStatus.CANCELLED))

    assert denied.exit_code == ExitCode.POLICY_DENIED
    assert cancelled.exit_code == ExitCode.CANCELLED
