from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..retrieval import FileRetriever
from .base import RiskLevel, ToolContext, ToolOutcome, ToolPreview, ToolSpec
from .output import (
    bound_json_items,
    bound_text,
    store_json_artifact,
)


class SearchTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200)
    path: str = Field(default=".", min_length=1, max_length=1024)
    limit: int = Field(default=20, ge=1, le=20)


class ReadRangeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ReadRangeArguments":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.end_line - self.start_line + 1 > 200:
            raise ValueError("line range cannot exceed 200 lines")
        return self


def _search_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Search project text for {0!r}".format(arguments.query),
        details=("path={0}".format(arguments.path),),
        affected_paths=(arguments.path,),
    )


def _search_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    retriever = FileRetriever(
        context.working_directory,
        deadline_check=context.ensure_within_deadline,
    )
    hard_result_limit = 100
    matches = retriever.search_text(
        query=arguments.query,
        under=arguments.path,
        limit=hard_result_limit,
    )
    records = [
        {
            "path": match.relative_path,
            "line_number": match.line_number,
            "line": match.line,
        }
        for match in matches
    ]
    selected, output_truncated = bound_json_items(records, arguments.limit, 16384)
    scan_truncated = (
        len(matches) >= hard_result_limit or retriever.last_search_truncated
    )
    truncated = scan_truncated or output_truncated
    artifact_ids = ()
    warnings = ()
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {"matches": records},
            sensitivity="sensitive",
            complete=not scan_truncated,
        )
        warnings = (
            "Search limits were reached; more matches may exist.",
        ) + artifact_warnings
    return ToolOutcome(
        summary="Found {0} text match(es); returned {1}.".format(
            len(matches), len(selected)
        ),
        data={"matches": selected, "truncated": truncated},
        warnings=warnings,
        artifact_ids=artifact_ids,
    )


def _range_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Read lines {0}-{1} from {2}".format(
            arguments.start_line,
            arguments.end_line,
            arguments.path,
        ),
        affected_paths=(arguments.path,),
    )


def _range_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    retriever = FileRetriever(
        context.working_directory,
        deadline_check=context.ensure_within_deadline,
    )
    excerpt = retriever.read_range(
        requested=arguments.path,
        start_line=arguments.start_line,
        end_line=arguments.end_line,
    )
    bounded = bound_text(
        context,
        excerpt.content,
        inline_bytes=8192,
        sensitivity="sensitive",
    )
    return ToolOutcome(
        summary="Read lines {0}-{1} from {2}.".format(
            excerpt.start_line,
            excerpt.end_line,
            excerpt.relative_path,
        ),
        data={
            "path": excerpt.relative_path,
            "start_line": excerpt.start_line,
            "end_line": excerpt.end_line,
            "total_lines": excerpt.total_lines,
            "content": bounded.text,
            "sha256": excerpt.sha256,
            "truncated": excerpt.truncated or bounded.truncated,
        },
        warnings=bounded.warnings,
        artifact_ids=bounded.artifact_ids,
    )


def build_context_tools() -> Tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            name="files.search_text",
            description=(
                "Search bounded, non-sensitive UTF-8 project files for literal text."
            ),
            argument_model=SearchTextArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_search_preview,
            executor=_search_execute,
            max_output_bytes=32768,
        ),
        ToolSpec(
            name="files.read_range",
            description=(
                "Read a bounded line range from one approved non-sensitive text file."
            ),
            argument_model=ReadRangeArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_range_preview,
            executor=_range_execute,
            max_output_bytes=65536,
        ),
    )
