import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..errors import StorageError
from ..memory import (
    contains_highly_sensitive_data,
    contains_secret,
    normalize_memory_content,
)
from ..paths import AppPaths
from .backups import LegacyBackup, LegacyBackupManager
from .memories import MemoryStore


@dataclass(frozen=True)
class LegacyMemoryMigrationReport:
    backup_path: str
    imported: int
    active: int
    review_candidates: int
    skipped_empty: int
    skipped_duplicate: int
    skipped_invalid: int
    skipped_sensitive: int = 0
    already_migrated: bool = False

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "backup_path": self.backup_path,
            "imported": self.imported,
            "active": self.active,
            "review_candidates": self.review_candidates,
            "skipped_empty": self.skipped_empty,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_invalid": self.skipped_invalid,
            "skipped_sensitive": self.skipped_sensitive,
            "already_migrated": self.already_migrated,
        }


class LegacyMemoryMigrator:
    MAXIMUM_LEGACY_BYTES = 1048576

    def __init__(self, paths: AppPaths, memories: MemoryStore) -> None:
        self.paths = paths
        self.memories = memories
        self.backups = LegacyBackupManager(paths)

    def migrate(
        self,
        backup: Optional[LegacyBackup] = None,
    ) -> Optional[LegacyMemoryMigrationReport]:
        existing = self.memories.legacy_report()
        if existing is not None:
            return LegacyMemoryMigrationReport(
                backup_path=str(existing["backup_path"]),
                imported=int(existing.get("imported", 0)),
                active=int(existing.get("active", 0)),
                review_candidates=int(existing.get("review_candidates", 0)),
                skipped_empty=int(existing.get("skipped_empty", 0)),
                skipped_duplicate=int(existing.get("skipped_duplicate", 0)),
                skipped_invalid=int(existing.get("skipped_invalid", 0)),
                skipped_sensitive=int(existing.get("skipped_sensitive", 0)),
                already_migrated=True,
            )

        source = self.paths.legacy_memory_file
        if not source.exists() and not source.is_symlink():
            return None
        if source.is_symlink() or not source.is_file():
            raise StorageError("Legacy MEMO path is not a safe regular file.")
        try:
            if source.stat().st_size > self.MAXIMUM_LEGACY_BYTES:
                raise StorageError("Legacy MEMO exceeds the migration size limit.")
        except OSError as exc:
            raise StorageError("Unable to inspect legacy MEMO.") from exc

        if backup is None:
            backup = self.backups.create()
        else:
            backup = self.backups.verify(backup.path)
        if backup is None or "MEMO" not in backup.files:
            raise StorageError("Legacy backup was not created before migration.")
        try:
            raw = source.read_text(encoding="utf-8")
            values = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError(
                "Legacy MEMO is malformed; backup was preserved at {0}.".format(
                    backup.path
                )
            ) from exc
        if not isinstance(values, list):
            raise StorageError(
                "Legacy MEMO root must be a list; backup was preserved at {0}.".format(
                    backup.path
                )
            )

        entries = []
        seen = set()
        skipped_empty = 0
        skipped_duplicate = 0
        skipped_invalid = 0
        skipped_sensitive = 0
        active = 0
        review_candidates = 0
        for index, value in enumerate(values):
            if not isinstance(value, str):
                skipped_invalid += 1
                continue
            normalized = normalize_memory_content(value)
            if not normalized:
                skipped_empty += 1
                continue
            identity = normalized.casefold()
            if identity in seen:
                skipped_duplicate += 1
                continue
            seen.add(identity)
            requires_review = contains_secret(
                normalized
            ) or contains_highly_sensitive_data(normalized)
            if requires_review:
                skipped_sensitive += 1
                continue
            status = "active"
            sensitivity = "normal"
            active += 1
            entries.append(
                {
                    "content": normalized,
                    "kind": "fact",
                    "scope_type": "global",
                    "scope_key": "",
                    "source_type": "legacy_import",
                    "confidence": 1.0,
                    "sensitivity": sensitivity,
                    "status": status,
                    "source_reference": "legacy:MEMO:{0}".format(index),
                    "expires_at": None,
                    "source_hash": hashlib.sha256(
                        normalized.encode("utf-8")
                    ).hexdigest(),
                    "supersedes_id": None,
                }
            )

        report = LegacyMemoryMigrationReport(
            backup_path=str(backup.path),
            imported=len(entries),
            active=active,
            review_candidates=review_candidates,
            skipped_empty=skipped_empty,
            skipped_duplicate=skipped_duplicate,
            skipped_invalid=skipped_invalid,
            skipped_sensitive=skipped_sensitive,
        )
        created = self.memories.import_legacy(
            entries=entries,
            backup_path=str(backup.path),
            report=report.to_mapping(),
        )
        if created:
            return report
        existing = self.memories.legacy_report()
        if existing is None:
            raise StorageError("Legacy memory migration marker was not persisted.")
        return LegacyMemoryMigrationReport(
            backup_path=str(existing["backup_path"]),
            imported=int(existing.get("imported", 0)),
            active=int(existing.get("active", 0)),
            review_candidates=int(existing.get("review_candidates", 0)),
            skipped_empty=int(existing.get("skipped_empty", 0)),
            skipped_duplicate=int(existing.get("skipped_duplicate", 0)),
            skipped_invalid=int(existing.get("skipped_invalid", 0)),
            skipped_sensitive=int(existing.get("skipped_sensitive", 0)),
            already_migrated=True,
        )
