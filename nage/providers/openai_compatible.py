import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import ValidationError

from ..errors import ProtocolError, ProviderError, TransientProviderError
from ..protocol import (
    AssistantDecision,
    DecisionStatus,
    ToolCall,
    Usage,
    decision_json_schema,
    parse_assistant_decision,
)
from .base import (
    BaseProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)


class ResponseFormatMode(str, Enum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    NONE = "none"


@dataclass(frozen=True)
class ProviderCapabilities:
    native_tools: bool = True
    response_format: ResponseFormatMode = ResponseFormatMode.JSON_OBJECT


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        capabilities: Optional[ProviderCapabilities] = None,
        client: Optional[Any] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if client is None and not api_key:
            raise ProviderError("An API key is required for the provider.")
        if timeout_seconds <= 0:
            raise ProviderError("Provider timeout must be positive.")
        self.capabilities = capabilities or ProviderCapabilities()
        self.client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=timeout_seconds,
        )

    def complete(self, request: ProviderRequest) -> ProviderResult:
        wire_to_canonical, canonical_to_wire = self._tool_name_maps(request.tools)
        parameters: Dict[str, Any] = {
            "model": request.model,
            "messages": [
                self._message_to_openai(message, canonical_to_wire=canonical_to_wire)
                for message in request.messages
            ],
            "stream": False,
        }

        using_native_tools = bool(request.tools) and self.capabilities.native_tools
        if using_native_tools:
            parameters["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": canonical_to_wire[tool.name],
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                    },
                }
                for tool in request.tools
            ]
            parameters["tool_choice"] = "auto"

        # Some OpenAI-compatible endpoints reject response_format together
        # with tools, or require plain-text finals after tool loops.
        if not using_native_tools:
            response_format = self._response_format(request.response_schema)
            if response_format is not None:
                parameters["response_format"] = response_format

        try:
            completion = self.client.chat.completions.create(**parameters)
        except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
            raise TransientProviderError(
                "Provider request failed temporarily: {0}".format(type(exc).__name__)
            ) from exc
        except APIStatusError as exc:
            error_type = type(exc).__name__
            if exc.status_code in {408, 409, 429} or exc.status_code >= 500:
                raise TransientProviderError(
                    "Provider request failed temporarily: {0}".format(error_type)
                ) from exc
            raise ProviderError(
                "Provider request failed: {0}".format(error_type)
            ) from exc
        except OpenAIError as exc:
            raise ProviderError(
                "Provider request failed: {0}".format(type(exc).__name__)
            ) from exc
        except Exception as exc:
            # Injected and third-party OpenAI-compatible clients do not share
            # one stable exception hierarchy. Normalize without including
            # request content or credentials in the error.
            raise ProviderError(
                "Provider request failed: {0}".format(type(exc).__name__)
            ) from exc

        try:
            choice = completion.choices[0]
            raw_message = choice.message
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProtocolError("Provider returned no assistant choice.") from exc

        native_calls = self._native_tool_calls(
            getattr(raw_message, "tool_calls", None),
            wire_to_canonical=wire_to_canonical,
        )
        content = getattr(raw_message, "content", None) or ""
        decision = self._normalize_decision(content, native_calls)
        assistant_message = ProviderMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tuple(native_calls),
        )
        return ProviderResult(
            decision=decision,
            assistant_message=assistant_message,
            usage=self._usage(getattr(completion, "usage", None)),
        )

    def _response_format(
        self, response_schema: Optional[Mapping[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if self.capabilities.response_format == ResponseFormatMode.NONE:
            return None
        if self.capabilities.response_format == ResponseFormatMode.JSON_OBJECT:
            return {"type": "json_object"}
        schema = dict(response_schema or decision_json_schema())
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "nage_assistant_decision",
                "strict": True,
                "schema": schema,
            },
        }

    @staticmethod
    def _tool_name_maps(
        tools: Tuple[Any, ...],
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        wire_to_canonical: Dict[str, str] = {}
        canonical_to_wire: Dict[str, str] = {}
        for tool in tools:
            wire_name = tool.name.replace(".", "_")
            existing = wire_to_canonical.get(wire_name)
            if existing is not None and existing != tool.name:
                raise ProviderError(
                    "Tool names collide after provider encoding: {0}".format(wire_name)
                )
            wire_to_canonical[wire_name] = tool.name
            canonical_to_wire[tool.name] = wire_name
        return wire_to_canonical, canonical_to_wire

    @staticmethod
    def _message_to_openai(
        message: ProviderMessage,
        canonical_to_wire: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "role": message.role.value,
            "content": message.content,
        }
        if message.tool_call_id is not None:
            result["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            mapping = dict(canonical_to_wire or {})
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": mapping.get(call.name, call.name.replace(".", "_")),
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        return result

    @staticmethod
    def _native_tool_calls(
        raw_calls: Any,
        wire_to_canonical: Optional[Mapping[str, str]] = None,
    ) -> List[ToolCall]:
        calls: List[ToolCall] = []
        mapping = dict(wire_to_canonical or {})
        for raw_call in raw_calls or []:
            try:
                function = raw_call.function
                arguments = json.loads(function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ProtocolError("Native tool arguments must be a JSON object.")
                wire_name = function.name
                calls.append(
                    ToolCall(
                        id=raw_call.id,
                        name=mapping.get(wire_name, wire_name),
                        arguments=arguments,
                    )
                )
            except json.JSONDecodeError as exc:
                raise ProtocolError(
                    "Native tool arguments are not valid JSON."
                ) from exc
            except (AttributeError, TypeError, ValidationError) as exc:
                raise ProtocolError("Native tool call is malformed.") from exc
        return calls

    @staticmethod
    def _normalize_decision(
        content: str, native_calls: List[ToolCall]
    ) -> AssistantDecision:
        if not native_calls:
            if not content.strip():
                raise ProtocolError("Provider returned an empty response.")
            try:
                return parse_assistant_decision(content)
            except ProtocolError:
                # Tool-capable OpenAI-compatible endpoints often return a plain
                # final answer instead of protocol JSON when no tool is needed.
                return parse_assistant_decision(
                    {
                        "protocol_version": "2.0",
                        "status": DecisionStatus.FINAL.value,
                        "message": content,
                        "tool_calls": [],
                        "memory_proposals": [],
                        "continuation": None,
                    }
                )

        if not content.strip():
            return parse_assistant_decision(
                {
                    "protocol_version": "2.0",
                    "status": DecisionStatus.TOOL_CALL.value,
                    "message": "",
                    "tool_calls": [
                        call.model_dump(mode="json") for call in native_calls
                    ],
                    "memory_proposals": [],
                    "continuation": None,
                }
            )

        try:
            metadata = json.loads(content)
        except json.JSONDecodeError:
            return parse_assistant_decision(
                {
                    "protocol_version": "2.0",
                    "status": DecisionStatus.TOOL_CALL.value,
                    "message": content,
                    "tool_calls": [
                        call.model_dump(mode="json") for call in native_calls
                    ],
                    "memory_proposals": [],
                    "continuation": None,
                }
            )
        if not isinstance(metadata, dict):
            raise ProtocolError("Native tool metadata must be a JSON object.")

        metadata["protocol_version"] = "2.0"
        metadata["status"] = DecisionStatus.TOOL_CALL.value
        metadata["tool_calls"] = [call.model_dump(mode="json") for call in native_calls]
        metadata.setdefault("message", "")
        metadata.setdefault("memory_proposals", [])
        metadata["continuation"] = None
        return parse_assistant_decision(metadata)

    @staticmethod
    def _usage(raw_usage: Any) -> Usage:
        if raw_usage is None:
            return Usage()
        try:
            return Usage(
                input_tokens=getattr(raw_usage, "prompt_tokens", None),
                output_tokens=getattr(raw_usage, "completion_tokens", None),
                total_tokens=getattr(raw_usage, "total_tokens", None),
            )
        except ValidationError as exc:
            raise ProtocolError("Provider returned invalid usage metadata.") from exc
