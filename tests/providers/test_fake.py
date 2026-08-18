import pytest

from nage.errors import ProviderError
from nage.protocol import AssistantDecision, DecisionStatus
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)


def make_result(message: str = "done") -> ProviderResult:
    return ProviderResult(
        decision=AssistantDecision(
            status=DecisionStatus.FINAL,
            message=message,
        ),
        assistant_message=ProviderMessage(
            role=MessageRole.ASSISTANT,
            content=message,
        ),
    )


def test_fake_provider_records_requests_and_returns_in_order() -> None:
    provider = FakeProvider([make_result("one"), make_result("two")])
    request = ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "hello"),),
    )

    assert provider.complete(request).decision.message == "one"
    assert provider.complete(request).decision.message == "two"
    assert provider.requests == [request, request]


def test_fake_provider_can_raise_queued_error() -> None:
    provider = FakeProvider([ProviderError("offline")])
    request = ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "hello"),),
    )

    with pytest.raises(ProviderError, match="offline"):
        provider.complete(request)


def test_fake_provider_rejects_empty_queue() -> None:
    provider = FakeProvider([])
    request = ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "hello"),),
    )

    with pytest.raises(ProviderError, match="no queued"):
        provider.complete(request)
