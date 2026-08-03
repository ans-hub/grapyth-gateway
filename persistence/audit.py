from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Callable

from .models import AuditReadModel


MAX_AUDIT_DETAILS_BYTES = 8000


def serialize_details(details: dict[str, Any]) -> str:
    serialized = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    size = len(serialized.encode("utf-8"))
    if size <= MAX_AUDIT_DETAILS_BYTES:
        return serialized
    marker = {
        "truncated": True,
        "originalBytes": size,
        "fields": sorted(str(key)[:160] for key in details),
    }
    serialized_marker = json.dumps(marker, ensure_ascii=False, separators=(",", ":"))
    if len(serialized_marker.encode("utf-8")) <= MAX_AUDIT_DETAILS_BYTES:
        return serialized_marker
    return json.dumps(
        {"truncated": True, "originalBytes": size, "fieldCount": len(details)},
        separators=(",", ":"),
    )


def _safe_details(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"invalidJson": True}
    if isinstance(parsed, dict):
        return parsed
    return {"invalidJson": True}


def write(
    connection: sqlite3.Connection,
    *,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any],
    now: Callable[[], str],
) -> None:
    connection.execute(
        "INSERT INTO audit_log(id, action, target_type, target_id, details_json, created_at) "
        "VALUES (:id, :action, :target_type, :target_id, :details_json, :created_at)",
        {
            "id": "audit-" + uuid.uuid4().hex,
            "action": action[:160],
            "target_type": target_type[:80],
            "target_id": target_id[:200],
            "details_json": serialize_details(details),
            "created_at": now(),
        },
    )


def list_recent(connection: sqlite3.Connection, *, limit: int) -> list[AuditReadModel]:
    rows = connection.execute(
        "SELECT id, action, target_type, target_id, details_json, created_at FROM audit_log "
        "ORDER BY created_at DESC LIMIT :limit",
        {"limit": limit},
    ).fetchall()
    return [
        AuditReadModel(
            id=str(row["id"]),
            action=str(row["action"]),
            target_type=str(row["target_type"]),
            target_id=str(row["target_id"]),
            details_json=str(row["details_json"]),
            details=_safe_details(row["details_json"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]
