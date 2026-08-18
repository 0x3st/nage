import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from ..errors import ProtocolError, TransientProviderError
from .base import (
    BaseProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)


@dataclass(frozen=True)
class ProviderRetryPolicy:
    transient_retries: int = 2
    protocol_repairs: int = 1
    base_delay_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.transient_retries < 0:
            raise ValueError("transient_retries cannot be negative")
        if self.protocol_repairs < 0:
            raise ValueError("protocol_repairs cannot be negative")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds cannot be negative")


class RetryingProvider(BaseProvider):
    """Apply bounded retries before any proposed tool reaches execution."""

    REPAIR_MESSAGE = (
        "Your previous response did not match the required Nage protocol. "
        "Return one valid protocol 2.0 response only. Do not repeat or "
        "reinterpret the user's request."
    )

    def __init__(
        self,
        provider: BaseProvider,
        policy: Optional[ProviderRetryPolicy] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.policy = policy or ProviderRetryPolicy()
        self.sleeper = sleeper

    def complete(self, request: ProviderRequest) -> ProviderResult:
        current_request = request
        transient_failures = 0
        protocol_failures = 0

        while True:
            try:
                return self.provider.complete(current_request)
            except TransientProviderError:
                if transient_failures >= self.policy.transient_retries:
                    raise
                delay = self.policy.base_delay_seconds * (2**transient_failures)
                transient_failures += 1
                if delay:
                    self.sleeper(delay)
            except ProtocolError:
                if protocol_failures >= self.policy.protocol_repairs:
                    raise
                protocol_failures += 1
                repair = ProviderMessage(
                    role=MessageRole.SYSTEM,
                    content=self.REPAIR_MESSAGE,
                )
                current_request = replace(
                    request,
                    messages=request.messages + (repair,),
                )
