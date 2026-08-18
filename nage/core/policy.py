from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Iterable

from pydantic import BaseModel

from ..errors import ToolError
from ..tools import RiskLevel, ToolContext, ToolSpec


class PolicyDecisionKind(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    kind: PolicyDecisionKind
    risk: RiskLevel
    reason: str


_RISK_ORDER = {
    RiskLevel.READ_ONLY: 0,
    RiskLevel.LOCAL_WRITE: 1,
    RiskLevel.DESTRUCTIVE: 2,
    RiskLevel.EXTERNAL_EFFECT: 3,
    RiskLevel.PRIVILEGED: 4,
    RiskLevel.DENIED: 5,
}


class PolicyEngine:
    def __init__(self, granted_permissions: Iterable[str] = ()) -> None:
        self.granted_permissions: FrozenSet[str] = frozenset(granted_permissions)

    def evaluate(
        self,
        spec: ToolSpec,
        arguments: BaseModel,
        context: ToolContext,
    ) -> PolicyDecision:
        missing_permissions = set(spec.required_permissions).difference(
            self.granted_permissions
        )
        if missing_permissions:
            return PolicyDecision(
                kind=PolicyDecisionKind.DENY,
                risk=RiskLevel.DENIED,
                reason="Missing required permission: {0}".format(
                    ", ".join(sorted(missing_permissions))
                ),
            )

        risk = spec.minimum_risk
        if spec.risk_evaluator is not None:
            try:
                dynamic_risk = spec.risk_evaluator(arguments, context)
            except Exception as exc:
                raise ToolError(
                    "Risk evaluation failed for {0}.".format(spec.name)
                ) from exc
            if not isinstance(dynamic_risk, RiskLevel):
                raise ToolError(
                    "Risk evaluation returned an invalid result for {0}.".format(
                        spec.name
                    )
                )
            if _RISK_ORDER[dynamic_risk] > _RISK_ORDER[risk]:
                risk = dynamic_risk

        if risk == RiskLevel.DENIED:
            return PolicyDecision(
                kind=PolicyDecisionKind.DENY,
                risk=risk,
                reason="Tool policy denies this operation.",
            )
        if risk == RiskLevel.READ_ONLY:
            return PolicyDecision(
                kind=PolicyDecisionKind.ALLOW,
                risk=risk,
                reason="Read-only operation is allowed.",
            )
        return PolicyDecision(
            kind=PolicyDecisionKind.CONFIRM,
            risk=risk,
            reason="This operation has side effects and requires confirmation.",
        )
