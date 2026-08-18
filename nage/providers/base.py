from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from ..protocol import AssistantDecision, ToolCall, Usage


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ProviderMessage:
    role: MessageRole
    content: str
    tool_calls: Tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if self.role == MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("Tool messages require a tool_call_id.")
        if self.role != MessageRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid for tool messages.")
        if self.tool_calls and self.role != MessageRole.ASSISTANT:
            raise ValueError("Only assistant messages can contain tool calls.")


@dataclass(frozen=True)
class ProviderToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", dict(self.parameters))


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    messages: Tuple[ProviderMessage, ...]
    tools: Tuple[ProviderToolDefinition, ...] = field(default_factory=tuple)
    response_schema: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Provider model must not be empty.")
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))
        if self.response_schema is not None:
            object.__setattr__(self, "response_schema", dict(self.response_schema))


@dataclass(frozen=True)
class ProviderResult:
    decision: AssistantDecision
    assistant_message: ProviderMessage
    usage: Usage = field(default_factory=Usage)


class BaseProvider(ABC):
    @abstractmethod
    def complete(self, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError
