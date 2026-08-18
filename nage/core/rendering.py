from dataclasses import dataclass
from typing import List

from ..errors import ExitCode
from ..protocol import ToolResultStatus
from .orchestrator import OrchestrationResult, OrchestrationStatus


@dataclass(frozen=True)
class RenderedResult:
    stdout: str
    stderr: str
    exit_code: ExitCode


def _terminal_safe(text: str) -> str:
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"}
        or (32 <= ord(character) < 127)
        or ord(character) >= 160
    )


class ResultRenderer:
    def render(self, result: OrchestrationResult) -> RenderedResult:
        message = result.decision.message.strip()
        non_success = [
            tool_result
            for tool_result in result.tool_results
            if tool_result.status != ToolResultStatus.SUCCESS
        ]
        if non_success:
            # Do not relay a model claim that contradicts a host-owned tool
            # failure, denial, or cancellation.
            message = "\n".join(
                tool_result.summary
                for tool_result in non_success
                if tool_result.summary
            )
        elif not message and result.tool_results:
            message = "\n".join(
                tool_result.summary
                for tool_result in result.tool_results
                if tool_result.summary
            )
        diagnostics: List[str] = [
            "[nage] memory: {0}".format(notice) for notice in result.memory_notices
        ]
        diagnostics.extend(
            "[nage] continuation: {0}".format(notice)
            for notice in result.continuation_notices
        )
        for tool_result in result.tool_results:
            diagnostics.extend(
                "[nage] warning: {0}".format(warning)
                for warning in tool_result.warnings
            )
            diagnostics.extend(
                "[nage] artifact: {0}".format(artifact_id)
                for artifact_id in tool_result.artifact_ids
            )
        return RenderedResult(
            stdout=_terminal_safe(message),
            stderr=_terminal_safe("\n".join(diagnostics)),
            exit_code=self._exit_code(result),
        )

    @staticmethod
    def _exit_code(result: OrchestrationResult) -> ExitCode:
        if result.status == OrchestrationStatus.NEEDS_INPUT:
            return ExitCode.NEEDS_INPUT
        if result.status == OrchestrationStatus.CANCELLED:
            return ExitCode.CANCELLED
        if result.status == OrchestrationStatus.POLICY_DENIED:
            return ExitCode.POLICY_DENIED
        if result.status == OrchestrationStatus.MODEL_ERROR:
            return ExitCode.PROVIDER
        if result.status == OrchestrationStatus.LIMIT_REACHED:
            return ExitCode.TOOL
        statuses = {item.status for item in result.tool_results}
        if ToolResultStatus.DENIED in statuses:
            return ExitCode.POLICY_DENIED
        if ToolResultStatus.CANCELLED in statuses:
            return ExitCode.CANCELLED
        if ToolResultStatus.FAILURE in statuses:
            return ExitCode.TOOL
        return ExitCode.SUCCESS
