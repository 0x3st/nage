import fnmatch
import hashlib
import os
import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple, Union

from pathspec import GitIgnoreSpec

from ..core.path_policy import PathBoundary
from ..errors import PolicyDeniedError, ToolError

DEFAULT_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".nage",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "venv",
    }
)
DEFAULT_MANIFEST_NAMES: Tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "Makefile",
    "README.md",
)
_SAFE_ENV_EXAMPLES = frozenset(
    {".env.example", ".env.sample", ".env.template", ".env.defaults"}
)
_SENSITIVE_DIRECTORY_NAMES = frozenset(
    {".aws", ".azure", ".gnupg", ".kube", ".ssh", "keychains"}
)
_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_ecdsa",
        "id_rsa",
        "secrets.json",
    }
)
_SENSITIVE_SUFFIXES = (".key", ".p12", ".pfx", ".pem")


def detect_repository_root(start: Path) -> Optional[Path]:
    try:
        current = Path(start).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if current.is_file():
        current = current.parent
    for candidate in (current,) + tuple(current.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            return candidate
    return None


def is_binary_data(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _read_file_bounded(path: Path, maximum_bytes: int) -> bytes:
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        descriptor = os.open(str(path), flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ToolError("Requested path is not a regular file.")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            data = source.read(maximum_bytes + 1)
    except OSError as exc:
        raise ToolError("Unable to read requested file.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) > maximum_bytes:
        raise ToolError("File exceeds the {0}-byte read limit.".format(maximum_bytes))
    return data


@dataclass(frozen=True)
class FileMatch:
    relative_path: str
    size_bytes: int


@dataclass(frozen=True)
class TextMatch:
    relative_path: str
    line_number: int
    line: str


@dataclass(frozen=True)
class FileExcerpt:
    relative_path: str
    start_line: int
    end_line: int
    total_lines: int
    content: str
    sha256: str
    truncated: bool


class IgnorePolicy:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._git_specs = {Path(): self._load_spec(self.root / ".gitignore")}
        self._nage_spec = self._load_spec(self.root / ".nageignore")

    @staticmethod
    def _load_spec(path: Path) -> Optional[GitIgnoreSpec]:
        if not path.is_file() or path.is_symlink():
            return None
        try:
            if path.stat().st_size > 262144:
                return None
            with path.open("r", encoding="utf-8") as source:
                return GitIgnoreSpec.from_lines(source)
        except (OSError, UnicodeError):
            return None

    @staticmethod
    def is_sensitive(relative_path: Union[str, Path]) -> bool:
        relative = Path(relative_path)
        lowered_parts = tuple(part.casefold() for part in relative.parts)
        if any(part in _SENSITIVE_DIRECTORY_NAMES for part in lowered_parts):
            return True
        if not lowered_parts:
            return False
        name = lowered_parts[-1]
        if name in _SAFE_ENV_EXAMPLES:
            return False
        if name == ".env" or name.startswith(".env."):
            return True
        if name in _SENSITIVE_FILE_NAMES:
            return True
        if name.endswith(_SENSITIVE_SUFFIXES):
            return True
        if name.startswith("id_rsa.") or name.startswith("id_ed25519."):
            return True
        return False

    def is_ignored(self, relative_path: Union[str, Path], is_dir: bool = False) -> bool:
        relative = Path(relative_path)
        parts = tuple(part.casefold() for part in relative.parts)
        if any(part in DEFAULT_EXCLUDED_DIRECTORIES for part in parts):
            return True
        posix_path = relative.as_posix()
        if is_dir and not posix_path.endswith("/"):
            posix_path += "/"
        if self._nage_spec is not None and self._nage_spec.match_file(posix_path):
            return True

        ignored = None
        # A .gitignore applies to descendants of its own directory. Loading
        # specs lazily keeps traversal bounded and preserves nested semantics.
        for depth in range(len(relative.parts)):
            base = Path(*relative.parts[:depth]) if depth else Path()
            if base not in self._git_specs:
                self._git_specs[base] = self._load_spec(self.root / base / ".gitignore")
            spec = self._git_specs[base]
            if spec is None:
                continue
            local = Path(*relative.parts[depth:]).as_posix()
            if is_dir and not local.endswith("/"):
                local += "/"
            decision = spec.check_file(local).include
            if decision is not None:
                ignored = decision
        return bool(ignored)


class FileRetriever:
    def __init__(
        self,
        root: Path,
        max_files: int = 5000,
        max_scan_entries: int = 20000,
        max_text_file_bytes: int = 1048576,
        max_total_text_bytes: int = 16777216,
        max_range_lines: int = 200,
        deadline_check: Optional[Callable[[], None]] = None,
    ) -> None:
        if (
            max_files <= 0
            or max_scan_entries <= 0
            or max_text_file_bytes <= 0
            or max_total_text_bytes <= 0
            or max_range_lines <= 0
        ):
            raise ValueError("File retrieval limits must be positive.")
        self.boundary = PathBoundary(root)
        self.root = self.boundary.root
        self.ignore = IgnorePolicy(self.root)
        self.max_files = max_files
        self.max_scan_entries = max_scan_entries
        self.max_text_file_bytes = max_text_file_bytes
        self.max_total_text_bytes = max_total_text_bytes
        self.max_range_lines = max_range_lines
        self.deadline_check = deadline_check
        self.last_scan_truncated = False
        self.last_search_truncated = False

    def iter_files(self, under: Union[str, Path] = ".") -> Iterator[Path]:
        resolved = self.boundary.resolve_for_read(under)
        if not resolved.absolute.is_dir():
            raise ToolError("File search root is not a directory.")

        yielded = 0
        scanned = 0
        stack = [resolved.absolute]
        self.last_scan_truncated = False
        scan_limit_hit = False
        while stack and yielded < self.max_files and scanned < self.max_scan_entries:
            directory = stack.pop()
            remaining_entries = self.max_scan_entries - scanned
            try:
                with os.scandir(str(directory)) as scanner:
                    entries = list(islice(scanner, remaining_entries + 1))
                if len(entries) > remaining_entries:
                    scan_limit_hit = True
                entries = sorted(
                    entries[:remaining_entries], key=lambda item: item.name
                )
            except OSError:
                continue
            child_directories = []
            for entry in entries:
                if scanned >= self.max_scan_entries:
                    break
                scanned += 1
                if self.deadline_check is not None and scanned % 128 == 0:
                    self.deadline_check()
                path = Path(entry.path)
                try:
                    relative = path.relative_to(self.root)
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not self.ignore.is_ignored(
                            relative, is_dir=True
                        ) and not self.ignore.is_sensitive(relative):
                            child_directories.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if self.ignore.is_ignored(relative) or self.ignore.is_sensitive(
                    relative
                ):
                    continue
                yield path
                yielded += 1
                if yielded >= self.max_files:
                    break
            stack.extend(reversed(child_directories))
        if (
            scan_limit_hit
            or stack
            or yielded >= self.max_files
            or scanned >= self.max_scan_entries
        ):
            self.last_scan_truncated = True

    def find_by_name(
        self,
        query: str,
        under: Union[str, Path] = ".",
        limit: int = 20,
    ) -> Tuple[FileMatch, ...]:
        normalized = query.strip()
        if not normalized:
            raise ToolError("Filename query must not be blank.")
        if limit <= 0:
            raise ToolError("Filename result limit must be positive.")
        use_pattern = any(character in normalized for character in "*?[")
        folded = normalized.casefold()
        matches: List[FileMatch] = []
        for path in self.iter_files(under):
            relative = path.relative_to(self.root).as_posix()
            name = path.name
            selected = (
                fnmatch.fnmatchcase(name.casefold(), folded)
                if use_pattern
                else folded in name.casefold()
            )
            if not selected:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            matches.append(FileMatch(relative_path=relative, size_bytes=size))
            if len(matches) >= limit:
                break
        return tuple(matches)

    def search_text(
        self,
        query: str,
        under: Union[str, Path] = ".",
        limit: int = 20,
    ) -> Tuple[TextMatch, ...]:
        normalized = query.strip()
        if not normalized:
            raise ToolError("Text query must not be blank.")
        if limit <= 0:
            raise ToolError("Text result limit must be positive.")
        folded = normalized.casefold()
        matches: List[TextMatch] = []
        total_bytes = 0
        self.last_search_truncated = False
        for path in self.iter_files(under):
            try:
                size = path.stat().st_size
                if size > self.max_text_file_bytes:
                    continue
                if total_bytes + size > self.max_total_text_bytes:
                    self.last_search_truncated = True
                    break
                total_bytes += size
                data = _read_file_bounded(path, self.max_text_file_bytes)
            except (OSError, ToolError):
                continue
            if is_binary_data(data):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if folded not in line.casefold():
                    continue
                compact = line.strip()
                if len(compact) > 500:
                    compact = compact[:497] + "..."
                matches.append(
                    TextMatch(
                        relative_path=path.relative_to(self.root).as_posix(),
                        line_number=number,
                        line=compact,
                    )
                )
                if len(matches) >= limit:
                    self.last_search_truncated = True
                    return tuple(matches)
        self.last_search_truncated = (
            self.last_search_truncated or self.last_scan_truncated
        )
        return tuple(matches)

    def read_range(
        self,
        requested: Union[str, Path],
        start_line: int,
        end_line: int,
    ) -> FileExcerpt:
        if start_line <= 0 or end_line < start_line:
            raise ToolError("Line range is invalid.")
        if end_line - start_line + 1 > self.max_range_lines:
            raise ToolError(
                "Line range exceeds the {0}-line limit.".format(self.max_range_lines)
            )
        raw = Path(str(requested))
        if not raw.is_absolute() and self.ignore.is_sensitive(raw):
            raise PolicyDeniedError("Refusing to read a likely secret path.")

        resolved = self.boundary.resolve_for_read(requested)
        if self.deadline_check is not None:
            self.deadline_check()
        relative = resolved.relative
        if self.ignore.is_sensitive(relative):
            raise PolicyDeniedError("Refusing to read a likely secret path.")
        if self.ignore.is_ignored(relative):
            raise PolicyDeniedError("Refusing to read an ignored path.")
        try:
            metadata = resolved.absolute.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ToolError("Requested path is not a regular file.")
            if metadata.st_size > self.max_text_file_bytes:
                raise ToolError(
                    "File exceeds the {0}-byte read limit.".format(
                        self.max_text_file_bytes
                    )
                )
            data = _read_file_bounded(resolved.absolute, self.max_text_file_bytes)
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError("Unable to read requested file.") from exc
        if is_binary_data(data):
            raise PolicyDeniedError("Refusing to read a binary file as text.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PolicyDeniedError("Refusing to decode a non-UTF-8 file.") from exc
        lines = text.splitlines()
        total_lines = len(lines)
        selected = lines[start_line - 1 : end_line]
        actual_end = start_line + len(selected) - 1
        if not selected:
            actual_end = start_line - 1
        return FileExcerpt(
            relative_path=relative.as_posix(),
            start_line=start_line,
            end_line=actual_end,
            total_lines=total_lines,
            content="\n".join(selected),
            sha256=hashlib.sha256(data).hexdigest(),
            truncated=start_line > 1 or actual_end < total_lines,
        )

    def manifest_excerpts(
        self,
        names: Sequence[str] = DEFAULT_MANIFEST_NAMES,
        max_lines: int = 120,
    ) -> Tuple[FileExcerpt, ...]:
        excerpts = []
        for name in names:
            path = self.root / name
            if not path.is_file() or path.is_symlink():
                continue
            try:
                excerpts.append(self.read_range(name, 1, max_lines))
            except (PolicyDeniedError, ToolError):
                continue
        return tuple(excerpts)
