from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from gateway.database import GatewayDatabase
from gateway.config import DEFAULT_MODEL, DEFAULT_PRICING_VERSION, DEFAULT_RATES
from gateway.domain import PricingPlanSpec
from gateway.persistence import audit as audit_repository
from gateway.stores import (
    GatewayAccountingStore,
    GatewayConfigurationStore,
    ProviderCredentialStore,
)


def configured_stores(tmp_path: Path):
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    credentials = ProviderCredentialStore(database)
    configuration = GatewayConfigurationStore(database)
    accounting = GatewayAccountingStore(database)
    plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Persistence plan",
            model=DEFAULT_MODEL,
            version=DEFAULT_PRICING_VERSION,
            provider_rates=DEFAULT_RATES,
            billed_rates=DEFAULT_RATES,
        )
    )
    configuration.update_defaults(pricing_plan_id=plan.id)
    return database, credentials, accounting


def test_oversized_audit_details_are_valid_bounded_json_without_values(tmp_path: Path) -> None:
    database, _credentials, accounting = configured_stores(tmp_path)
    sensitive_value = "provider-secret-" + ("x" * 9000)

    with database.transaction() as connection:
        audit_repository.write(
            connection,
            action="test.oversized",
            target_type="test",
            target_id="oversized",
            details={"credential": sensitive_value, "reason": "test"},
            now=lambda: "2026-08-01T00:00:00Z",
        )

    record = next(
        item
        for item in accounting.list_audit()
        if item.action == "test.oversized"
    )
    parsed = json.loads(record.details_json)
    assert len(record.details_json.encode("utf-8")) <= 8000
    assert parsed == record.details
    assert parsed["truncated"] is True
    assert parsed["originalBytes"] > 8000
    assert sensitive_value not in record.details_json


def test_invalid_audit_json_remains_readable_without_exposing_raw_text(tmp_path: Path) -> None:
    database, _credentials, accounting = configured_stores(tmp_path)
    invalid = '{"secret":"private-value"'
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_log(id, action, target_type, target_id, details_json, created_at) "
            "VALUES ('invalid-json', 'audit.invalid', 'test', 'invalid', :details, "
            "'2026-08-01T00:00:00Z')",
            {"details": invalid},
        )

    record = next(item for item in accounting.list_audit() if item.id == "invalid-json")
    assert record.details == {"invalidJson": True}
    assert invalid not in str(record.details)


def test_repository_read_models_are_immutable(tmp_path: Path) -> None:
    database, credentials, _accounting = configured_stores(tmp_path)
    credentials.create_provider_credential("Provider", "encrypted", "hint")

    with database.connect() as connection:
        record = audit_repository.list_recent(connection, limit=1)[0]

    with pytest.raises(FrozenInstanceError):
        record.action = "changed"  # type: ignore[misc]


def test_runtime_persistence_has_no_outward_select_star() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = [
        root / "database.py",
        *(root / "persistence").glob("*.py"),
        *(root / "stores").glob("*.py"),
    ]

    for source in sources:
        assert "SELECT *" not in source.read_text(encoding="utf-8"), source
