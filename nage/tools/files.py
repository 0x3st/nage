import errno
import hashlib
import os
import stat
import tempfile
from itertools import islice
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator
from send2trash import send2trash

from ..core.path_policy import PathBoundary
from ..errors import PolicyDeniedError, ToolError
from ..retrieval import FileRetriever
from .base import RiskLevel, ToolContext, ToolOutcome, ToolPreview, ToolSpec
from .context import build_context_tools
from .output import bound_json_items, store_json_artifact

_MAX_COPY_BYTES = 64 * 1024 * 1024
_PROTECTED_INTERNAL_DIRECTORIES = frozenset({".git", ".nage"})


class ListDirectoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(default=".", min_length=1, max_length=1024)
    include_hidden: bool = False
    limit: int = Field(default=100, ge=1, le=100)


class PathArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)


class FindNameArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200)
    path: str = Field(default=".", min_length=1, max_length=1024)
    limit: int = Field(default=20, ge=1, le=100)


class FindLargeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(default=".", min_length=1, max_length=1024)
    minimum_size_bytes: int = Field(default=104857600, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class CreateDirectoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    parents: bool = True


class CreateTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=65536)
    create_parents: bool = False

    @field_validator("content")
    @classmethod
    def validate_content_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("UTF-8 content cannot exceed 65536 bytes")
        return value


class CopyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=1024)
    destination: str = Field(min_length=1, max_length=1024)
    overwrite: bool = False


class MoveArguments(CopyArguments):
    pass


class TrashArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paths: List[str] = Field(min_length=1, max_length=20)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, value: List[str]) -> List[str]:
        if any(not path or len(path) > 1024 for path in value):
            raise ValueError("trash paths must be non-empty and bounded")
        if len(value) != len(set(value)):
            raise ValueError("trash paths must not contain duplicates")
        return value


def _relative_label(path: Path) -> str:
    value = path.as_posix()
    return value if value and value != "." else "."


def _mutation_boundary(context: ToolContext) -> PathBoundary:
    return PathBoundary(
        context.working_directory,
        protected_paths=context.protected_paths,
    )


def _reject_protected_path(relative: Path) -> None:
    if any(
        part.casefold() in _PROTECTED_INTERNAL_DIRECTORIES for part in relative.parts
    ):
        raise PolicyDeniedError(
            "Refusing to mutate Nage or Git internal state through a file tool."
        )


def _resolve_mutation_path(
    boundary: PathBoundary,
    requested: str,
    must_exist: bool = False,
):
    resolved = boundary.resolve_for_write(requested, must_exist=must_exist)
    _reject_protected_path(resolved.relative)
    return resolved


def _resolve_mutation_source(boundary: PathBoundary, requested: str):
    resolved = boundary.resolve_for_read(requested)
    _reject_protected_path(resolved.relative)
    return resolved


def _safe_read_path(context: ToolContext, requested: str):
    retriever = FileRetriever(
        context.working_directory,
        deadline_check=context.ensure_within_deadline,
    )
    raw = Path(requested)
    if not raw.is_absolute() and retriever.ignore.is_sensitive(raw):
        raise PolicyDeniedError("Refusing to inspect a likely secret path.")
    resolved = retriever.boundary.resolve_for_read(requested)
    if retriever.ignore.is_sensitive(resolved.relative):
        raise PolicyDeniedError("Refusing to inspect a likely secret path.")
    if retriever.ignore.is_ignored(resolved.relative):
        raise PolicyDeniedError("Refusing to inspect an ignored path.")
    return retriever, resolved


def _path_state(
    path: Path,
    include_directory_changes: bool = False,
) -> Dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"exists": False}
    except OSError as exc:
        raise ToolError("Unable to inspect filesystem state.") from exc
    file_type = stat.S_IFMT(metadata.st_mode)
    state = {
        "exists": True,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": file_type,
        "symlink": stat.S_ISLNK(metadata.st_mode),
    }
    if stat.S_ISREG(metadata.st_mode) or include_directory_changes:
        state.update(
            {
                "size_bytes": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
                "ctime_ns": metadata.st_ctime_ns,
            }
        )
    return state


def _ancestor_states(boundary: PathBoundary, target: Path) -> Mapping[str, Any]:
    states = {}
    current = target.parent
    while True:
        relative = current.relative_to(boundary.root)
        states[_relative_label(relative)] = _path_state(current)
        if current == boundary.root:
            break
        current = current.parent
    return states


def _missing_parent_labels(boundary: PathBoundary, target: Path) -> Tuple[str, ...]:
    labels = []
    current = target.parent
    while current != boundary.root and not current.exists():
        labels.append(_relative_label(current.relative_to(boundary.root)))
        current = current.parent
    return tuple(reversed(labels))


def _target_state(boundary: PathBoundary, requested: str) -> Mapping[str, Any]:
    resolved = _resolve_mutation_path(boundary, requested)
    return {
        "target": _path_state(resolved.absolute),
        "ancestors": _ancestor_states(boundary, resolved.absolute),
    }


def _hash_regular_file(path: Path, context: ToolContext) -> str:
    descriptor, starting_metadata = _open_regular_file(path)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while True:
                context.ensure_within_deadline()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            ending_metadata = os.fstat(source.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        starting_metadata.st_dev != ending_metadata.st_dev
        or starting_metadata.st_ino != ending_metadata.st_ino
        or starting_metadata.st_size != ending_metadata.st_size
        or starting_metadata.st_mtime_ns != ending_metadata.st_mtime_ns
    ):
        raise ToolError("Source changed while its action plan was prepared.")
    return digest.hexdigest()


def _two_path_state(
    context: ToolContext,
    source: str,
    destination: str,
    hash_destination: bool,
) -> Mapping[str, Any]:
    boundary = _mutation_boundary(context)
    source_path = _resolve_mutation_source(boundary, source).absolute
    destination_path = _resolve_mutation_path(boundary, destination).absolute
    source_state = _path_state(source_path)
    if source_state.get("mode") == stat.S_IFREG:
        source_state["sha256"] = _hash_regular_file(source_path, context)
    destination_state = _path_state(destination_path)
    if hash_destination and destination_state.get("mode") == stat.S_IFREG:
        destination_state["sha256"] = _hash_regular_file(destination_path, context)
    return {
        "source": source_state,
        "destination": destination_state,
        "destination_parent": _path_state(destination_path.parent),
    }


def _write_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
    boundary = _mutation_boundary(context)
    try:
        resolved = _resolve_mutation_path(boundary, arguments.path)
    except (PolicyDeniedError, ToolError):
        return RiskLevel.DENIED
    if resolved.absolute == boundary.root:
        return RiskLevel.DENIED
    return RiskLevel.LOCAL_WRITE


def _create_text_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
    boundary = _mutation_boundary(context)
    try:
        resolved = _resolve_mutation_path(boundary, arguments.path)
    except (PolicyDeniedError, ToolError):
        return RiskLevel.DENIED
    if (
        resolved.absolute == boundary.root
        or resolved.absolute.exists()
        or resolved.absolute.is_symlink()
    ):
        return RiskLevel.DENIED
    return RiskLevel.LOCAL_WRITE


def _copy_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
    boundary = _mutation_boundary(context)
    try:
        source = _resolve_mutation_source(boundary, arguments.source)
        destination = _resolve_mutation_path(boundary, arguments.destination)
    except (PolicyDeniedError, ToolError):
        return RiskLevel.DENIED
    if source.absolute == destination.absolute:
        return RiskLevel.DENIED
    try:
        source_metadata = source.absolute.stat()
    except OSError:
        return RiskLevel.DENIED
    if not stat.S_ISREG(source_metadata.st_mode):
        return RiskLevel.DENIED
    if not destination.absolute.parent.is_dir():
        return RiskLevel.DENIED
    if destination.absolute.exists() and not arguments.overwrite:
        return RiskLevel.DENIED
    return RiskLevel.DESTRUCTIVE if arguments.overwrite else RiskLevel.LOCAL_WRITE


def _move_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
    boundary = _mutation_boundary(context)
    try:
        source = _resolve_mutation_path(boundary, arguments.source, must_exist=True)
        destination = _resolve_mutation_path(boundary, arguments.destination)
    except (PolicyDeniedError, ToolError):
        return RiskLevel.DENIED
    if source.absolute == destination.absolute:
        return RiskLevel.DENIED
    try:
        source_metadata = source.absolute.lstat()
    except OSError:
        return RiskLevel.DENIED
    if not stat.S_ISREG(source_metadata.st_mode):
        return RiskLevel.DENIED
    if not destination.absolute.parent.is_dir():
        return RiskLevel.DENIED
    if destination.absolute.exists() and not arguments.overwrite:
        return RiskLevel.DENIED
    return RiskLevel.DESTRUCTIVE if arguments.overwrite else RiskLevel.LOCAL_WRITE


def _resolve_trash_paths(arguments: BaseModel, context: ToolContext):
    boundary = _mutation_boundary(context)
    paths = [
        _resolve_mutation_path(boundary, path, must_exist=True)
        for path in arguments.paths
    ]
    canonical = [path.absolute for path in paths]
    if len(canonical) != len(set(canonical)):
        raise PolicyDeniedError("Trash paths resolve to duplicate targets.")
    if any(path == boundary.root for path in canonical):
        raise PolicyDeniedError("Refusing to trash the approved workspace root.")
    return boundary, paths


def _trash_risk(arguments: BaseModel, context: ToolContext) -> RiskLevel:
    try:
        _resolve_trash_paths(arguments, context)
    except (PolicyDeniedError, ToolError):
        return RiskLevel.DENIED
    return RiskLevel.DESTRUCTIVE


def _list_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="List directory {0}".format(arguments.path),
        affected_paths=(arguments.path,),
    )


def _list_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    retriever, resolved = _safe_read_path(context, arguments.path)
    if not resolved.absolute.is_dir():
        raise ToolError("Requested path is not a directory.")
    records = []
    scanned = 0
    hard_limit = 2000
    try:
        with os.scandir(str(resolved.absolute)) as scanner:
            entries = list(islice(scanner, hard_limit + 1))
        scan_limit_reached = len(entries) > hard_limit
        entries = sorted(entries[:hard_limit], key=lambda item: item.name.casefold())
    except OSError as exc:
        raise ToolError("Unable to list requested directory.") from exc
    for entry in entries:
        if scanned >= hard_limit:
            break
        scanned += 1
        if scanned % 128 == 0:
            context.ensure_within_deadline()
        if entry.is_symlink() or (
            not arguments.include_hidden and entry.name.startswith(".")
        ):
            continue
        path = Path(entry.path)
        relative = path.relative_to(retriever.root)
        try:
            is_directory = entry.is_dir(follow_symlinks=False)
            if retriever.ignore.is_ignored(relative, is_dir=is_directory):
                continue
            if retriever.ignore.is_sensitive(relative):
                continue
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        records.append(
            {
                "name": entry.name,
                "path": relative.as_posix(),
                "type": "directory" if is_directory else "file",
                "size_bytes": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
            }
        )
    selected, output_truncated = bound_json_items(records, arguments.limit, 32768)
    truncated = output_truncated or scan_limit_reached
    artifact_ids = ()
    warnings = ()
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {"entries": records, "scan_limit_reached": scan_limit_reached},
            sensitivity="sensitive",
            complete=not scan_limit_reached,
        )
        warnings = artifact_warnings
        if scan_limit_reached:
            warnings += (
                "Directory scan limit was reached; captured results are partial.",
            )
    return ToolOutcome(
        summary="Listed {0}; returned {1} entr{2}.".format(
            _relative_label(resolved.relative),
            len(selected),
            "y" if len(selected) == 1 else "ies",
        ),
        data={"entries": selected, "truncated": truncated},
        warnings=warnings,
        artifact_ids=artifact_ids,
    )


def _metadata_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Inspect metadata for {0}".format(arguments.path),
        affected_paths=(arguments.path,),
    )


def _metadata_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    _, resolved = _safe_read_path(context, arguments.path)
    try:
        metadata = resolved.absolute.stat()
    except OSError as exc:
        raise ToolError("Unable to inspect requested path.") from exc
    if stat.S_ISREG(metadata.st_mode):
        kind = "file"
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    else:
        kind = "other"
    return ToolOutcome(
        summary="Inspected metadata for {0}.".format(
            _relative_label(resolved.relative)
        ),
        data={
            "path": _relative_label(resolved.relative),
            "type": kind,
            "size_bytes": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
            "mode": stat.S_IMODE(metadata.st_mode),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
        },
    )


def _find_name_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Find files named like {0!r}".format(arguments.query),
        details=("path={0}".format(arguments.path),),
        affected_paths=(arguments.path,),
    )


def _find_name_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    retriever = FileRetriever(
        context.working_directory,
        deadline_check=context.ensure_within_deadline,
    )
    hard_result_limit = 1000
    matches = retriever.find_by_name(
        arguments.query,
        under=arguments.path,
        limit=hard_result_limit,
    )
    scan_truncated = len(matches) >= hard_result_limit or retriever.last_scan_truncated
    match_records = [
        {"path": match.relative_path, "size_bytes": match.size_bytes}
        for match in matches
    ]
    selected, output_truncated = bound_json_items(match_records, arguments.limit, 32768)
    truncated = scan_truncated or output_truncated
    artifact_ids = ()
    warnings = ()
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {"matches": match_records},
            sensitivity="sensitive",
            complete=not scan_truncated,
        )
        warnings = (
            "Search limits were reached; more files may exist.",
        ) + artifact_warnings
    return ToolOutcome(
        summary="Found {0} matching file(s).".format(len(selected)),
        data={"matches": selected, "truncated": truncated},
        warnings=warnings,
        artifact_ids=artifact_ids,
    )


def _find_large_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    return ToolPreview(
        summary="Find files at least {0} bytes".format(arguments.minimum_size_bytes),
        details=("path={0}".format(arguments.path),),
        affected_paths=(arguments.path,),
    )


def _find_large_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    retriever = FileRetriever(
        context.working_directory,
        deadline_check=context.ensure_within_deadline,
    )
    records = []
    for path in retriever.iter_files(arguments.path):
        context.ensure_within_deadline()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= arguments.minimum_size_bytes:
            records.append(
                {
                    "path": path.relative_to(retriever.root).as_posix(),
                    "size_bytes": size,
                }
            )
    records.sort(key=lambda item: (-item["size_bytes"], item["path"]))
    selected, output_truncated = bound_json_items(records, arguments.limit, 32768)
    truncated = output_truncated or retriever.last_scan_truncated
    artifact_ids = ()
    warnings = ()
    if truncated:
        artifact_ids, artifact_warnings = store_json_artifact(
            context,
            {"files": records, "scan_limit_reached": retriever.last_scan_truncated},
            sensitivity="sensitive",
            complete=not retriever.last_scan_truncated,
        )
        warnings = artifact_warnings
        if retriever.last_scan_truncated:
            warnings += ("File scan limit was reached; captured results are partial.",)
    return ToolOutcome(
        summary="Found {0} file(s) at or above the size threshold.".format(
            len(records)
        ),
        data={"files": selected, "truncated": truncated},
        warnings=warnings,
        artifact_ids=artifact_ids,
    )


def _directory_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    boundary = _mutation_boundary(context)
    resolved = _resolve_mutation_path(boundary, arguments.path)
    missing_parents = (
        _missing_parent_labels(boundary, resolved.absolute) if arguments.parents else ()
    )
    affected = missing_parents + (_relative_label(resolved.relative),)
    return ToolPreview(
        summary="Create directory {0}".format(_relative_label(resolved.relative)),
        details=(
            "create parents={0}".format(arguments.parents),
            "missing parents={0}".format(len(missing_parents)),
        ),
        affected_paths=tuple(dict.fromkeys(affected)),
    )


def _directory_state(arguments: BaseModel, context: ToolContext) -> Mapping[str, Any]:
    return _target_state(_mutation_boundary(context), arguments.path)


def _directory_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    boundary = _mutation_boundary(context)
    resolved = _resolve_mutation_path(boundary, arguments.path)
    if resolved.absolute == boundary.root:
        return ToolOutcome(
            summary="Directory already exists: .", data={"created": False}
        )
    try:
        resolved.absolute.mkdir(parents=arguments.parents, exist_ok=False)
    except FileExistsError:
        if resolved.absolute.is_dir() and not resolved.absolute.is_symlink():
            return ToolOutcome(
                summary="Directory already exists: {0}.".format(
                    _relative_label(resolved.relative)
                ),
                data={"created": False, "path": _relative_label(resolved.relative)},
            )
        raise ToolError("Destination already exists and is not a safe directory.")
    except OSError as exc:
        raise ToolError("Unable to create directory.") from exc
    return ToolOutcome(
        summary="Created directory {0}.".format(_relative_label(resolved.relative)),
        data={"created": True, "path": _relative_label(resolved.relative)},
    )


def _text_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    boundary = _mutation_boundary(context)
    resolved = _resolve_mutation_path(boundary, arguments.path)
    missing_parents = (
        _missing_parent_labels(boundary, resolved.absolute)
        if arguments.create_parents
        else ()
    )
    digest = hashlib.sha256(arguments.content.encode("utf-8")).hexdigest()
    return ToolPreview(
        summary="Create text file {0}".format(_relative_label(resolved.relative)),
        details=(
            "UTF-8 bytes={0}".format(len(arguments.content.encode("utf-8"))),
            "sha256={0}".format(digest),
            "create parents={0}".format(arguments.create_parents),
        ),
        affected_paths=tuple(
            dict.fromkeys(missing_parents + (_relative_label(resolved.relative),))
        ),
    )


def _text_state(arguments: BaseModel, context: ToolContext) -> Mapping[str, Any]:
    return _target_state(_mutation_boundary(context), arguments.path)


def _text_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    boundary = _mutation_boundary(context)
    resolved = _resolve_mutation_path(boundary, arguments.path)
    parent = resolved.absolute.parent
    if arguments.create_parents:
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError("Unable to create parent directories.") from exc
    if not parent.is_dir() or parent.is_symlink():
        raise ToolError("Destination parent is missing or unsafe.")
    data = arguments.content.encode("utf-8")
    descriptor = None
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(resolved.absolute), flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise ToolError(
            "Destination already exists; refusing to overwrite it."
        ) from exc
    except OSError as exc:
        if created:
            try:
                resolved.absolute.unlink()
            except OSError:
                pass
        raise ToolError("Unable to create text file.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return ToolOutcome(
        summary="Created text file {0}.".format(_relative_label(resolved.relative)),
        data={
            "path": _relative_label(resolved.relative),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )


def _copy_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    boundary = _mutation_boundary(context)
    source = _resolve_mutation_source(boundary, arguments.source)
    destination = _resolve_mutation_path(boundary, arguments.destination)
    return ToolPreview(
        summary="Copy {0} to {1}".format(
            _relative_label(source.relative), _relative_label(destination.relative)
        ),
        details=("overwrite={0}".format(arguments.overwrite),),
        affected_paths=(
            _relative_label(source.relative),
            _relative_label(destination.relative),
        ),
    )


def _copy_state(arguments: BaseModel, context: ToolContext) -> Mapping[str, Any]:
    return _two_path_state(
        context,
        arguments.source,
        arguments.destination,
        hash_destination=arguments.overwrite,
    )


def _open_regular_file(path: Path):
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(str(path), flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ToolError("Source must be a regular file.")
    if metadata.st_size > _MAX_COPY_BYTES:
        os.close(descriptor)
        raise ToolError("Source exceeds the 64 MiB copy limit.")
    return descriptor, metadata


def _copy_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    boundary = _mutation_boundary(context)
    source = _resolve_mutation_source(boundary, arguments.source)
    destination = _resolve_mutation_path(boundary, arguments.destination)
    if source.absolute == destination.absolute:
        raise ToolError("Source and destination must be different.")
    if (
        not destination.absolute.parent.is_dir()
        or destination.absolute.parent.is_symlink()
    ):
        raise ToolError("Destination parent is missing or unsafe.")
    if destination.absolute.exists() and not arguments.overwrite:
        raise ToolError("Destination exists; overwrite was not approved.")

    temporary = None
    source_descriptor = None
    temporary_descriptor = None
    try:
        source_descriptor, source_metadata = _open_regular_file(source.absolute)
        temporary_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".nage-copy-", dir=str(destination.absolute.parent)
        )
        temporary = Path(temporary_name)
        with os.fdopen(source_descriptor, "rb") as input_file:
            source_descriptor = None
            with os.fdopen(temporary_descriptor, "wb") as output_file:
                temporary_descriptor = None
                copied_bytes = 0
                while True:
                    context.ensure_within_deadline()
                    chunk = input_file.read(1024 * 1024)
                    if not chunk:
                        break
                    copied_bytes += len(chunk)
                    if copied_bytes > _MAX_COPY_BYTES:
                        raise ToolError("Source grew beyond the 64 MiB copy limit.")
                    output_file.write(chunk)
                ending_metadata = os.fstat(input_file.fileno())
                if (
                    ending_metadata.st_dev != source_metadata.st_dev
                    or ending_metadata.st_ino != source_metadata.st_ino
                    or ending_metadata.st_size != source_metadata.st_size
                    or ending_metadata.st_mtime_ns != source_metadata.st_mtime_ns
                    or copied_bytes != source_metadata.st_size
                ):
                    raise ToolError("Source changed during copy; copy was stopped.")
                output_file.flush()
                os.fsync(output_file.fileno())
        os.chmod(str(temporary), stat.S_IMODE(source_metadata.st_mode))
        if arguments.overwrite:
            os.replace(str(temporary), str(destination.absolute))
        else:
            os.link(str(temporary), str(destination.absolute))
            temporary.unlink()
        temporary = None
    except FileExistsError as exc:
        raise ToolError(
            "Destination appeared after confirmation; copy stopped."
        ) from exc
    except OSError as exc:
        raise ToolError("Unable to copy file safely.") from exc
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return ToolOutcome(
        summary="Copied {0} to {1}.".format(
            _relative_label(source.relative), _relative_label(destination.relative)
        ),
        data={
            "source": _relative_label(source.relative),
            "destination": _relative_label(destination.relative),
            "size_bytes": source_metadata.st_size,
        },
    )


def _move_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    boundary = _mutation_boundary(context)
    source = _resolve_mutation_path(boundary, arguments.source, must_exist=True)
    destination = _resolve_mutation_path(boundary, arguments.destination)
    return ToolPreview(
        summary="Move {0} to {1}".format(
            _relative_label(source.relative), _relative_label(destination.relative)
        ),
        details=("overwrite={0}".format(arguments.overwrite),),
        affected_paths=(
            _relative_label(source.relative),
            _relative_label(destination.relative),
        ),
    )


def _move_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    boundary = _mutation_boundary(context)
    source = _resolve_mutation_path(boundary, arguments.source, must_exist=True)
    destination = _resolve_mutation_path(boundary, arguments.destination)
    if source.absolute == destination.absolute:
        raise ToolError("Source and destination must be different.")
    try:
        source_metadata = source.absolute.lstat()
    except OSError as exc:
        raise ToolError("Unable to inspect move source.") from exc
    if not stat.S_ISREG(source_metadata.st_mode):
        raise ToolError("Move currently supports regular files only.")
    if (
        not destination.absolute.parent.is_dir()
        or destination.absolute.parent.is_symlink()
    ):
        raise ToolError("Destination parent is missing or unsafe.")
    if destination.absolute.exists() and not arguments.overwrite:
        raise ToolError("Destination exists; overwrite was not approved.")
    try:
        if arguments.overwrite:
            os.replace(str(source.absolute), str(destination.absolute))
        else:
            os.link(str(source.absolute), str(destination.absolute))
            try:
                source.absolute.unlink()
            except OSError as exc:
                try:
                    destination.absolute.unlink()
                except OSError:
                    pass
                raise ToolError(
                    "Move could not remove its source; rollback attempted."
                ) from exc
    except FileExistsError as exc:
        raise ToolError(
            "Destination appeared after confirmation; move stopped."
        ) from exc
    except ToolError:
        raise
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise ToolError("Cross-device moves are not supported safely.") from exc
        raise ToolError("Unable to move file safely.") from exc
    return ToolOutcome(
        summary="Moved {0} to {1}.".format(
            _relative_label(source.relative), _relative_label(destination.relative)
        ),
        data={
            "source": _relative_label(source.relative),
            "destination": _relative_label(destination.relative),
        },
    )


def _trash_preview(arguments: BaseModel, context: ToolContext) -> ToolPreview:
    _, paths = _resolve_trash_paths(arguments, context)
    labels = tuple(_relative_label(path.relative) for path in paths)
    return ToolPreview(
        summary="Move {0} item(s) to the system trash".format(len(labels)),
        details=labels,
        affected_paths=labels,
    )


def _trash_state(arguments: BaseModel, context: ToolContext) -> Mapping[str, Any]:
    _, paths = _resolve_trash_paths(arguments, context)
    result = {}
    for resolved in paths:
        result[_relative_label(resolved.relative)] = _path_state(
            resolved.absolute,
            include_directory_changes=True,
        )
    return result


def _trash_execute(arguments: BaseModel, context: ToolContext) -> ToolOutcome:
    boundary, resolved_paths = _resolve_trash_paths(arguments, context)
    moved = []
    for resolved in resolved_paths:
        if resolved.absolute == boundary.root:
            raise ToolError("Refusing to trash the approved workspace root.")
        try:
            send2trash(str(resolved.absolute))
            moved.append(_relative_label(resolved.relative))
        except Exception as exc:
            raise ToolError(
                "Moved {0} item(s), then trash failed for {1}.".format(
                    len(moved), _relative_label(resolved.relative)
                )
            ) from exc
    return ToolOutcome(
        summary="Moved {0} item(s) to the system trash.".format(len(moved)),
        data={"paths": moved},
    )


def build_file_tools() -> Tuple[ToolSpec, ...]:
    read_tools = (
        ToolSpec(
            name="files.list",
            description=(
                "List bounded, non-sensitive entries in one approved directory."
            ),
            argument_model=ListDirectoryArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_list_preview,
            executor=_list_execute,
            max_output_bytes=65536,
        ),
        ToolSpec(
            name="files.metadata",
            description="Inspect metadata for one approved non-sensitive path.",
            argument_model=PathArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_metadata_preview,
            executor=_metadata_execute,
            max_output_bytes=16384,
        ),
        ToolSpec(
            name="files.find_name",
            description="Find bounded project files by literal name or glob pattern.",
            argument_model=FindNameArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_find_name_preview,
            executor=_find_name_execute,
            max_output_bytes=65536,
        ),
        ToolSpec(
            name="files.find_large",
            description="Find the largest non-sensitive files above a byte threshold.",
            argument_model=FindLargeArguments,
            minimum_risk=RiskLevel.READ_ONLY,
            required_permissions=("filesystem.read",),
            preview_builder=_find_large_preview,
            executor=_find_large_execute,
            max_output_bytes=65536,
        ),
    ) + build_context_tools()
    mutation_tools = (
        ToolSpec(
            name="files.create_directory",
            description=(
                "Create one local directory, optionally including missing parents."
            ),
            argument_model=CreateDirectoryArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("filesystem.write",),
            risk_evaluator=_write_risk,
            state_reader=_directory_state,
            preview_builder=_directory_preview,
            executor=_directory_execute,
            supports_dry_run=True,
        ),
        ToolSpec(
            name="files.create_text",
            description=(
                "Create a new UTF-8 text file without overwriting an existing path. "
                "Generated source remains inactive content."
            ),
            argument_model=CreateTextArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("filesystem.write",),
            risk_evaluator=_create_text_risk,
            state_reader=_text_state,
            preview_builder=_text_preview,
            executor=_text_execute,
            supports_dry_run=True,
        ),
        ToolSpec(
            name="files.copy",
            description="Copy one regular file within the approved workspace.",
            argument_model=CopyArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("filesystem.read", "filesystem.write"),
            risk_evaluator=_copy_risk,
            state_reader=_copy_state,
            preview_builder=_copy_preview,
            executor=_copy_execute,
            supports_dry_run=True,
        ),
        ToolSpec(
            name="files.move",
            description=(
                "Move or rename one regular file within the approved workspace."
            ),
            argument_model=MoveArguments,
            minimum_risk=RiskLevel.LOCAL_WRITE,
            required_permissions=("filesystem.write",),
            risk_evaluator=_move_risk,
            state_reader=_copy_state,
            preview_builder=_move_preview,
            executor=_move_execute,
            supports_dry_run=True,
        ),
        ToolSpec(
            name="files.trash",
            description=(
                "Move approved local files or directories to the system trash; "
                "permanent deletion is not provided."
            ),
            argument_model=TrashArguments,
            minimum_risk=RiskLevel.DESTRUCTIVE,
            required_permissions=("filesystem.write",),
            risk_evaluator=_trash_risk,
            state_reader=_trash_state,
            preview_builder=_trash_preview,
            executor=_trash_execute,
            supports_dry_run=True,
            supports_rollback=True,
        ),
    )
    return read_tools + mutation_tools
