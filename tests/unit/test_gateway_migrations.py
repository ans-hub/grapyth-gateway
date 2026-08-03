from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gateway.database import GatewayDatabase
from gateway.persistence.connection import connect_sqlite
from gateway.persistence.migrations import (
    CURRENT_SCHEMA_FINGERPRINT,
    MIGRATIONS,
    Migration,
    MigrationRunner,
    NewerSchemaError,
    SchemaCompatibilityError,
    schema_fingerprint,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INITIAL_MIGRATION_CHECKSUM = "bb25d5d3a5a843ce468606063daf02c207d737f9198605710e6ef4723ad93178"
MANAGED_PRICING_MIGRATION_CHECKSUM = (
    "025553f379e3db1964d359185c39761f97328328f64083f1d417f8724bbe9883"
)


def migration_rows(database: GatewayDatabase) -> list[sqlite3.Row]:
    with database.connect() as connection:
        return connection.execute(
            "SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()


def test_fresh_database_reaches_the_versioned_schema_with_required_objects(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "gateway.db")

    database.initialize()

    rows = migration_rows(database)
    assert [(row["version"], row["name"], row["checksum"]) for row in rows] == [
        (1, "initial_gateway_schema", INITIAL_MIGRATION_CHECKSUM),
        (2, "update_managed_terra_pricing", MANAGED_PRICING_MIGRATION_CHECKSUM),
    ]
    with database.connect() as connection:
        assert schema_fingerprint(connection) == CURRENT_SCHEMA_FINGERPRINT
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        defaults = connection.execute(
            "SELECT singleton FROM gateway_defaults WHERE singleton=1"
        ).fetchone()
        settings = connection.execute(
            "SELECT singleton FROM gateway_settings WHERE singleton=1"
        ).fetchone()
    assert {
        "schema_migrations",
        "provider_credentials",
        "pricing_plans",
        "installations",
        "installation_balances",
        "calls",
        "user_limits",
        "credit_ledger",
        "gateway_defaults",
        "gateway_settings",
        "audit_log",
    } <= tables
    assert {
        "credit_ledger_call_id",
        "calls_installation_created",
        "calls_installation_user_created",
        "calls_status",
        "credit_ledger_installation_created",
        "audit_log_created",
    } <= indexes
    assert defaults is not None
    assert settings is not None


def test_initialization_is_a_no_op_after_the_migration_is_recorded(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    before = [tuple(row) for row in migration_rows(database)]

    database.initialize()

    assert [tuple(row) for row in migration_rows(database)] == before


def test_managed_pricing_migration_updates_only_the_untouched_builtin_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-managed-pricing.db"
    MigrationRunner((MIGRATIONS[0],)).initialize(lambda: connect_sqlite(path))
    rates_json = (
        '{"input":"2.500000","cached":"0.250000",'
        '"cacheWrite":"3.125000","output":"15.000000"}'
    )
    with connect_sqlite(path) as connection:
        for plan_id, updated_at in (
            ("plan-managed", "2026-07-22T00:00:00Z"),
            ("plan-touched", "2026-07-23T00:00:00Z"),
        ):
            connection.execute(
                "INSERT INTO pricing_plans("
                "id,name,model,version,provider_input_usd,provider_cached_usd,"
                "provider_cache_write_usd,provider_output_usd,billed_input_usd,"
                "billed_cached_usd,billed_cache_write_usd,billed_output_usd,"
                "allow_below_cost,below_cost_reason,enabled,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    "Default managed pricing",
                    "gpt-5.6-terra",
                    "grapyth-managed-2026-07-22",
                    "2.500000",
                    "0.250000",
                    "3.125000",
                    "15.000000",
                    "2.500000",
                    "0.250000",
                    "3.125000",
                    "15.000000",
                    0,
                    "",
                    1,
                    "2026-07-22T00:00:00Z",
                    updated_at,
                ),
            )
        connection.execute(
            "INSERT INTO installations("
            "id,name,token_hash,token_hint,pricing_plan_id,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?)",
            (
                "inst-managed",
                "Managed installation",
                "token-hash",
                "-token",
                "plan-managed",
                "2026-07-22T00:00:00Z",
                "2026-07-22T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO calls("
            "id,installation_id,idempotency_key,status,requested_model,created_at,"
            "pricing_plan_id,pricing_version,provider_rates_json,billed_rates_json"
            ") VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "call-before-pricing-update",
                "inst-managed",
                "legacy-call",
                "ok",
                "gpt-5.6-terra",
                "2026-07-22T00:00:00Z",
                "plan-managed",
                "grapyth-managed-2026-07-22",
                rates_json,
                rates_json,
            ),
        )

    database = GatewayDatabase(path)
    database.initialize()
    database.initialize()

    with database.connect() as connection:
        managed = connection.execute(
            "SELECT version,provider_input_usd,provider_cached_usd,"
            "provider_cache_write_usd,provider_output_usd,billed_input_usd,"
            "billed_cached_usd,billed_cache_write_usd,billed_output_usd "
            "FROM pricing_plans WHERE id='plan-managed'"
        ).fetchone()
        touched = connection.execute(
            "SELECT version,provider_input_usd,updated_at "
            "FROM pricing_plans WHERE id='plan-touched'"
        ).fetchone()
        historical_call = connection.execute(
            "SELECT pricing_version,provider_rates_json,billed_rates_json "
            "FROM calls WHERE id='call-before-pricing-update'"
        ).fetchone()
        audits = connection.execute(
            "SELECT action,target_id,details_json FROM audit_log "
            "WHERE action='pricing_plan.managed_rates_migrated'"
        ).fetchall()

    assert tuple(managed) == (
        "grapyth-managed-2026-08-03",
        "2.000000",
        "0.200000",
        "2.500000",
        "12.000000",
        "2.000000",
        "0.200000",
        "2.500000",
        "12.000000",
    )
    assert tuple(touched) == (
        "grapyth-managed-2026-07-22",
        "2.500000",
        "2026-07-23T00:00:00Z",
    )
    assert tuple(historical_call) == (
        "grapyth-managed-2026-07-22",
        rates_json,
        rates_json,
    )
    assert [tuple(row) for row in audits] == [
        (
            "pricing_plan.managed_rates_migrated",
            "plan-managed",
            '{"fromVersion":"grapyth-managed-2026-07-22",'
            '"toVersion":"grapyth-managed-2026-08-03"}',
        )
    ]


def test_current_unversioned_schema_is_rejected_without_losing_data(tmp_path: Path) -> None:
    path = tmp_path / "current-unversioned.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((FIXTURES / "current_unversioned.sql").read_text(encoding="utf-8"))
        connection.execute(
            "UPDATE calls SET estimated_provider_cost_usd='0.020000',provider_cost_usd=NULL "
            "WHERE id='call-current'"
        )
    database = GatewayDatabase(path)

    with pytest.raises(SchemaCompatibilityError, match="no schema migration history"):
        GatewayDatabase.verify_backup(path)
    with pytest.raises(SchemaCompatibilityError, match="unversioned schema"):
        database.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM installations WHERE id='inst-current'"
        ).fetchone()[0] == "Current installation"
        assert connection.execute(
            "SELECT provider_cost_usd FROM calls WHERE id='call-current'"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_empty_unversioned_calls_shape_is_rejected_without_changes(tmp_path: Path) -> None:
    path = tmp_path / "unversioned.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((FIXTURES / "unversioned_calls.sql").read_text(encoding="utf-8"))
    database = GatewayDatabase(path)

    with pytest.raises(SchemaCompatibilityError, match="unversioned schema"):
        database.initialize()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
        assert columns == {
            "id",
            "installation_id",
            "idempotency_key",
            "status",
            "requested_model",
            "created_at",
            "estimated_provider_cost_usd",
        }
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_nonempty_unversioned_calls_schema_is_rejected_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "unversioned-with-data.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((FIXTURES / "unversioned_calls.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO calls(id,installation_id,idempotency_key,status,requested_model,created_at) "
            "VALUES ('call-one','inst-one','request-one','ok','model','2026-01-01T00:00:00Z')"
        )

    with pytest.raises(SchemaCompatibilityError, match="unversioned schema"):
        GatewayDatabase(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_unknown_unversioned_schema_is_rejected_without_changes(tmp_path: Path) -> None:
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE mystery(id TEXT PRIMARY KEY)")

    with pytest.raises(SchemaCompatibilityError, match="unversioned schema"):
        GatewayDatabase(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_newer_and_modified_migration_history_are_rejected(tmp_path: Path) -> None:
    newer = GatewayDatabase(tmp_path / "newer.db")
    newer.initialize()
    with newer.connect() as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version,name,checksum,applied_at) "
            "VALUES (3,'future_schema','future','2026-01-01T00:00:00Z')"
        )
        connection.commit()
    with pytest.raises(NewerSchemaError, match="newer than this binary"):
        newer.initialize()

    modified = GatewayDatabase(tmp_path / "modified.db")
    modified.initialize()
    with modified.connect() as connection:
        connection.execute("UPDATE schema_migrations SET checksum='modified' WHERE version=1")
        connection.commit()
    with pytest.raises(SchemaCompatibilityError, match="immutable registry"):
        modified.initialize()


def test_structural_drift_is_rejected_even_with_current_migration_history(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "drifted.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute("DROP INDEX audit_log_created")
        connection.commit()

    with pytest.raises(SchemaCompatibilityError, match="does not match"):
        database.initialize()


def test_migration_history_table_shape_is_itself_immutable(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "history-shape.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute("ALTER TABLE schema_migrations ADD COLUMN unexpected TEXT")
        connection.commit()

    with pytest.raises(SchemaCompatibilityError, match="required structure"):
        database.initialize()


def test_failed_migration_rolls_back_schema_and_version_record_together(tmp_path: Path) -> None:
    path = tmp_path / "failed.db"
    runner = MigrationRunner(
        (
            Migration(
                1,
                "intentional_failure",
                (
                    "CREATE TABLE partial_change(id TEXT PRIMARY KEY)",
                    "INSERT INTO missing_table(id) VALUES ('failure')",
                ),
            ),
        ),
        verify_fingerprint=False,
    )

    with pytest.raises(sqlite3.OperationalError, match="missing_table"):
        runner.initialize(lambda: connect_sqlite(path))

    with sqlite3.connect(path) as connection:
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert "partial_change" not in objects
    assert "schema_migrations" not in objects


def test_concurrent_initialization_records_each_migration_once(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "concurrent.db")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: database.initialize(), range(2)))

    assert results == [None, None]
    rows = migration_rows(database)
    assert len(rows) == len(MIGRATIONS) == 2
    assert rows[0]["checksum"] == INITIAL_MIGRATION_CHECKSUM
    assert rows[1]["checksum"] == MANAGED_PRICING_MIGRATION_CHECKSUM
