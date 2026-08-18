import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ..core.path_policy import PathBoundary
from ..errors import PolicyDeniedError, ToolError
from ..retrieval import IgnorePolicy, detect_repository_root
from .base import RiskLevel, ToolContext, ToolOutcome, ToolPreview, ToolSpec
from .output import bound_json_items, bound_text, store_json_artifact
from .subprocesses import BoundedProcessResult, run_bounded_process

_GIT_HARD_OUTPUT_BYTES = 2 * 1024 * 1024


class EmptyGitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GitDiffArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    staged: bool = False
    path: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    context_lines: int = Field(default=3, ge=0, le=10)
    max_files: int = Field(default=10, ge=1, le=10)


class GitLogArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=10, ge=1, le=50)


@dataclass(frozen=True)
class _GitInvocation:
    root: Path
    executable: str
    environment: dict


def _git_invocation(context: ToolContext) -> _GitInvocation:
    root = detect_repository_root(context.working_directory)
    if root is None:
        raise ToolError("Current directory is not inside a Git repository.")
    executable = shutil.which("git")
    if executable is None:
        raise ToolError("Git executable is not available.")
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "PAGER": "cat",
        }
    )
    return _GitInvocation(root=root, executable=executable, environment=environment)


def _run_git(
    context: ToolContext,
    invocation: _GitInvocation,
    arguments: List[str],
    maximum_output_bytes: int = _GIT_HARD_OUTPUT_BYTES,
    allow_failure: bool = False,
) -> BoundedProcessResult:
    context.ensure_within_deadline()
    result = run_bounded_process(
        [
            invocation.executable,
            "--no-pager",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "diff.external=",
            "-C",
            str(invocation.root),
        ]
        + arguments,
        cwd=invocation.root,
        timeout_seconds=5.0,
        maximum_output_bytes=maximum_output_bytes,
        environment=invocation.environment,
    )
    if result.returncode != 0 and not result.output_truncated and not allow_failure:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        detail = "".join(
            character if character.isprintable() else " " for character in detail
        )
        detail = " ".join(detail.split())[:500]
        raise ToolError(
            "Git command failed{0}.".format(": {0}".format(detail) if detail else "")
        )
    return result


def _decode_path(value: bytes) -> Optional[str]:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError:
        return None
    path = Path(decoded)
    if path.is_absolute() or ".." in path.parts:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return None
    return decoded


def _status_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(summary="Inspect Git working-tree status")


def _status_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    invocation = _git_invocation(context)
    result = _run_git(
        context,
        invocation,
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
    )
    parts = result.stdout.split(b"\x00")
    if result.stdout and not result.stdout.endswith(b"\x00"):
        parts = parts[:-1]
    policy = IgnorePolicy(invocation.root)
    records = []
    omitted_sensitive = 0
    invalid_paths = 0
    index = 0
    while index < len(parts):
        raw = parts[index]
        index += 1
        if not raw:
            continue
        if len(raw) < 4:
            continue
        status_code = raw[:2].decode("ascii", errors="replace")
        path = _decode_path(raw[3:])
        source = None
        if "R" in status_code or "C" in status_code:
            if index < len(parts):
                source = _decode_path(parts[index])
                index += 1
        if path is None or (
            source is None and ("R" in status_code or "C" in status_code)
        ):
            invalid_paths += 1
            continue
        if (
            policy.is_sensitive(path)
            or policy.is_ignored(path)
            or (
                source is not None
                and (policy.is_sensitive(source) or policy.is_ignored(source))
            )
        ):
            omitted_sensitive += 1
            continue
        record = {"status": status_code, "path": path}
        if source is not None:
            record["source"] = source
        records.append(record)
        if len(records) >= 2000:
            break

    branch_result = _run_git(
        context,
        invocation,
        ["branch", "--show-current"],
        maximum_output_bytes=4096,
        allow_failure=True,
    )
    branch = branch_result.stdout.decode("utf-8", errors="replace").strip() or None
    selected, output_truncated = bound_json_items(records, 200, 16384)
    truncated = (
        result.output_truncated
        or output_truncated
        or len(records) >= 2000
        or invalid_paths > 0
    )
    warnings = []
    artifact_ids = ()
    if omitted_sensitive:
        warnings.append(
            "Omitted {0} sensitive or ignored path(s).".format(omitted_sensitive)
        )
    if invalid_paths:
        warnings.append("Omitted paths that could not be represented safely.")
    if result.output_truncated:
        warnings.append("Git status exceeded the hard output limit and is partial.")
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {
                "branch": branch,
                "changes": records,
                "source_output_truncated": result.output_truncated,
            },
            sensitivity="sensitive",
            complete=not result.output_truncated,
        )
        warnings.extend(artifact_warnings)
    return ToolOutcome(
        summary="Git status contains {0} visible change(s).".format(len(records)),
        data={
            "branch": branch,
            "changes": selected,
            "truncated": truncated,
            "sensitive_paths_omitted": omitted_sensitive,
        },
        warnings=tuple(warnings),
        artifact_ids=artifact_ids,
    )


def _diff_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    details = (
        "staged={0}".format(arguments.staged),
        "context lines={0}".format(arguments.context_lines),
        "max files={0}".format(arguments.max_files),
    )
    paths = () if arguments.path is None else (arguments.path,)
    return ToolPreview(
        summary="Inspect bounded Git diff",
        details=details,
        affected_paths=paths,
    )


def _explicit_git_path(
    context: ToolContext,
    invocation: _GitInvocation,
    requested: str,
) -> str:
    candidate = Path(requested)
    if not candidate.is_absolute():
        candidate = context.working_directory / candidate
    resolved = PathBoundary(invocation.root).resolve_for_write(candidate)
    if IgnorePolicy.is_sensitive(resolved.relative):
        raise PolicyDeniedError("Refusing to show a diff for a likely secret path.")
    return resolved.relative.as_posix()


def _changed_paths(
    context: ToolContext,
    invocation: _GitInvocation,
    arguments: BaseModel,
) -> Tuple[List[str], int, bool]:
    command = ["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv"]
    if arguments.staged:
        command.append("--cached")
    if arguments.path is not None:
        command.extend(["--", _explicit_git_path(context, invocation, arguments.path)])
    result = _run_git(
        context,
        invocation,
        command,
        maximum_output_bytes=262144,
    )
    policy = IgnorePolicy(invocation.root)
    paths = []
    omitted = 0
    raw_paths = result.stdout.split(b"\x00")
    if result.stdout and not result.stdout.endswith(b"\x00"):
        raw_paths = raw_paths[:-1]
        omitted += 1
    for raw in raw_paths:
        if not raw:
            continue
        path = _decode_path(raw)
        if path is None:
            omitted += 1
            continue
        if policy.is_sensitive(path) or policy.is_ignored(path):
            omitted += 1
            continue
        paths.append(path)
    unique_paths = list(dict.fromkeys(paths))
    truncated = result.output_truncated or len(unique_paths) > arguments.max_files
    return unique_paths[: arguments.max_files], omitted, truncated


def _diff_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    invocation = _git_invocation(context)
    paths, omitted, paths_truncated = _changed_paths(context, invocation, arguments)
    if not paths:
        warnings = ()
        if omitted:
            warnings = ("Changed paths were omitted by local sensitivity policy.",)
        return ToolOutcome(
            summary="No approved changed files were available for the Git diff.",
            data={"diff": "", "files": [], "truncated": paths_truncated},
            warnings=warnings,
        )

    command = [
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--unified={0}".format(arguments.context_lines),
    ]
    if arguments.staged:
        command.append("--cached")
    command.extend(["--"] + paths)
    result = _run_git(context, invocation, command)
    text = result.stdout.decode("utf-8", errors="replace")
    bounded = bound_text(
        context,
        text,
        inline_bytes=6144,
        sensitivity="sensitive",
        complete=not result.output_truncated,
    )
    warnings = list(bounded.warnings)
    if omitted:
        warnings.append("Omitted {0} sensitive or unsafe path(s).".format(omitted))
    if paths_truncated:
        warnings.append("Diff file limit was reached; additional files were omitted.")
    if result.output_truncated:
        warnings.append("Git diff exceeded the hard output limit and is partial.")
    return ToolOutcome(
        summary="Inspected Git diff for {0} file(s).".format(len(paths)),
        data={
            "diff": bounded.text,
            "files": paths,
            "truncated": (
                bounded.truncated or result.output_truncated or paths_truncated
            ),
        },
        warnings=tuple(warnings),
        artifact_ids=bounded.artifact_ids,
    )


def _log_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Inspect recent Git commits",
        details=("limit={0}".format(arguments.limit),),
    )


def _log_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    invocation = _git_invocation(context)
    result = _run_git(
        context,
        invocation,
        [
            "log",
            "-n",
            str(arguments.limit),
            "-z",
            "--format=%H%x00%aI%x00%s",
        ],
        maximum_output_bytes=262144,
    )
    fields = result.stdout.split(b"\x00")
    commits = []
    for index in range(0, len(fields) - 2, 3):
        if not fields[index]:
            continue
        commits.append(
            {
                "commit": fields[index].decode("ascii", errors="replace"),
                "authored_at": fields[index + 1].decode("utf-8", errors="replace"),
                "subject": fields[index + 2].decode("utf-8", errors="replace"),
            }
        )
    selected, output_truncated = bound_json_items(commits, arguments.limit, 16384)
    truncated = result.output_truncated or output_truncated
    warnings = ()
    artifact_ids = ()
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {"commits": commits},
            sensitivity="sensitive",
            complete=not result.output_truncated,
        )
        warnings = artifact_warnings
        if result.output_truncated:
            warnings += ("Git log exceeded the hard output limit and is partial.",)
    return ToolOutcome(
        summary="Read {0} recent Git commit(s).".format(len(selected)),
        data={"commits": selected, "truncated": truncated},
        warnings=warnings,
        artifact_ids=artifact_ids,
    )


def build_git_tools() -> Tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            name="git.status",
            description=(
                "Inspect bounded Git working-tree status without returning file "
                "contents."
            ),
            argument_model=EmptyGitArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("git.read",),
            preview_builder=_status_preview,
            executor=_status_execute,
            max_output_bytes=65536,
        ),
        ToolSpec(
            name="git.diff",
            description=(
                "Inspect a bounded Git diff for non-sensitive changed files; external "
                "diff and text-conversion commands are disabled."
            ),
            argument_model=GitDiffArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("git.read",),
            preview_builder=_diff_preview,
            executor=_diff_execute,
            max_output_bytes=65536,
        ),
        ToolSpec(
            name="git.log",
            description=(
                "Inspect a bounded list of recent Git commit hashes and subjects."
            ),
            argument_model=GitLogArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("git.read",),
            preview_builder=_log_preview,
            executor=_log_execute,
            max_output_bytes=65536,
        ),
    )
