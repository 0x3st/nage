from typing import Callable, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from nage.tools import (
    RiskLevel,
    ToolContext,
    ToolOutcome,
    ToolPreview,
    ToolSpec,
)


class SampleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def make_tool(
    name: str = "sample.read",
    risk: RiskLevel = RiskLevel.READ_ONLY,
    executor: Optional[Callable] = None,
    required_permissions: Tuple[str, ...] = (),
    risk_evaluator: Optional[Callable] = None,
    state_reader: Optional[Callable] = None,
    max_output_bytes: int = 65536,
) -> ToolSpec:
    def default_executor(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        return ToolOutcome(
            summary="handled {0}".format(arguments.value),
            data={"value": arguments.value},
        )

    def preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        return ToolPreview(
            summary="Handle {0}".format(arguments.value),
            details=("value={0}".format(arguments.value),),
        )

    return ToolSpec(
        name=name,
        description="A synthetic tool used by tests.",
        argument_model=SampleArguments,
        minimum_risk=risk,
        executor=executor or default_executor,
        preview_builder=preview,
        required_permissions=required_permissions,
        risk_evaluator=risk_evaluator,
        state_reader=state_reader,
        max_output_bytes=max_output_bytes,
    )
