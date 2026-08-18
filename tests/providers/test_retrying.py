import pytest

from nage.errors import ProtocolError, ProviderError, TransientProviderError
from nage.protocol import AssistantDecision, DecisionStatus
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)
from nage.providers.retrying import ProviderRetryPolicy, RetryingProvider


def make_request() -> ProviderRequest:
    return ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "hello"),),
    )


def make_result() -> ProviderResult:
    return ProviderResult(
        decision=AssistantDecision(
            status=DecisionStatus.FINAL,
            message="done",
        ),
        assistant_message=ProviderMessage(MessageRole.ASSISTANT, "done"),
    )


def test_retrying_provider_repairs_protocol_once() -> None:
    inner = FakeProvider([ProtocolError("bad response"), make_result()])
    provider = RetryingProvider(
        inner,
        policy=ProviderRetryPolicy(
            transient_retries=0,
            protocol_repairs=1,
            base_delay_seconds=0,
        ),
    )

    result = provider.complete(make_request())

    assert result.decision.message == "done"
    assert len(inner.requests) == 2
    repair = inner.requests[1].messages[-1]
    assert repair.role == MessageRole.SYSTEM
    assert "protocol 2.0" in repair.content
    assert inner.requests[1].messages[:-1] == inner.requests[0].messages


def test_retrying_provider_limits_protocol_repairs() -> None:
    inner = FakeProvider([ProtocolError("first"), ProtocolError("second")])
    provider = RetryingProvider(
        inner,
        policy=ProviderRetryPolicy(protocol_repairs=1),
        sleeper=lambda _: None,
    )

    with pytest.raises(ProtocolError, match="second"):
        provider.complete(make_request())

    assert len(inner.requests) == 2


def test_retrying_provider_retries_transient_failures_with_backoff() -> None:
    inner = FakeProvider(
        [
            TransientProviderError("temporary"),
            TransientProviderError("temporary"),
            make_result(),
        ]
    )
    delays = []
    provider = RetryingProvider(
        inner,
        policy=ProviderRetryPolicy(
            transient_retries=2,
            protocol_repairs=0,
            base_delay_seconds=0.5,
        ),
        sleeper=delays.append,
    )

    assert provider.complete(make_request()).decision.message == "done"
    assert delays == [0.5, 1.0]
    assert len(inner.requests) == 3


def test_retrying_provider_does_not_retry_permanent_failure() -> None:
    inner = FakeProvider([ProviderError("permanent"), make_result()])
    provider = RetryingProvider(inner, sleeper=lambda _: None)

    with pytest.raises(ProviderError, match="permanent"):
        provider.complete(make_request())

    assert len(inner.requests) == 1
