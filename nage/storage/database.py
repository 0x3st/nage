import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..errors import MigrationError, StorageError
from .migrations import apply_migrations, current_schema_version


class Database:
    def __init__(self, path: Path, timeout_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds

    def _open(self) -> sqlite3.Connection:
        if self.path.exists() and not self.path.is_file():
            raise StorageError(
                "Nage database path is not a file: {0}".format(self.path)
            )

        connection = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(self.path),
                timeout=self.timeout_seconds,
            )
            if os.name != "nt":
                self.path.chmod(0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "PRAGMA busy_timeout = {0}".format(
                    max(0, int(self.timeout_seconds * 1000))
                )
            )
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as exc:
                # Two first-run processes can race while one switches the
                # database to WAL. The migration transaction below still
                # serializes safely, and a later connection can set WAL.
                if "locked" not in str(exc).lower():
                    raise
            return connection
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise StorageError(
                "Unable to open Nage database {0}: {1}".format(self.path, exc)
            ) from exc
        except OSError as exc:
            if connection is not None:
                connection.close()
            raise StorageError(
                "Unable to prepare Nage database directory {0}: {1}".format(
                    self.path.parent, exc
                )
            ) from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def initialize(self) -> int:
        try:
            with self.connection() as connection:
                return apply_migrations(connection)
        except MigrationError:
            raise
        except sqlite3.Error as exc:
            raise StorageError(
                "Unable to initialize Nage database {0}: {1}".format(self.path, exc)
            ) from exc

    def schema_version(self) -> int:
        try:
            with self.connection() as connection:
                return current_schema_version(connection)
        except sqlite3.Error as exc:
            raise StorageError(
                "Unable to read Nage database schema version: {0}".format(exc)
            ) from exc
