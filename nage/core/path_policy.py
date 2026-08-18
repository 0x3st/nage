from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple, Union

from ..errors import PolicyDeniedError, ToolError


@dataclass(frozen=True)
class CanonicalPath:
    requested: str
    absolute: Path
    relative: Path


class PathBoundary:
    """Resolve tool paths without allowing escape from an approved root."""

    def __init__(
        self,
        root: Path,
        protected_paths: Iterable[Path] = (),
    ) -> None:
        root = Path(root)
        try:
            resolved = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ToolError("Approved path root does not exist.") from exc
        if not resolved.is_dir():
            raise ToolError("Approved path root is not a directory.")
        self.root = resolved
        normalized = []
        for protected in protected_paths:
            try:
                protected_path = Path(protected).resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise ToolError("Unable to resolve a protected path.") from exc
            normalized.append(protected_path)
        self.protected_paths: Tuple[Path, ...] = tuple(normalized)

    def resolve_for_read(
        self,
        requested: Union[str, Path],
        must_exist: bool = True,
    ) -> CanonicalPath:
        return self._resolve(
            requested=requested,
            must_exist=must_exist,
            reject_target_symlink=False,
        )

    def resolve_for_write(
        self,
        requested: Union[str, Path],
        must_exist: bool = False,
    ) -> CanonicalPath:
        return self._resolve(
            requested=requested,
            must_exist=must_exist,
            reject_target_symlink=True,
        )

    def _resolve(
        self,
        requested: Union[str, Path],
        must_exist: bool,
        reject_target_symlink: bool,
    ) -> CanonicalPath:
        requested_text = str(requested)
        if not requested_text or "\x00" in requested_text:
            raise ToolError("Path must be a non-empty valid string.")

        raw = Path(requested_text)
        candidate = raw if raw.is_absolute() else self.root / raw
        if reject_target_symlink and candidate.is_symlink():
            raise PolicyDeniedError("Refusing to write through a symbolic link.")

        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise ToolError("Requested path does not exist.") from exc
        except (OSError, RuntimeError) as exc:
            raise ToolError("Unable to resolve requested path.") from exc

        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise PolicyDeniedError(
                "Requested path is outside the approved root."
            ) from exc

        if reject_target_symlink and any(
            resolved == protected or protected in resolved.parents
            for protected in self.protected_paths
        ):
            raise PolicyDeniedError("Requested path is protected application state.")
        if must_exist and not resolved.exists():
            raise ToolError("Requested path does not exist.")
        return CanonicalPath(
            requested=requested_text,
            absolute=resolved,
            relative=relative,
        )
