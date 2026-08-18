import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple, Type

from pydantic import BaseModel, ValidationError

from ..errors import ToolError

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"
    EXTERNAL_EFFECT = "external_effect"
    DENIED = "denied"


class ArtifactReference(Protocol):
    artifact_id: str


class ArtifactWriter(Protocol):
    def create_text(
        self,
        text: str,
        media_type: str = "text/plain; charset=utf-8",
        sensitivity: str = "normal",
        ttl_seconds: int = 86400,
    ) -> ArtifactReference: ...


@dataclass(frozen=True)
class ToolContext:
    request_id: str
    working_directory: Path
    dry_run: bool = False
    deadline_monotonic: Optional[float] = None
    artifact_writer: Optional[ArtifactWriter] = None
    protected_paths: Tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.request_id):
            raise ValueError("tool request ID has an invalid format")
        try:
            working_directory = Path(self.working_directory).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ToolError("Tool working directory does not exist.") from exc
        if not working_directory.is_dir():
            raise ToolError("Tool working directory is not a directory.")
        if not isinstance(self.dry_run, bool):
            raise TypeError("tool dry_run flag must be a boolean")
        if self.deadline_monotonic is not None and (
            not isinstance(self.deadline_monotonic, (int, float))
            or isinstance(self.deadline_monotonic, bool)
        ):
            raise TypeError("tool deadline must be numeric or null")
        protected_paths = []
        for protected in self.protected_paths:
            try:
                protected_paths.append(Path(protected).resolve(strict=False))
            except (OSError, RuntimeError) as exc:
                raise ToolError("Unable to resolve protected tool path.") from exc
        object.__setattr__(self, "working_directory", working_directory)
        object.__setattr__(self, "protected_paths", tuple(protected_paths))

    def ensure_within_deadline(self) -> None:
        if (
            self.deadline_monotonic is not None
            and time.monotonic() >= self.deadline_monotonic
        ):
            raise ToolError("Tool execution deadline has expired.")


@dataclass(frozen=True)
class ToolPreview:
    summary: str
    details: Tuple[str, ...] = field(default_factory=tuple)
    affected_paths: Tuple[str, ...] = field(default_factory=tuple)
    external_destinations: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", tuple(self.details))
        object.__setattr__(self, "affected_paths", tuple(self.affected_paths))
        object.__setattr__(
            self,
            "external_destinations",
            tuple(self.external_destinations),
        )


@dataclass(frozen=True)
class ToolOutcome:
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    artifact_ids: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "artifact_ids", tuple(self.artifact_ids))


RiskEvaluator = Callable[[BaseModel, ToolContext], RiskLevel]
StateReader = Callable[[BaseModel, ToolContext], Mapping[str, Any]]
PreviewBuilder = Callable[[BaseModel, ToolContext], ToolPreview]
ToolExecutor = Callable[[BaseModel, ToolContext], ToolOutcome]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    argument_model: Type[BaseModel]
    minimum_risk: RiskLevel
    executor: ToolExecutor
    preview_builder: PreviewBuilder
    required_permissions: Tuple[str, ...] = field(default_factory=tuple)
    risk_evaluator: Optional[RiskEvaluator] = None
    state_reader: Optional[StateReader] = None
    timeout_seconds: float = 30.0
    max_output_bytes: int = 65536
    supports_dry_run: bool = False
    supports_rollback: bool = False

    def __post_init__(self) -> None:
        if not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("Tool name must be a dotted lowercase identifier.")
        if not self.description.strip():
            raise ValueError("Tool description must not be empty.")
        if not isinstance(self.argument_model, type) or not issubclass(
            self.argument_model, BaseModel
        ):
            raise TypeError("Tool argument_model must be a Pydantic model.")
        if self.timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive.")
        if self.max_output_bytes <= 0:
            raise ValueError("Tool output limit must be positive.")
        object.__setattr__(
            self, "required_permissions", tuple(self.required_permissions)
        )

    def validate_arguments(self, arguments: Mapping[str, Any]) -> BaseModel:
        try:
            return self.argument_model.model_validate(dict(arguments))
        except ValidationError as exc:
            details = []
            for item in exc.errors(include_input=False)[:5]:
                location = (
                    ".".join(str(part) for part in item.get("loc", ())) or "arguments"
                )
                details.append(
                    "{0} [{1}]: {2}".format(
                        location,
                        item.get("type", "invalid"),
                        item.get("msg", "invalid"),
                    )
                )
            raise ToolError(
                "Invalid arguments for {0}: {1}".format(self.name, "; ".join(details))
            ) from exc

    def preview(self, arguments: BaseModel, context: ToolContext) -> ToolPreview:
        try:
            preview = self.preview_builder(arguments, context)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                "Preview generation failed for {0}.".format(self.name)
            ) from exc
        if not isinstance(preview, ToolPreview):
            raise ToolError(
                "Preview generation returned an invalid result for {0}.".format(
                    self.name
                )
            )
        return preview

    def relevant_state(
        self, arguments: BaseModel, context: ToolContext
    ) -> Mapping[str, Any]:
        if self.state_reader is None:
            return {}
        try:
            return dict(self.state_reader(arguments, context))
        except Exception as exc:
            raise ToolError(
                "State validation failed for {0}.".format(self.name)
            ) from exc

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        context.ensure_within_deadline()
        return self.executor(arguments, context)
