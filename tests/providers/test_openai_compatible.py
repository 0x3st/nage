import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from nage.errors import ProtocolError, ProviderError
from nage.protocol import DecisionStatus, ToolCall
from nage.providers import (
    MessageRole,
    OpenAICompatibleProvider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderRequest,
    ProviderToolDefinition,
    ResponseFormatMode,
)


class FakeCompletions:
    def __init__(self, response: Any = None, error: Exception = None) -> None:
        self.response = response
        self.error = error
        self.parameters: Dict[str, Any] = {}

    def create(self, **parameters: Any) -> Any:
        self.parameters = parameters
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response: Any = None, error: Exception = None) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response=response, error=error)
        )


def completion(content: str, tool_calls=None, usage=None) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ],
        usage=usage,
    )


def request_with_tool() -> ProviderRequest:
    return ProviderRequest(
        model="test-model",
        messages=(ProviderMessage(MessageRole.USER, "list files"),),
        tools=(
            ProviderToolDefinition(
                name="files.list",
                description="List files",
                parameters={"type": "object", "properties": {}},
            ),
        ),
    )


def test_provider_parses_final_protocol_response() -> None:
    content = json.dumps(
        {
            "protocol_version": "2.0",
            "status": "final",
            "message": "Done",
            "tool_calls": [],
            "memory_proposals": [],
            "continuation": None,
        }
    )
    client = FakeClient(
        completion(
            content,
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )
    )
    provider = OpenAICompatibleProvider("key", "https://example.test", client=client)

    result = provider.complete(request_with_tool())

    assert result.decision.status == DecisionStatus.FINAL
    assert result.usage.total_tokens == 15
    assert client.chat.completions.parameters["stream"] is False
    assert client.chat.completions.parameters["tool_choice"] == "auto"
    assert client.chat.completions.parameters["tools"][0]["function"]["name"] == (
        "files_list"
    )
    assert "response_format" not in client.chat.completions.parameters


def test_provider_normalizes_native_tool_call() -> None:
    raw_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="files_list", arguments='{"root":"."}'),
    )
    client = FakeClient(completion("", tool_calls=[raw_call]))
    provider = OpenAICompatibleProvider("key", "https://example.test", client=client)

    result = provider.complete(request_with_tool())

    assert result.decision.status == DecisionStatus.TOOL_CALL
    assert result.decision.tool_calls == [
        ToolCall(id="call_1", name="files.list", arguments={"root": "."})
    ]


def test_provider_accepts_plain_text_final_when_tools_are_available() -> None:
    client = FakeClient(completion("You are on macOS."))
    provider = OpenAICompatibleProvider("key", "https://example.test", client=client)

    result = provider.complete(request_with_tool())

    assert result.decision.status == DecisionStatus.FINAL
    assert result.decision.message == "You are on macOS."


def test_provider_merges_native_calls_with_json_metadata() -> None:
    raw_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="files_list", arguments="{}"),
    )
    content = json.dumps(
        {
            "protocol_version": "2.0",
            "status": "tool_call",
            "message": "I will inspect the directory.",
            "tool_calls": [],
            "memory_proposals": [],
            "continuation": None,
        }
    )
    provider = OpenAICompatibleProvider(
        "key",
        "https://example.test",
        client=FakeClient(completion(content, tool_calls=[raw_call])),
    )

    result = provider.complete(request_with_tool())

    assert result.decision.message == "I will inspect the directory."
    assert result.decision.tool_calls[0].id == "call_1"


def test_provider_rejects_non_object_native_arguments() -> None:
    raw_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="files_list", arguments="[]"),
    )
    provider = OpenAICompatibleProvider(
        "key",
        "https://example.test",
        client=FakeClient(completion("", tool_calls=[raw_call])),
    )

    with pytest.raises(ProtocolError, match="JSON object"):
        provider.complete(request_with_tool())


def test_provider_bounds_plain_text_attached_to_native_call() -> None:
    raw_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="files_list", arguments="{}"),
    )
    provider = OpenAICompatibleProvider(
        "key",
        "https://example.test",
        client=FakeClient(completion("x" * 40000, tool_calls=[raw_call])),
    )

    with pytest.raises(ProtocolError, match="message"):
        provider.complete(request_with_tool())


def test_provider_rejects_invalid_usage_metadata() -> None:
    content = json.dumps({"protocol_version": "2.0", "status": "final"})
    provider = OpenAICompatibleProvider(
        "key",
        "https://example.test",
        client=FakeClient(
            completion(
                content,
                usage=SimpleNamespace(
                    prompt_tokens=-1,
                    completion_tokens=1,
                    total_tokens=0,
                ),
            )
        ),
    )

    with pytest.raises(ProtocolError, match="usage metadata"):
        provider.complete(request_with_tool())


def test_provider_uses_json_schema_mode() -> None:
    content = json.dumps({"protocol_version": "2.0", "status": "final"})
    client = FakeClient(completion(content))
    provider = OpenAICompatibleProvider(
        "key",
        "https://example.test",
        capabilities=ProviderCapabilities(
            native_tools=False,
            response_format=ResponseFormatMode.JSON_SCHEMA,
        ),
        client=client,
    )

    provider.complete(request_with_tool())

    parameters = client.chat.completions.parameters
    assert "tools" not in parameters
    assert parameters["response_format"]["type"] == "json_schema"
    assert parameters["response_format"]["json_schema"]["strict"] is True


def test_provider_serializes_assistant_and_tool_messages() -> None:
    content = json.dumps({"protocol_version": "2.0", "status": "final"})
    client = FakeClient(completion(content))
    provider = OpenAICompatibleProvider("key", "https://example.test", client=client)
    previous_call = ToolCall(id="call_1", name="files.list", arguments={})
    request = ProviderRequest(
        model="test-model",
        messages=(
            ProviderMessage(
                MessageRole.ASSISTANT,
                "",
                tool_calls=(previous_call,),
            ),
            ProviderMessage(
                MessageRole.TOOL,
                '{"status":"success"}',
                tool_call_id="call_1",
            ),
        ),
    )

    provider.complete(request)

    messages = client.chat.completions.parameters["messages"]
    assert messages[0]["tool_calls"][0]["function"]["name"] == "files_list"
    assert messages[1]["tool_call_id"] == "call_1"


def test_provider_normalizes_client_errors_without_message_details() -> None:
    client = FakeClient(error=RuntimeError("contains secret detail"))
    provider = OpenAICompatibleProvider("key", "https://example.test", client=client)

    with pytest.raises(ProviderError) as caught:
        provider.complete(request_with_tool())

    assert "RuntimeError" in str(caught.value)
    assert "secret detail" not in str(caught.value)
