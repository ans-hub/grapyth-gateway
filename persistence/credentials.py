from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from .models import ProviderCredentialReadModel


def _map(row: sqlite3.Row) -> ProviderCredentialReadModel:
    return ProviderCredentialReadModel(
        id=str(row["id"]),
        name=str(row["name"]),
        key_hint=str(row["key_hint"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def get(connection: sqlite3.Connection, credential_id: str) -> ProviderCredentialReadModel | None:
    row = connection.execute(
        "SELECT id, name, key_hint, enabled, created_at, updated_at "
        "FROM provider_credentials WHERE id=:credential_id",
        {"credential_id": credential_id},
    ).fetchone()
    return _map(row) if row else None


def list_all(connection: sqlite3.Connection) -> list[ProviderCredentialReadModel]:
    rows = connection.execute(
        "SELECT id, name, key_hint, enabled, created_at, updated_at "
        "FROM provider_credentials ORDER BY created_at"
    ).fetchall()
    return [_map(row) for row in rows]


def secret(connection: sqlite3.Connection, credential_id: str) -> tuple[str, bool] | None:
    row = connection.execute(
        "SELECT encrypted_api_key, enabled FROM provider_credentials WHERE id=:credential_id",
        {"credential_id": credential_id},
    ).fetchone()
    return (str(row["encrypted_api_key"]), bool(row["enabled"])) if row else None


def iter_enabled_secrets(connection: sqlite3.Connection) -> Iterator[str]:
    rows = connection.execute(
        "SELECT encrypted_api_key FROM provider_credentials WHERE enabled=1"
    )
    for row in rows:
        yield str(row["encrypted_api_key"])


def insert(
    connection: sqlite3.Connection,
    *,
    credential_id: str,
    name: str,
    encrypted_api_key: str,
    key_hint: str,
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO provider_credentials(id, name, encrypted_api_key, key_hint, enabled, created_at, updated_at) "
        "VALUES (:id, :name, :encrypted_api_key, :key_hint, 1, :created_at, :created_at)",
        {
            "id": credential_id,
            "name": name,
            "encrypted_api_key": encrypted_api_key,
            "key_hint": key_hint,
            "created_at": created_at,
        },
    )


def update(
    connection: sqlite3.Connection,
    *,
    credential_id: str,
    name: str,
    enabled: bool,
    encrypted_api_key: str | None,
    key_hint: str | None,
    updated_at: str,
) -> None:
    connection.execute(
        "UPDATE provider_credentials SET name=:name, enabled=:enabled, "
        "encrypted_api_key=COALESCE(:encrypted_api_key, encrypted_api_key), "
        "key_hint=COALESCE(:key_hint, key_hint), updated_at=:updated_at WHERE id=:id",
        {
            "id": credential_id,
            "name": name,
            "enabled": int(enabled),
            "encrypted_api_key": encrypted_api_key,
            "key_hint": key_hint,
            "updated_at": updated_at,
        },
    )
