import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from ..errors import StorageError
from ..paths import AppPaths


@dataclass(frozen=True)
class LegacyBackup:
    path: Path
    files: Tuple[str, ...]


class LegacyBackupManager:
    LEGACY_NAMES = ("SETT", "MEMO", "HIST")
    MANIFEST_NAME = "manifest.json"

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def create(self) -> Optional[LegacyBackup]:
        sources = self._legacy_sources()
        if not sources:
            return None

        self.paths.ensure()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.paths.backups_dir / "legacy-v1-{0}-{1}".format(
            timestamp, uuid.uuid4().hex[:8]
        )

        try:
            backup_path.mkdir(mode=0o700)
            if os.name != "nt":
                backup_path.chmod(0o700)

            manifest_files: Dict[str, Dict[str, object]] = {}
            for name, source in sources.items():
                destination = backup_path / name
                shutil.copyfile(str(source), str(destination))
                if os.name != "nt":
                    destination.chmod(0o600)
                data = destination.read_bytes()
                manifest_files[name] = {
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }

            manifest = {
                "format_version": 1,
                "created_at": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "files": manifest_files,
            }
            manifest_path = backup_path / self.MANIFEST_NAME
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                manifest_path.chmod(0o600)
            return LegacyBackup(backup_path, tuple(sorted(sources)))
        except (OSError, ValueError) as exc:
            shutil.rmtree(str(backup_path), ignore_errors=True)
            raise StorageError(
                "Unable to create legacy-data backup: {0}".format(exc)
            ) from exc

    def verify(self, backup_path: Path) -> LegacyBackup:
        backup_path = Path(backup_path)
        self._validate_backup_directory(backup_path)
        manifest_path = backup_path / self.MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError("Unable to read backup manifest.") from exc

        if manifest.get("format_version") != 1:
            raise StorageError("Unsupported legacy backup format.")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise StorageError("Legacy backup manifest has no files.")

        verified = []
        for name, metadata in files.items():
            if name not in self.LEGACY_NAMES or not isinstance(metadata, dict):
                raise StorageError("Legacy backup manifest contains unsafe entries.")
            source = backup_path / name
            if source.is_symlink() or not source.is_file():
                raise StorageError("Legacy backup file is missing or unsafe.")
            data = source.read_bytes()
            if metadata.get("size_bytes") != len(data):
                raise StorageError("Legacy backup size verification failed.")
            if metadata.get("sha256") != hashlib.sha256(data).hexdigest():
                raise StorageError("Legacy backup hash verification failed.")
            verified.append(name)

        return LegacyBackup(backup_path, tuple(sorted(verified)))

    def restore(self, backup_path: Path, overwrite: bool = False) -> LegacyBackup:
        backup = self.verify(backup_path)
        self.paths.ensure()
        token = uuid.uuid4().hex
        staged: Dict[str, Path] = {}
        originals: Dict[str, Path] = {}
        installed = []

        for name in backup.files:
            target = self.paths.root / name
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise StorageError("Legacy restore target is unsafe: {0}".format(name))
            if target.exists() and not overwrite:
                raise StorageError(
                    "Legacy restore target already exists: {0}".format(name)
                )

        try:
            for name in backup.files:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=".restore-{0}-".format(token),
                    suffix="-{0}".format(name),
                    dir=str(self.paths.root),
                )
                os.close(descriptor)
                temporary = Path(temporary_name)
                shutil.copyfile(str(backup.path / name), str(temporary))
                if os.name != "nt":
                    temporary.chmod(0o600)
                staged[name] = temporary

            for name in backup.files:
                target = self.paths.root / name
                if target.exists():
                    rollback = self.paths.root / ".rollback-{0}-{1}".format(token, name)
                    os.replace(str(target), str(rollback))
                    originals[name] = rollback
                os.replace(str(staged.pop(name)), str(target))
                installed.append(name)

            for rollback in originals.values():
                try:
                    rollback.unlink()
                except OSError:
                    # A stale rollback copy is safer than turning successful
                    # restoration into a data-loss recovery path.
                    pass
            return backup
        except OSError as exc:
            recovery_errors = []
            for name in installed:
                target = self.paths.root / name
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                except OSError as recovery_error:
                    recovery_errors.append(str(recovery_error))
            for name, rollback in originals.items():
                if rollback.exists():
                    try:
                        os.replace(str(rollback), str(self.paths.root / name))
                    except OSError as recovery_error:
                        # Keep the rollback file in place for manual recovery.
                        recovery_errors.append(str(recovery_error))
            detail = "Unable to restore legacy-data backup: {0}".format(exc)
            if recovery_errors:
                detail += "; manual recovery may be required: {0}".format(
                    "; ".join(recovery_errors)
                )
            raise StorageError(detail) from exc
        finally:
            for temporary in staged.values():
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _legacy_sources(self) -> Dict[str, Path]:
        sources: Dict[str, Path] = {}
        for name in self.LEGACY_NAMES:
            source = self.paths.root / name
            if not source.exists() and not source.is_symlink():
                continue
            if source.is_symlink() or not source.is_file():
                raise StorageError(
                    "Legacy Nage path is not a safe regular file: {0}".format(source)
                )
            sources[name] = source
        return sources

    def _validate_backup_directory(self, backup_path: Path) -> None:
        if backup_path.is_symlink() or not backup_path.is_dir():
            raise StorageError("Legacy backup path is missing or unsafe.")
        try:
            expected = self.paths.backups_dir.resolve()
            actual = backup_path.parent.resolve()
        except OSError as exc:
            raise StorageError("Unable to validate legacy backup path.") from exc
        if actual != expected:
            raise StorageError("Legacy backup is outside the Nage backup directory.")
