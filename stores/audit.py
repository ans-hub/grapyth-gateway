from __future__ import annotations

import sqlite3
from typing import Any

from ..database import utc_now
from ..persistence import audit as audit_repository


def write_audit_event(
    connection: sqlite3.Connection,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any],
) -> None:
    audit_repository.write(
        connection,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        now=utc_now,
    )
