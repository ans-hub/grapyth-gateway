from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


def connect_sqlite(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    resolved = path.resolve()
    if readonly:
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=20)
        connection.execute("PRAGMA query_only=ON")
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(resolved, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=20000")
    connection.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        _enable_wal(connection)
    return connection


@contextmanager
def transactional_connection(
    connect: Callable[[], sqlite3.Connection],
) -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _enable_wal(connection: sqlite3.Connection) -> None:
    deadline = time.monotonic() + 20.0
    while True:
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                connection.close()
                raise
            time.sleep(0.02)
