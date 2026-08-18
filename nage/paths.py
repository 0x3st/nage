import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import ConfigurationError


@dataclass(frozen=True)
class AppPaths:
    """All persistent paths used by Nage.

    Passing an explicit root keeps tests and migrations away from the real
    user directory.
    """

    root: Path
    config_root: Optional[Path] = None
    cache_root: Optional[Path] = None

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(Path.home() / ".nage")

    @classmethod
    def from_home(cls, home: Path) -> "AppPaths":
        return cls(Path(home) / ".nage")

    @property
    def config_dir(self) -> Path:
        return self.config_root or self.root

    @property
    def cache_dir(self) -> Path:
        return self.cache_root or self.root

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def database_file(self) -> Path:
        return self.root / "nage.sqlite3"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def legacy_settings_file(self) -> Path:
        return self.root / "SETT"

    @property
    def legacy_memory_file(self) -> Path:
        return self.root / "MEMO"

    @property
    def legacy_history_file(self) -> Path:
        return self.root / "HIST"

    def ensure(self) -> "AppPaths":
        directories = []
        for directory in (
            self.root,
            self.config_dir,
            self.cache_dir,
            self.artifacts_dir,
            self.backups_dir,
        ):
            if directory not in directories:
                directories.append(directory)
        for directory in directories:
            self._ensure_private_directory(directory)
        return self

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        if path.exists() and not path.is_dir():
            raise ConfigurationError(
                "Nage data path is not a directory: {0}".format(path)
            )

        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                path.chmod(0o700)
        except OSError as exc:
            raise ConfigurationError(
                "Unable to create private Nage directory {0}: {1}".format(path, exc)
            ) from exc
