import hashlib
import hmac
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from ..errors import ToolError
from ..tools import RiskLevel, ToolContext, ToolPreview, ToolSpec


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ActionPlan:
    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    risk: RiskLevel
    preview: ToolPreview
    relevant_state: Mapping[str, Any] = field(default_factory=dict)
    plan_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _deep_freeze(self.arguments))
        object.__setattr__(self, "relevant_state", _deep_freeze(self.relevant_state))

    @classmethod
    def build(
        cls,
        call_id: str,
        spec: ToolSpec,
        arguments: BaseModel,
        risk: RiskLevel,
        context: ToolContext,
        relevant_state: Mapping[str, Any] = None,
    ) -> "ActionPlan":
        argument_data = arguments.model_dump(mode="json")
        state = dict(relevant_state or {})
        preview = spec.preview(arguments, context)
        plan_hash = cls.compute_hash(
            call_id=call_id,
            tool_name=spec.name,
            arguments=argument_data,
            risk=risk,
            relevant_state=state,
        )
        return cls(
            call_id=call_id,
            tool_name=spec.name,
            arguments=argument_data,
            risk=risk,
            preview=preview,
            relevant_state=state,
            plan_hash=plan_hash,
        )

    @classmethod
    def denied(
        cls,
        call_id: str,
        spec: ToolSpec,
        arguments: BaseModel,
        reason: str,
    ) -> "ActionPlan":
        argument_data = arguments.model_dump(mode="json")
        risk = RiskLevel.DENIED
        plan_hash = cls.compute_hash(
            call_id=call_id,
            tool_name=spec.name,
            arguments=argument_data,
            risk=risk,
            relevant_state={},
        )
        return cls(
            call_id=call_id,
            tool_name=spec.name,
            arguments=argument_data,
            risk=risk,
            preview=ToolPreview(summary=reason),
            relevant_state={},
            plan_hash=plan_hash,
        )

    @staticmethod
    def compute_hash(
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        risk: RiskLevel,
        relevant_state: Mapping[str, Any],
    ) -> str:
        payload = {
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "risk": risk.value,
            "relevant_state": dict(relevant_state),
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolError("Action plan is not canonical JSON.") from exc
        return hashlib.sha256(encoded).hexdigest()

    def matches(
        self,
        spec: ToolSpec,
        arguments: BaseModel,
        risk: RiskLevel,
        relevant_state: Mapping[str, Any] = None,
    ) -> bool:
        candidate = self.compute_hash(
            call_id=self.call_id,
            tool_name=spec.name,
            arguments=arguments.model_dump(mode="json"),
            risk=risk,
            relevant_state=dict(relevant_state or {}),
        )
        return hmac.compare_digest(self.plan_hash, candidate)


ConfirmationCallback = Callable[[ActionPlan], bool]


def _terminal_text(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)[1:-1]


class ConfirmationService:
    def __init__(self, callback: ConfirmationCallback) -> None:
        self.callback = callback

    def confirm(self, plan: ActionPlan) -> bool:
        try:
            return bool(self.callback(plan))
        except (EOFError, KeyboardInterrupt) as exc:
            raise ToolError("Confirmation was cancelled.") from exc
        except Exception as exc:
            raise ToolError("Confirmation failed.") from exc


class TerminalConfirmer:
    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.input_fn = input_fn
        self.output_fn = output_fn

    def __call__(self, plan: ActionPlan) -> bool:
        self.output_fn(
            "[nage] Planned action: {0}".format(_terminal_text(plan.preview.summary))
        )
        self.output_fn("[nage] Tool: {0}".format(_terminal_text(plan.tool_name)))
        self.output_fn("[nage] Risk: {0}".format(_terminal_text(plan.risk.value)))
        for detail in plan.preview.details:
            self.output_fn("[nage] - {0}".format(_terminal_text(detail)))
        for path in plan.preview.affected_paths:
            self.output_fn("[nage] Path: {0}".format(_terminal_text(path)))
        for destination in plan.preview.external_destinations:
            self.output_fn(
                "[nage] Destination: {0}".format(_terminal_text(destination))
            )
        answer = self.input_fn("Continue? [y/N] ").strip().lower()
        return answer in {"y", "yes"}
