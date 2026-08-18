import re
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "2.0"
_TOOL_CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_INTENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionStatus(str, Enum):
    FINAL = "final"
    NEEDS_INPUT = "needs_input"
    TOOL_CALL = "tool_call"
    ERROR = "error"


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    CANCELLED = "cancelled"


class MemoryOperation(str, Enum):
    UPSERT = "upsert"
    SUPERSEDE = "supersede"
    FORGET = "forget"


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    INSTRUCTION = "instruction"
    PROFILE = "profile"
    PROJECT = "project"
    FACT = "fact"
    UNCATEGORIZED = "uncategorized"


class MemoryScope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    REPOSITORY = "repository"


class EvidenceType(str, Enum):
    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    STRONG_IMPLICIT_PREFERENCE = "strong_implicit_preference"
    REPEATED_USER_EVIDENCE = "repeated_user_evidence"
    MODEL_INFERENCE = "model_inference"
    UNTRUSTED_CONTEXT = "untrusted_context"


class Sensitivity(str, Enum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class ToolCall(StrictModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _TOOL_CALL_ID_PATTERN.fullmatch(value):
            raise ValueError("tool call ID has an invalid format")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("tool name must be a dotted lowercase identifier")
        return value


class MemoryProposal(StrictModel):
    operation: MemoryOperation
    content: str = Field(min_length=1, max_length=2000)
    kind: MemoryKind
    scope: MemoryScope = MemoryScope.GLOBAL
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: EvidenceType
    sensitivity: Sensitivity = Sensitivity.NORMAL
    supersedes: List[str] = Field(default_factory=list, max_length=16)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("memory content cannot be blank")
        return normalized

    @field_validator("supersedes")
    @classmethod
    def validate_supersedes(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("supersedes contains duplicate memory IDs")
        if any(not _TOOL_CALL_ID_PATTERN.fullmatch(item) for item in value):
            raise ValueError("supersedes contains an invalid memory ID")
        return value

    @model_validator(mode="after")
    def validate_operation_targets(self) -> "MemoryProposal":
        if self.operation == MemoryOperation.SUPERSEDE and not self.supersedes:
            raise ValueError("supersede operation requires target memory IDs")
        if self.operation != MemoryOperation.SUPERSEDE and self.supersedes:
            raise ValueError("supersedes IDs require the supersede operation")
        return self


class ContinuationProposal(StrictModel):
    label: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1, max_length=120)
    known: Dict[str, Any] = Field(default_factory=dict)
    missing: List[str] = Field(min_length=1, max_length=32)
    sensitivity: Sensitivity = Sensitivity.NORMAL

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("continuation label cannot be blank")
        return normalized

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, value: str) -> str:
        if not _INTENT_PATTERN.fullmatch(value):
            raise ValueError("continuation intent has an invalid format")
        return value

    @field_validator("missing")
    @classmethod
    def validate_missing(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("missing fields contain duplicates")
        for item in value:
            if not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", item):
                raise ValueError("missing field has an invalid format")
        return value


class AssistantDecision(StrictModel):
    protocol_version: Literal["2.0"] = PROTOCOL_VERSION
    status: DecisionStatus
    message: str = Field(default="", max_length=32768)
    tool_calls: List[ToolCall] = Field(default_factory=list, max_length=8)
    memory_proposals: List[MemoryProposal] = Field(default_factory=list, max_length=8)
    continuation: Optional[ContinuationProposal] = None

    @model_validator(mode="after")
    def validate_consistency(self) -> "AssistantDecision":
        call_ids = [call.id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call IDs must be unique")

        if self.status == DecisionStatus.TOOL_CALL and not self.tool_calls:
            raise ValueError("tool_call status requires at least one tool call")
        if self.status != DecisionStatus.TOOL_CALL and self.tool_calls:
            raise ValueError("tool calls require tool_call status")

        if self.status == DecisionStatus.NEEDS_INPUT:
            if self.continuation is None:
                raise ValueError("needs_input status requires a continuation proposal")
        elif self.continuation is not None:
            raise ValueError("continuation is only valid for needs_input status")

        if self.status == DecisionStatus.ERROR and self.memory_proposals:
            raise ValueError("error responses cannot propose memory changes")
        return self


class ToolResult(StrictModel):
    call_id: str
    status: ToolResultStatus
    summary: str = Field(default="", max_length=4096)
    data: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list, max_length=32)
    artifact_ids: List[str] = Field(default_factory=list, max_length=32)

    @field_validator("call_id")
    @classmethod
    def validate_call_id(cls, value: str) -> str:
        if not _TOOL_CALL_ID_PATTERN.fullmatch(value):
            raise ValueError("tool result call ID has an invalid format")
        return value


class Usage(StrictModel):
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
