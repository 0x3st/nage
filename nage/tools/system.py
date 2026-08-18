import platform
import shutil
import sys
from typing import Tuple

import psutil
from pydantic import BaseModel, ConfigDict, Field

from ..core.path_policy import PathBoundary
from .base import RiskLevel, ToolContext, ToolOutcome, ToolPreview, ToolSpec
from .output import bound_json_items, store_json_artifact


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiskUsageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(default=".", min_length=1, max_length=1024)


class ProcessArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name_contains: str = Field(default="", max_length=120)
    limit: int = Field(default=20, ge=1, le=100)


def _simple_preview(summary: str):
    def preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
        return ToolPreview(summary=summary)

    return preview


def _platform_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    return ToolOutcome(
        summary="Inspected local platform information.",
        data={
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "byte_order": sys.byteorder,
        },
    )


def _disk_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Inspect disk usage for {0}".format(arguments.path),
        affected_paths=(arguments.path,),
    )


def _disk_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    resolved = PathBoundary(context.working_directory).resolve_for_read(arguments.path)
    usage = shutil.disk_usage(str(resolved.absolute))
    percent_used = 0.0 if usage.total == 0 else usage.used / usage.total * 100.0
    return ToolOutcome(
        summary="Inspected disk usage for {0}.".format(
            resolved.relative.as_posix() or "."
        ),
        data={
            "path": resolved.relative.as_posix() or ".",
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent_used": round(percent_used, 2),
        },
    )


def _memory_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    virtual = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return ToolOutcome(
        summary="Inspected local memory usage.",
        data={
            "virtual": {
                "total_bytes": virtual.total,
                "available_bytes": virtual.available,
                "used_bytes": virtual.used,
                "percent_used": virtual.percent,
            },
            "swap": {
                "total_bytes": swap.total,
                "used_bytes": swap.used,
                "free_bytes": swap.free,
                "percent_used": swap.percent,
            },
        },
    )


def _process_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    detail = "all process names"
    if arguments.name_contains:
        detail = "names containing {0!r}".format(arguments.name_contains)
    return ToolPreview(
        summary="Inspect processes by memory usage",
        details=(detail, "limit={0}".format(arguments.limit)),
    )


def _process_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    filter_text = arguments.name_contains.casefold().strip()
    records = []
    scanned = 0
    scan_limit = 5000
    for process in psutil.process_iter(
        attrs=("pid", "name", "status", "memory_info", "create_time")
    ):
        if scanned >= scan_limit:
            break
        scanned += 1
        if scanned % 128 == 0:
            context.ensure_within_deadline()
        try:
            info = process.info
            name = info.get("name") or ""
            if filter_text and filter_text not in name.casefold():
                continue
            memory_info = info.get("memory_info")
            records.append(
                {
                    "pid": int(info["pid"]),
                    "name": name,
                    "status": info.get("status") or "unknown",
                    "rss_bytes": 0 if memory_info is None else memory_info.rss,
                    "created_at_epoch": info.get("create_time"),
                }
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    records.sort(key=lambda item: (-item["rss_bytes"], item["pid"]))
    selected, output_truncated = bound_json_items(records, arguments.limit, 32768)
    truncated = output_truncated or scanned >= scan_limit
    artifact_ids = ()
    warnings = ()
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {
                "processes": records,
                "scan_limit_reached": scanned >= scan_limit,
            },
            sensitivity="sensitive",
            complete=scanned < scan_limit,
        )
        warnings = artifact_warnings
        if scanned >= scan_limit:
            warnings += (
                "Process scan limit was reached; captured results are partial.",
            )
    return ToolOutcome(
        summary="Found {0} matching process(es); returned {1}.".format(
            len(records), len(selected)
        ),
        data={
            "processes": selected,
            "truncated": truncated,
            "scanned_processes": scanned,
        },
        warnings=warnings,
        artifact_ids=artifact_ids,
    )


def build_system_tools() -> Tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            name="system.platform",
            description="Inspect local operating-system and Python platform metadata.",
            argument_model=EmptyArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("system.read",),
            preview_builder=_simple_preview("Inspect local platform information"),
            executor=_platform_execute,
            max_output_bytes=16384,
        ),
        ToolSpec(
            name="system.disk_usage",
            description=(
                "Inspect total, used, and free bytes for an approved local path."
            ),
            argument_model=DiskUsageArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_disk_preview,
            executor=_disk_execute,
            max_output_bytes=16384,
        ),
        ToolSpec(
            name="system.memory_usage",
            description="Inspect local virtual-memory and swap usage.",
            argument_model=EmptyArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("system.read",),
            preview_builder=_simple_preview("Inspect local memory usage"),
            executor=_memory_execute,
            max_output_bytes=16384,
        ),
        ToolSpec(
            name="system.processes",
            description=(
                "List bounded process metadata sorted by resident memory; command "
                "lines and environments are never returned."
            ),
            argument_model=ProcessArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("process.read",),
            preview_builder=_process_preview,
            executor=_process_execute,
            max_output_bytes=65536,
        ),
    )
