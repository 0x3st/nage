import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from ..config import AppConfig, ConfigStore
from ..errors import StorageError
from ..paths import AppPaths
from .backups import LegacyBackup, LegacyBackupManager
from .database import Database
from .legacy_memory import LegacyMemoryMigrator
from .memories import MemoryStore


@dataclass(frozen=True)
class LegacyMigrationReport:
    backup_path: str
    settings_status: str
    memory: Optional[Mapping[str, Any]]
    history_status: str
    history_entries: int
    already_migrated: bool = False

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "backup_path": self.backup_path,
            "settings_status": self.settings_status,
            "memory": None if self.memory is None else dict(self.memory),
            "history_status": self.history_status,
            "history_entries": self.history_entries,
            "already_migrated": self.already_migrated,
        }


class LegacyMigrationCoordinator:
    MARKER_NAME = "legacy_v1_complete"
    MAXIMUM_FILE_BYTES = 4194304

    def __init__(
        self,
        paths: AppPaths,
        database: Database,
        config_store: ConfigStore,
        memories: MemoryStore,
    ) -> None:
        self.paths = paths
        self.database = database
        self.config_store = config_store
        self.memories = memories
        self.backups = LegacyBackupManager(paths)

    def migrate(self) -> Optional[LegacyMigrationReport]:
        existing = self.report()
        if existing is not None:
            return LegacyMigrationReport(
                backup_path=existing.backup_path,
                settings_status=existing.settings_status,
                memory=existing.memory,
                history_status=existing.history_status,
                history_entries=existing.history_entries,
                already_migrated=True,
            )
        backup = self.backups.create()
        if backup is None:
            return None

        config = self._prepare_config(backup)
        history_status, history_entries = self._inspect_history(backup)
        memory_report = LegacyMemoryMigrator(self.paths, self.memories).migrate(
            backup=backup
        )

        settings_status = "missing"
        if config is not None:
            if self.paths.config_file.exists():
                # A validated v2 configuration is authoritative over legacy
                # settings on an interrupted or repeated migration.
                self.config_store.load()
                settings_status = "existing_v2_preserved"
            else:
                self.config_store.save(config)
                settings_status = "migrated"

        report = LegacyMigrationReport(
            backup_path=str(backup.path),
            settings_status=settings_status,
            memory=None if memory_report is None else memory_report.to_mapping(),
            history_status=history_status,
            history_entries=history_entries,
        )
        self._save_marker(report)
        return report

    def report(self) -> Optional[LegacyMigrationReport]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM legacy_migrations WHERE name = ?",
                (self.MARKER_NAME,),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["report_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise StorageError("Legacy migration report is corrupt.") from exc
        return LegacyMigrationReport(
            backup_path=row["backup_path"],
            settings_status=value["settings_status"],
            memory=value.get("memory"),
            history_status=value["history_status"],
            history_entries=int(value["history_entries"]),
            already_migrated=bool(value.get("already_migrated", False)),
        )

    def rollback(self, overwrite: bool = True) -> LegacyBackup:
        report = self.report()
        if report is None:
            raise StorageError(
                "No completed legacy migration is available to roll back."
            )
        return self.backups.restore(report.backup_path, overwrite=overwrite)

    def _prepare_config(self, backup: LegacyBackup) -> Optional[AppConfig]:
        if "SETT" not in backup.files:
            return None
        value = self._read_json(backup, "SETT")
        if not isinstance(value, dict):
            raise StorageError("Legacy SETT root must be an object.")
        key = value.get("key", "")
        if not isinstance(key, str):
            raise StorageError("Legacy API key must be text.")
        return AppConfig(
            model=value.get("model", ""),
            endpoint=value.get("endpoint", ""),
            api_key=key,
            memory_enabled=True,
        )

    def _inspect_history(self, backup: LegacyBackup) -> tuple:
        if "HIST" not in backup.files:
            return "missing", 0
        try:
            value = self._read_json(backup, "HIST")
        except StorageError:
            return "malformed_preserved", 0
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            return "malformed_preserved", 0
        return "preserved_in_backup_only", len(value)

    def _read_json(self, backup: LegacyBackup, name: str) -> Any:
        path = backup.path / name
        try:
            if path.stat().st_size > self.MAXIMUM_FILE_BYTES:
                raise StorageError("Legacy {0} exceeds the size limit.".format(name))
            return json.loads(path.read_text(encoding="utf-8"))
        except StorageError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError("Legacy {0} is malformed.".format(name)) from exc

    def _save_marker(self, report: LegacyMigrationReport) -> None:
        serialized = json.dumps(
            report.to_mapping(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO legacy_migrations(
                        name, backup_path, report_json, completed_at
                    ) VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (self.MARKER_NAME, report.backup_path, serialized),
                )
        except sqlite3.IntegrityError:
            # A concurrent migration completed first. Its marker is canonical.
            if self.report() is None:
                raise StorageError("Legacy migration marker could not be stored.")
        except sqlite3.Error as exc:
            raise StorageError("Unable to store legacy migration marker.") from exc
