from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass


SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
""".strip()

INITIAL_GATEWAY_SQL = """
CREATE TABLE provider_credentials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    encrypted_api_key TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE pricing_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    version TEXT NOT NULL,
    provider_input_usd TEXT NOT NULL,
    provider_cached_usd TEXT NOT NULL,
    provider_cache_write_usd TEXT NOT NULL,
    provider_output_usd TEXT NOT NULL,
    billed_input_usd TEXT NOT NULL,
    billed_cached_usd TEXT NOT NULL,
    billed_cache_write_usd TEXT NOT NULL,
    billed_output_usd TEXT NOT NULL,
    allow_below_cost INTEGER NOT NULL DEFAULT 0,
    below_cost_reason TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE installations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_hint TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    provider_credential_id TEXT NOT NULL DEFAULT '',
    pricing_plan_id TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT 'low',
    billing_mode TEXT NOT NULL DEFAULT 'prepaid',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE installation_balances (
    installation_id TEXT PRIMARY KEY REFERENCES installations(id) ON DELETE CASCADE,
    balance_microusd INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE calls (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    board_id TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    app_ai_call_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    resolved_model TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    provider_request_id TEXT NOT NULL DEFAULT '',
    estimated_provider_cost_usd TEXT,
    provider_cost_usd TEXT,
    charged_usd TEXT,
    margin_usd TEXT,
    pricing_plan_id TEXT NOT NULL DEFAULT '',
    pricing_version TEXT NOT NULL DEFAULT '',
    provider_rates_json TEXT NOT NULL DEFAULT '{}',
    billed_rates_json TEXT NOT NULL DEFAULT '{}',
    below_cost INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(installation_id, idempotency_key)
);
CREATE TABLE user_limits (
    installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    monthly_limit_usd TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(installation_id, user_id)
);
CREATE TABLE credit_ledger (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
    amount_usd TEXT NOT NULL,
    kind TEXT NOT NULL,
    call_id TEXT REFERENCES calls(id),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE gateway_defaults (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    provider_credential_id TEXT NOT NULL DEFAULT '',
    pricing_plan_id TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT 'low',
    billing_mode TEXT NOT NULL DEFAULT 'prepaid',
    updated_at TEXT NOT NULL
);
CREATE TABLE gateway_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    max_output_tokens INTEGER NOT NULL DEFAULT 64000,
    max_request_bytes INTEGER NOT NULL DEFAULT 5242880,
    updated_at TEXT NOT NULL
);
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX credit_ledger_call_id
    ON credit_ledger(call_id) WHERE call_id IS NOT NULL;
CREATE INDEX calls_installation_created
    ON calls(installation_id, created_at DESC);
CREATE INDEX calls_installation_user_created
    ON calls(installation_id, user_id, created_at DESC);
CREATE INDEX calls_status ON calls(status);
CREATE INDEX credit_ledger_installation_created
    ON credit_ledger(installation_id, created_at DESC);
CREATE INDEX audit_log_created ON audit_log(created_at DESC);
INSERT INTO gateway_defaults(singleton, updated_at)
    VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
INSERT INTO gateway_settings(singleton, updated_at)
    VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
"""

MANAGED_TERRA_PRICING_SQL = """
INSERT INTO audit_log(id, action, target_type, target_id, details_json, created_at)
SELECT
    'audit-' || lower(hex(randomblob(16))),
    'pricing_plan.managed_rates_migrated',
    'pricing_plan',
    id,
    '{"fromVersion":"grapyth-managed-2026-07-22","toVersion":"grapyth-managed-2026-08-03"}',
    strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM pricing_plans
WHERE name='Default managed pricing'
    AND model='gpt-5.6-terra'
    AND version='grapyth-managed-2026-07-22'
    AND provider_input_usd='2.500000'
    AND provider_cached_usd='0.250000'
    AND provider_cache_write_usd='3.125000'
    AND provider_output_usd='15.000000'
    AND billed_input_usd='2.500000'
    AND billed_cached_usd='0.250000'
    AND billed_cache_write_usd='3.125000'
    AND billed_output_usd='15.000000'
    AND allow_below_cost=0
    AND below_cost_reason=''
    AND enabled=1
    AND updated_at=created_at;
UPDATE pricing_plans
SET version='grapyth-managed-2026-08-03',
    provider_input_usd='2.000000',
    provider_cached_usd='0.200000',
    provider_cache_write_usd='2.500000',
    provider_output_usd='12.000000',
    billed_input_usd='2.000000',
    billed_cached_usd='0.200000',
    billed_cache_write_usd='2.500000',
    billed_output_usd='12.000000',
    updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE name='Default managed pricing'
    AND model='gpt-5.6-terra'
    AND version='grapyth-managed-2026-07-22'
    AND provider_input_usd='2.500000'
    AND provider_cached_usd='0.250000'
    AND provider_cache_write_usd='3.125000'
    AND provider_output_usd='15.000000'
    AND billed_input_usd='2.500000'
    AND billed_cached_usd='0.250000'
    AND billed_cache_write_usd='3.125000'
    AND billed_output_usd='15.000000'
    AND allow_below_cost=0
    AND below_cost_reason=''
    AND enabled=1
    AND updated_at=created_at;
"""

CALL_DIAGNOSTICS_SQL = """
ALTER TABLE calls ADD COLUMN gateway_error_code TEXT NOT NULL DEFAULT '';
ALTER TABLE calls ADD COLUMN failure_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE calls ADD COLUMN request_kind TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE calls ADD COLUMN outcome_kind TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE calls ADD COLUMN requested_tool_call_count INTEGER NOT NULL DEFAULT 0;
"""


class SchemaCompatibilityError(RuntimeError):
    """The database schema cannot be used safely by this Gateway binary."""


class NewerSchemaError(SchemaCompatibilityError):
    """The database was created by a newer Gateway binary."""


def _split_sql(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines():
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip().removesuffix(";").strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("Migration SQL contains an incomplete statement")
    return tuple(statements)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("Migration version must be positive")
        if not re.fullmatch(r"[a-z0-9_]+", self.name):
            raise ValueError("Migration name must contain only lowercase letters, numbers, and underscores")
        if not self.statements:
            raise ValueError("Migration must contain at least one statement")

    @property
    def checksum(self) -> str:
        source = json.dumps(
            [self.version, self.name, *self.statements],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(1, "initial_gateway_schema", _split_sql(INITIAL_GATEWAY_SQL)),
    Migration(2, "update_managed_terra_pricing", _split_sql(MANAGED_TERRA_PRICING_SQL)),
    Migration(3, "add_call_diagnostics", _split_sql(CALL_DIAGNOSTICS_SQL)),
)


def _normalized_sql(value: str | None) -> str:
    normalized = re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", value or "", flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized.strip()).lower()
    normalized = re.sub(r"\s*([(),=])\s*", r"\1", normalized)
    return normalized


def schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND name<>'schema_migrations' "
        "ORDER BY type,name"
    ).fetchall()
    shape = [
        {
            "type": str(row["type"] if isinstance(row, sqlite3.Row) else row[0]),
            "name": str(row["name"] if isinstance(row, sqlite3.Row) else row[1]),
            "table": str(row["tbl_name"] if isinstance(row, sqlite3.Row) else row[2]),
            "sql": _normalized_sql(row["sql"] if isinstance(row, sqlite3.Row) else row[3]),
        }
        for row in rows
    ]
    encoded = json.dumps(shape, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fingerprint_for_statements(statements: Iterable[str]) -> str:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        for statement in statements:
            connection.execute(statement)
        return schema_fingerprint(connection)


CURRENT_SCHEMA_FINGERPRINT = _fingerprint_for_statements(
    statement for migration in MIGRATIONS for statement in migration.statements
)
EMPTY_SCHEMA_FINGERPRINT = _fingerprint_for_statements(())
EXPECTED_MIGRATION_COLUMNS = (
    ("version", "INTEGER", 0, None, 1),
    ("name", "TEXT", 1, None, 0),
    ("checksum", "TEXT", 1, None, 0),
    ("applied_at", "TEXT", 1, None, 0),
)


class MigrationRunner:
    def __init__(
        self,
        migrations: tuple[Migration, ...] = MIGRATIONS,
        *,
        verify_fingerprint: bool = True,
    ):
        versions = [migration.version for migration in migrations]
        if versions != list(range(1, len(migrations) + 1)):
            raise ValueError("Migration versions must be unique, ordered, and contiguous from 1")
        self.migrations = migrations
        self.latest_version = migrations[-1].version if migrations else 0
        if not verify_fingerprint:
            self.expected_fingerprint = None
        elif migrations == MIGRATIONS:
            self.expected_fingerprint = CURRENT_SCHEMA_FINGERPRINT
        else:
            self.expected_fingerprint = _fingerprint_for_statements(
                statement for migration in migrations for statement in migration.statements
            )

    def initialize(self, connect: Callable[[], sqlite3.Connection]) -> None:
        with closing(connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._initialize_locked(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def assert_current(self, connection: sqlite3.Connection) -> None:
        if not self._has_migration_table(connection):
            raise SchemaCompatibilityError("Gateway database has no schema migration history")
        applied = self._validated_applied_migrations(connection)
        current_version = applied[-1] if applied else 0
        if current_version < self.latest_version:
            raise SchemaCompatibilityError(
                f"Gateway database schema version {current_version} requires migration to "
                f"{self.latest_version}"
            )
        self._assert_expected_fingerprint(connection)

    def assert_backup_compatible(self, connection: sqlite3.Connection) -> None:
        if not self._has_migration_table(connection):
            raise SchemaCompatibilityError("Gateway backup has no schema migration history")
        self.assert_current(connection)

    def _initialize_locked(self, connection: sqlite3.Connection) -> None:
        if not self._has_migration_table(connection):
            self._prepare_database(connection)
        applied = self._validated_applied_migrations(connection)
        current_version = applied[-1] if applied else 0
        for migration in self.migrations:
            if migration.version > current_version:
                self._apply_migration(connection, migration)
        self.assert_current(connection)

    def _prepare_database(self, connection: sqlite3.Connection) -> None:
        fingerprint = schema_fingerprint(connection)
        if fingerprint == EMPTY_SCHEMA_FINGERPRINT:
            connection.execute(SCHEMA_MIGRATIONS_DDL)
            return
        raise SchemaCompatibilityError(
            "Gateway database has an unversioned schema; replace the disposable database or "
            "restore a versioned backup"
        )

    def _validated_applied_migrations(self, connection: sqlite3.Connection) -> list[int]:
        self._assert_migration_table_shape(connection)
        rows = connection.execute(
            "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        newer_versions = [
            int(row["version"])
            for row in rows
            if int(row["version"]) > self.latest_version
        ]
        if newer_versions:
            version = max(newer_versions)
            raise NewerSchemaError(
                f"Gateway database schema version {version} is newer than this binary "
                f"({self.latest_version})"
            )
        expected_versions = list(range(1, len(rows) + 1))
        actual_versions = [int(row["version"]) for row in rows]
        if actual_versions != expected_versions:
            raise SchemaCompatibilityError(
                "Gateway schema migration history has gaps or starts incorrectly"
            )
        by_version = {migration.version: migration for migration in self.migrations}
        for row in rows:
            version = int(row["version"])
            migration = by_version.get(version)
            if migration is None:
                raise SchemaCompatibilityError(f"Gateway schema migration {version} is unknown")
            if row["name"] != migration.name or row["checksum"] != migration.checksum:
                raise SchemaCompatibilityError(
                    f"Gateway schema migration {version} does not match the immutable registry"
                )
        return actual_versions

    def _apply_migration(self, connection: sqlite3.Connection, migration: Migration) -> None:
        for statement in migration.statements:
            connection.execute(statement)
        self._record_migration(connection, migration)

    @staticmethod
    def _record_migration(connection: sqlite3.Connection, migration: Migration) -> None:
        connection.execute(
            "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES (?,?,?,?)",
            (migration.version, migration.name, migration.checksum, _utc_now()),
        )

    @staticmethod
    def _has_migration_table(connection: sqlite3.Connection) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is not None

    @staticmethod
    def _assert_migration_table_shape(connection: sqlite3.Connection) -> None:
        columns = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
            for row in connection.execute("PRAGMA table_info(schema_migrations)")
        )
        if columns != EXPECTED_MIGRATION_COLUMNS:
            raise SchemaCompatibilityError(
                "Gateway schema migration table does not match the required structure"
            )

    def _assert_expected_fingerprint(self, connection: sqlite3.Connection) -> None:
        if self.expected_fingerprint is None:
            return
        actual = schema_fingerprint(connection)
        if actual != self.expected_fingerprint:
            raise SchemaCompatibilityError(
                "Gateway database structure does not match its recorded migration version"
            )


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
