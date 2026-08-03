from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

from .persistence.connection import connect_sqlite, transactional_connection
from .persistence.migrations import MigrationRunner


MIGRATION_RUNNER = MigrationRunner()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class GatewayDatabase:
    """Own Gateway SQLite connections, schema lifecycle, and backup operations."""

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with transactional_connection(self.connect) as connection:
            yield connection

    def initialize(self) -> None:
        MIGRATION_RUNNER.initialize(self.connect)

    def verify_schema(self) -> None:
        with closing(self.connect()) as connection:
            MIGRATION_RUNNER.assert_current(connection)

    def backup(self, target: Path) -> Path:
        if not self.path.is_file():
            raise FileNotFoundError("Gateway database not found")
        resolved_target = target.resolve()
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
        temporary = resolved_target.with_name(
            f".{resolved_target.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with closing(self.connect()) as source, closing(
                sqlite3.connect(temporary)
            ) as destination:
                source.backup(destination)
            self.verify_backup(temporary)
            os.replace(temporary, resolved_target)
        finally:
            temporary.unlink(missing_ok=True)
        return resolved_target

    @staticmethod
    def verify_backup(source: Path) -> None:
        resolved_source = source.resolve()
        if not resolved_source.is_file():
            raise FileNotFoundError("Gateway backup not found")
        with closing(connect_sqlite(resolved_source, readonly=True)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Gateway backup failed its integrity check")
            foreign_key_failures = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_failures:
                raise RuntimeError("Gateway backup failed its foreign-key check")
            MIGRATION_RUNNER.assert_backup_compatible(connection)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        required = {
            "installations",
            "calls",
            "credit_ledger",
            "provider_credentials",
        }
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(
                f"Gateway backup is missing required tables: {', '.join(missing)}"
            )
