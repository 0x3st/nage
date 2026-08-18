import hashlib
import os
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..errors import StorageError
from ..paths import AppPaths
from .database import Database


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    sensitivity: str
    created_at: str
    expires_at: str


class ArtifactStore:
    ALLOWED_SENSITIVITIES = {"normal", "sensitive"}

    def __init__(self, database: Database, paths: AppPaths) -> None:
        self.database = database
        self.paths = paths

    @staticmethod
    def _format_time(value: datetime) -> str:
        normalized = value.astimezone(timezone.utc)
        return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def create_bytes(
        self,
        data: bytes,
        media_type: str = "application/octet-stream",
        sensitivity: str = "normal",
        ttl_seconds: int = 86400,
    ) -> ArtifactRecord:
        if not isinstance(data, bytes):
            raise StorageError("Artifact data must be bytes.")
        if not media_type or not isinstance(media_type, str):
            raise StorageError("Artifact media type must be a non-empty string.")
        if sensitivity not in self.ALLOWED_SENSITIVITIES:
            raise StorageError("Unsupported artifact sensitivity.")
        if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise StorageError("Artifact lifetime must be a positive integer.")

        self.paths.ensure()
        artifact_id = uuid.uuid4().hex
        filename = "{0}.bin".format(artifact_id)
        relative_path = (Path("artifacts") / filename).as_posix()
        target = self.paths.artifacts_dir / filename
        temporary_path = None
        created = datetime.now(timezone.utc)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            relative_path=relative_path,
            media_type=media_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            sensitivity=sensitivity,
            created_at=self._format_time(created),
            expires_at=self._format_time(created + timedelta(seconds=ttl_seconds)),
        )

        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".artifact-",
                suffix=".tmp",
                dir=str(self.paths.artifacts_dir),
            )
            temporary_path = Path(temporary_name)
            if os.name != "nt":
                os.chmod(temporary_name, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(str(temporary_path), str(target))
            temporary_path = None
            if os.name != "nt":
                target.chmod(0o600)

            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id, relative_path, media_type, size_bytes,
                        sha256, sensitivity, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.artifact_id,
                        record.relative_path,
                        record.media_type,
                        record.size_bytes,
                        record.sha256,
                        record.sensitivity,
                        record.created_at,
                        record.expires_at,
                    ),
                )
            return record
        except StorageError:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise
        except (OSError, sqlite3.Error) as exc:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise StorageError(
                "Unable to create local artifact: {0}".format(exc)
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def create_text(
        self,
        text: str,
        media_type: str = "text/plain; charset=utf-8",
        sensitivity: str = "normal",
        ttl_seconds: int = 86400,
    ) -> ArtifactRecord:
        if not isinstance(text, str):
            raise StorageError("Artifact text must be a string.")
        return self.create_bytes(
            text.encode("utf-8"),
            media_type=media_type,
            sensitivity=sensitivity,
            ttl_seconds=ttl_seconds,
        )

    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            return None
        return ArtifactRecord(**dict(row))

    def read_bytes(self, artifact_id: str, max_bytes: int) -> bytes:
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 0
        ):
            raise StorageError("Artifact read limit must be a non-negative integer.")
        record = self.get(artifact_id)
        if record is None:
            raise StorageError("Artifact does not exist: {0}".format(artifact_id))
        if record.size_bytes > max_bytes:
            raise StorageError(
                "Artifact is {0} bytes, above the {1}-byte read limit.".format(
                    record.size_bytes, max_bytes
                )
            )

        path = self._absolute_path(record.relative_path)
        if path.is_symlink() or not path.is_file():
            raise StorageError("Artifact file is missing or unsafe.")
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
                raise StorageError("Artifact path is not a regular file.")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = None
                data = source.read(max_bytes + 1)
        except OSError as exc:
            raise StorageError("Unable to read artifact: {0}".format(exc)) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(data) > max_bytes:
            raise StorageError("Artifact content exceeds the read limit.")
        if len(data) != record.size_bytes:
            raise StorageError("Artifact size/hash does not match its metadata.")
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise StorageError("Artifact content hash does not match its metadata.")
        return data

    def delete(self, artifact_id: str) -> bool:
        record = self.get(artifact_id)
        if record is None:
            return False
        path = self._absolute_path(record.relative_path)

        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                )
                if path.exists() or path.is_symlink():
                    path.unlink()
        except (OSError, sqlite3.Error) as exc:
            raise StorageError("Unable to delete artifact: {0}".format(exc)) from exc
        return True

    def cleanup_expired(self, now: Optional[datetime] = None) -> int:
        current = now or datetime.now(timezone.utc)
        threshold = self._format_time(current)
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE expires_at <= ?",
                (threshold,),
            ).fetchall()

        removed = 0
        for row in rows:
            if self.delete(row["artifact_id"]):
                removed += 1
        return removed

    def _absolute_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise StorageError("Artifact metadata contains an unsafe path.")
        candidate = self.paths.root / relative
        try:
            expected_parent = self.paths.artifacts_dir.resolve()
            actual_parent = candidate.parent.resolve()
        except OSError as exc:
            raise StorageError("Unable to validate artifact path.") from exc
        if actual_parent != expected_parent:
            raise StorageError("Artifact path escapes the artifact directory.")
        return candidate
