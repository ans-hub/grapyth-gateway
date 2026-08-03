import sqlite3
from pathlib import Path

import pytest

from gateway.database import GatewayDatabase
from gateway.config import DEFAULT_MODEL, DEFAULT_PRICING_VERSION, DEFAULT_RATES
from gateway.domain import PricingPlanSpec
from gateway.manage import restore_backup
from gateway.persistence.migrations import SchemaCompatibilityError
from gateway.stores import GatewayConfigurationStore


def test_gateway_backup_is_consistent_and_restore_keeps_a_safety_copy(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    database = GatewayDatabase(data_root / "gateway.db")
    database.initialize()
    configuration = GatewayConfigurationStore(database)
    plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Backup test",
            model=DEFAULT_MODEL,
            version=DEFAULT_PRICING_VERSION,
            provider_rates=DEFAULT_RATES,
            billed_rates=DEFAULT_RATES,
        )
    )
    original, _token = configuration.create_installation(
        "Original",
        pricing_plan_id=plan.id,
        reasoning_effort="low",
        billing_mode="prepaid",
    )
    backup = database.backup(tmp_path / "off-vps" / "gateway.db")

    configuration.create_installation(
        "Added after backup",
        pricing_plan_id=plan.id,
        reasoning_effort="low",
        billing_mode="prepaid",
    )
    restored, safety = restore_backup(data_root, backup)

    assert restored == database.path
    assert safety is not None and safety.is_file()
    database.verify_backup(restored)
    database.verify_backup(safety)
    with sqlite3.connect(restored) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM installations ORDER BY name")]
    assert names == [original.name]
    with sqlite3.connect(safety) as connection:
        safety_names = [row[0] for row in connection.execute("SELECT name FROM installations ORDER BY name")]
    assert safety_names == ["Added after backup", "Original"]


def test_gateway_backup_verification_rejects_modified_migration_history(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    backup = database.backup(tmp_path / "backup.db")
    with sqlite3.connect(backup) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='modified' WHERE version=1")

    with pytest.raises(SchemaCompatibilityError, match="immutable registry"):
        database.verify_backup(backup)

    unpublished = tmp_path / "must-not-exist.db"
    with database.connect() as connection:
        connection.execute("UPDATE schema_migrations SET checksum='modified' WHERE version=1")
        connection.commit()
    with pytest.raises(SchemaCompatibilityError, match="immutable registry"):
        database.backup(unpublished)
    assert not unpublished.exists()


def test_restore_rejects_unversioned_backup_before_creating_the_data_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "current-unversioned.db"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "current_unversioned.sql"
    with sqlite3.connect(source) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))

    data_root = tmp_path / "restored"

    with pytest.raises(SchemaCompatibilityError, match="no schema migration history"):
        restore_backup(data_root, source)

    assert not data_root.exists()
