import hashlib
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ..errors import ToolError
from ..memory import contains_highly_sensitive_data, contains_secret
from ..protocol import MemoryKind
from ..retrieval import detect_repository_root
from ..storage import MemoryRecord, MemoryStore
from .base import RiskLevel, ToolContext, ToolOutcome, ToolPreview, ToolSpec
from .output import bound_json_items, store_json_artifact


class MemoryToolScope(str, Enum):
    ALL = "all"
    GLOBAL = "global"
    WORKSPACE = "workspace"
    REPOSITORY = "repository"


class InspectMemoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(
        default="active", pattern="^(active|candidate|superseded|forgotten)$"
    )
    scope: MemoryToolScope = MemoryToolScope.ALL
    limit: int = Field(default=50, ge=1, le=100)


class SearchMemoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=50)


class MemoryIdArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class CorrectMemoryArguments(MemoryIdArguments):
    content: str = Field(min_length=1, max_length=2000)
    kind: Optional[MemoryKind] = None


class ClearMemoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryToolScope = MemoryToolScope.ALL


class ExportMemoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryToolScope = MemoryToolScope.ALL
    include_inactive: bool = False


def _scope(
    value: MemoryToolScope,
    context: ToolContext,
) -> Tuple[Optional[str], Optional[str]]:
    if value == MemoryToolScope.ALL:
        return None, None
    if value == MemoryToolScope.GLOBAL:
        return "global", ""
    if value == MemoryToolScope.WORKSPACE:
        return "workspace", str(context.working_directory)
    root = detect_repository_root(context.working_directory)
    if root is None:
        raise ToolError("Current directory is not inside a repository scope.")
    return "repository", str(root)


def _record_mapping(record: MemoryRecord) -> Mapping[str, Any]:
    content = record.content
    if record.sensitivity != "normal":
        content = "[sensitive memory content hidden]"
    return {
        "id": record.id,
        "kind": record.kind,
        "content": content,
        "scope_type": record.scope_type,
        "scope_key": record.scope_key,
        "source_type": record.source_type,
        "confidence": record.confidence,
        "evidence_count": record.evidence_count,
        "sensitivity": record.sensitivity,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
        "supersedes_id": record.supersedes_id,
    }


def _record_state(store: MemoryStore, memory_id: str) -> Mapping[str, Any]:
    record = store.get(memory_id)
    if record is None:
        return {"exists": False}
    return {
        "exists": True,
        "id": record.id,
        "status": record.status,
        "updated_at": record.updated_at,
        "content_sha256": hashlib.sha256(record.content.encode("utf-8")).hexdigest(),
    }


def build_memory_tools(store: MemoryStore) -> Tuple[ToolSpec, ...]:
    def correction_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
        if contains_secret(arguments.content) or contains_highly_sensitive_data(
            arguments.content
        ):
            return RiskLevel.DENIED
        return RiskLevel.LOCAL_WRITE

    def activation_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
        record = store.get(arguments.memory_id)
        if (
            record is None
            or record.status != "candidate"
            or record.sensitivity != "normal"
        ):
            return RiskLevel.DENIED
        return RiskLevel.LOCAL_WRITE

    def inspect_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        return ToolPreview(
            summary="Inspect local memories",
            details=(
                "status={0}".format(arguments.status),
                "scope={0}".format(arguments.scope.value),
                "limit={0}".format(arguments.limit),
            ),
        )

    def inspect_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        scope_type, scope_key = _scope(arguments.scope, context)
        records = store.list(
            statuses=(arguments.status,),
            scope_type=scope_type,
            scope_key=scope_key,
            limit=arguments.limit + 1,
        )
        values = [_record_mapping(record) for record in records]
        selected, output_truncated = bound_json_items(values, arguments.limit, 32768)
        truncated = output_truncated or len(records) > arguments.limit
        artifact_ids = ()
        warnings = ()
        if truncated:
            artifact_ids, warnings = store_json_artifact(
                context,
                {"memories": values},
                sensitivity="sensitive",
            )
        return ToolOutcome(
            summary="Inspected {0} memory record(s).".format(len(selected)),
            data={"memories": selected, "truncated": truncated},
            warnings=warnings,
            artifact_ids=artifact_ids,
        )

    def search_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        return ToolPreview(
            summary="Search active memories for {0!r}".format(arguments.query),
            details=("limit={0}".format(arguments.limit),),
        )

    def search_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        root = detect_repository_root(context.working_directory)
        records = store.search(
            arguments.query,
            workspace_key=str(context.working_directory),
            repository_key=None if root is None else str(root),
            limit=arguments.limit + 1,
        )
        values = [_record_mapping(record) for record in records]
        selected, output_truncated = bound_json_items(values, arguments.limit, 32768)
        truncated = output_truncated or len(records) > arguments.limit
        artifact_ids = ()
        warnings = ()
        if truncated:
            artifact_ids, warnings = store_json_artifact(
                context,
                {"memories": values},
                sensitivity="sensitive",
            )
        return ToolOutcome(
            summary="Found {0} active memory record(s).".format(len(selected)),
            data={"memories": selected, "truncated": truncated},
            warnings=warnings,
            artifact_ids=artifact_ids,
        )

    def correct_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        record = store.get(arguments.memory_id)
        if record is None:
            raise ToolError("Memory does not exist.")
        return ToolPreview(
            summary="Correct memory {0}".format(record.id),
            details=(
                "new kind={0}".format(
                    record.kind if arguments.kind is None else arguments.kind.value
                ),
                "new content={0}".format(arguments.content),
            ),
        )

    def memory_state(arguments: BaseModel, context: ToolContext) -> Mapping[str, Any]:
        return _record_state(store, arguments.memory_id)

    def correct_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        if contains_secret(arguments.content) or contains_highly_sensitive_data(
            arguments.content
        ):
            raise ToolError(
                "Refusing to store secret or highly sensitive memory content."
            )
        result = store.correct(
            arguments.memory_id,
            arguments.content,
            kind=None if arguments.kind is None else arguments.kind.value,
            source_reference=context.request_id,
            source_hash=hashlib.sha256(arguments.content.encode("utf-8")).hexdigest(),
        )
        return ToolOutcome(
            summary="Corrected memory {0}.".format(arguments.memory_id),
            data={"memory": _record_mapping(result.record)},
        )

    def forget_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        record = store.get(arguments.memory_id)
        if record is None:
            raise ToolError("Memory does not exist.")
        return ToolPreview(
            summary="Forget memory {0}".format(record.id),
            details=("content={0}".format(_record_mapping(record)["content"]),),
        )

    def forget_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        record = store.forget(arguments.memory_id)
        return ToolOutcome(
            summary="Forgot memory {0}; it can be restored.".format(record.id),
            data={"memory_id": record.id, "status": record.status},
        )

    def activate_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        record = store.get(arguments.memory_id)
        if record is None:
            raise ToolError("Memory does not exist.")
        return ToolPreview(
            summary="Activate memory candidate {0}".format(record.id),
            details=("content={0}".format(_record_mapping(record)["content"]),),
        )

    def activate_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        candidate = store.get(arguments.memory_id)
        if candidate is None or candidate.sensitivity != "normal":
            raise ToolError(
                "Only normal-sensitivity memory candidates can be activated."
            )
        record = store.activate(arguments.memory_id)
        return ToolOutcome(
            summary="Activated memory {0}.".format(record.id),
            data={"memory": _record_mapping(record)},
        )

    def restore_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        record = store.get(arguments.memory_id)
        if record is None:
            raise ToolError("Memory does not exist.")
        return ToolPreview(
            summary="Restore memory {0}".format(record.id),
            details=("current status={0}".format(record.status),),
        )

    def restore_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        record = store.restore(arguments.memory_id)
        return ToolOutcome(
            summary="Restored memory {0}.".format(record.id),
            data={"memory": _record_mapping(record)},
        )

    def clear_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        scope_type, scope_key = _scope(arguments.scope, context)
        state = store.state_digest(scope_type, scope_key)
        return ToolPreview(
            summary="Forget {0} memory record(s)".format(state["count"]),
            details=("scope={0}".format(arguments.scope.value),),
        )

    def clear_state(arguments: BaseModel, context: ToolContext) -> Mapping[str, Any]:
        scope_type, scope_key = _scope(arguments.scope, context)
        return store.state_digest(scope_type, scope_key)

    def clear_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        scope_type, scope_key = _scope(arguments.scope, context)
        count = store.clear(scope_type, scope_key)
        return ToolOutcome(
            summary=(
                "Forgot {0} memory record(s); individual records remain restorable."
            ).format(count),
            data={"forgotten": count, "scope": arguments.scope.value},
        )

    def export_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        return ToolPreview(
            summary="Export local memory metadata",
            details=(
                "scope={0}".format(arguments.scope.value),
                "include inactive={0}".format(arguments.include_inactive),
            ),
        )

    def export_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
        scope_type, scope_key = _scope(arguments.scope, context)
        statuses = (
            ("active", "candidate", "superseded", "forgotten")
            if arguments.include_inactive
            else ("active",)
        )
        records = store.list(
            statuses=statuses,
            scope_type=scope_type,
            scope_key=scope_key,
            limit=1000,
        )
        values = [_record_mapping(record) for record in records]
        selected, output_truncated = bound_json_items(values, 20, 24576)
        captured_complete = len(records) < 1000
        truncated = output_truncated or not captured_complete
        artifact_ids = ()
        warnings = ()
        if context.artifact_writer is not None or truncated:
            artifact_ids, warnings = store_json_artifact(
                context,
                {"memories": values},
                sensitivity="sensitive",
                complete=captured_complete,
            )
        return ToolOutcome(
            summary="Exported {0} captured memory record(s).".format(len(records)),
            data={"memories": selected, "truncated": truncated},
            warnings=warnings,
            artifact_ids=artifact_ids,
        )

    return (
        ToolSpec(
            name="memory.inspect",
            description="Inspect bounded local memory records and lifecycle metadata.",
            argument_model=InspectMemoryArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("memory.read",),
            preview_builder=inspect_preview,
            executor=inspect_execute,
            max_output_bytes=65536,
        ),
        ToolSpec(
            name="memory.search",
            description=(
                "Search active normal-sensitivity memories in the current scope."
            ),
            argument_model=SearchMemoryArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("memory.read",),
            preview_builder=search_preview,
            executor=search_execute,
            max_output_bytes=65536,
        ),
        ToolSpec(
            name="memory.correct",
            description=(
                "Correct one memory by superseding it with validated new content."
            ),
            argument_model=CorrectMemoryArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("memory.write",),
            risk_evaluator=correction_risk,
            state_reader=memory_state,
            preview_builder=correct_preview,
            executor=correct_execute,
            supports_dry_run=True,
            supports_rollback=True,
        ),
        ToolSpec(
            name="memory.forget",
            description="Reversibly forget one active or candidate memory.",
            argument_model=MemoryIdArguments,
            minimum_risk=RiskLevel.DESTRUCTIVE,
            required_permissions=("memory.write",),
            state_reader=memory_state,
            preview_builder=forget_preview,
            executor=forget_execute,
            supports_dry_run=True,
            supports_rollback=True,
        ),
        ToolSpec(
            name="memory.activate",
            description="Activate one reviewed inactive memory candidate.",
            argument_model=MemoryIdArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("memory.write",),
            risk_evaluator=activation_risk,
            state_reader=memory_state,
            preview_builder=activate_preview,
            executor=activate_execute,
            supports_dry_run=True,
            supports_rollback=True,
        ),
        ToolSpec(
            name="memory.restore",
            description="Restore one reversibly forgotten or superseded memory.",
            argument_model=MemoryIdArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("memory.write",),
            state_reader=memory_state,
            preview_builder=restore_preview,
            executor=restore_execute,
            supports_dry_run=True,
            supports_rollback=True,
        ),
        ToolSpec(
            name="memory.clear",
            description=(
                "Reversibly forget all active and candidate memories in a scope."
            ),
            argument_model=ClearMemoryArguments,
            minimum_risk=RiskLevel.DESTRUCTIVE,
            required_permissions=("memory.write",),
            state_reader=clear_state,
            preview_builder=clear_preview,
            executor=clear_execute,
            supports_dry_run=True,
            supports_rollback=True,
        ),
        ToolSpec(
            name="memory.export",
            description="Export bounded, redacted local memory records to an artifact.",
            argument_model=ExportMemoryArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("memory.read",),
            preview_builder=export_preview,
            executor=export_execute,
            max_output_bytes=65536,
        ),
    )
