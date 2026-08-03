from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from gateway.database import GatewayDatabase
from gateway.config import DEFAULT_PRICING_VERSION, DEFAULT_RATES
from gateway.domain import (
    CallCompletion,
    CallStart,
    GatewayLimits,
    InstallationPolicy,
    InstallationPrincipal,
    PricingPlanChanges,
    PricingPlanSpec,
    PricingSnapshot,
    UserBudget,
)
from gateway.persistence.models import (
    GatewayDefaultsReadModel,
    GatewaySettingsReadModel,
    InstallationSummaryReadModel,
    PricingPlanReadModel,
)
from gateway.stores import GatewayAccountingStore, GatewayConfigurationStore


def configured_stores(
    tmp_path: Path,
) -> tuple[
    GatewayDatabase,
    GatewayConfigurationStore,
    GatewayAccountingStore,
    PricingPlanReadModel,
]:
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    configuration = GatewayConfigurationStore(database)
    accounting = GatewayAccountingStore(database)
    plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Primary",
            model="gpt-5.6-terra",
            version=DEFAULT_PRICING_VERSION,
            provider_rates=DEFAULT_RATES,
            billed_rates=DEFAULT_RATES,
        )
    )
    configuration.update_defaults(pricing_plan_id=plan.id)
    return database, configuration, accounting, plan


def test_gateway_settings_are_database_managed_and_audited(tmp_path: Path) -> None:
    _database, configuration, accounting, _plan = configured_stores(tmp_path)

    settings = configuration.gateway_settings()
    assert settings.max_output_tokens == 64_000
    assert settings.max_request_bytes == 5 * 1024 * 1024
    updated = configuration.update_gateway_settings(
        max_output_tokens=48_000,
        max_request_bytes=7 * 1024 * 1024,
    )

    assert updated.max_output_tokens == 48_000
    assert updated.max_request_bytes == 7 * 1024 * 1024
    assert "gateway_settings.updated" in {
        row.action for row in accounting.list_audit()
    }
    with pytest.raises(ValueError, match="maxOutputTokens"):
        configuration.update_gateway_settings(max_output_tokens=0)


def test_pricing_plan_is_the_only_stored_source_of_installation_model(tmp_path: Path) -> None:
    database, configuration, _accounting, plan = configured_stores(tmp_path)
    installation, _token = configuration.create_installation("Model policy")

    with database.connect() as connection:
        installation_columns = {row[1] for row in connection.execute("PRAGMA table_info(installations)")}
        default_columns = {row[1] for row in connection.execute("PRAGMA table_info(gateway_defaults)")}
    assert "model" not in installation_columns
    assert "model" not in default_columns
    assert installation.model == "gpt-5.6-terra"

    configuration.update_pricing_plan(
        plan.id,
        PricingPlanChanges(model="gpt-5.7"),
    )

    assert configuration.installation(installation.id).model == "gpt-5.7"
    assert configuration.load_runtime_policy(installation.id).model == "gpt-5.7"


def test_complete_call_and_usage_ledger_roll_back_together(tmp_path: Path) -> None:
    _database, configuration, accounting, plan = configured_stores(tmp_path)
    installation, _token = configuration.create_installation("Atomic billing")
    call = accounting.begin_call(
        CallStart(
            installation_id=installation.id,
            idempotency_key="atomic-call",
            requested_model="ignored-client-model",
        )
    )
    original = CallCompletion(
        call_id=call.id,
        status="ok",
        resolved_model=plan.model,
        reasoning_effort="low",
        pricing=PricingSnapshot(
            plan_id=plan.id,
            version=plan.version,
            provider_rates=plan.provider_rates,
            billed_rates=plan.billed_rates,
            below_cost=plan.below_cost,
        ),
        charged_usd=Decimal("0.1"),
        provider_cost_usd=Decimal("0.08"),
        margin_usd=Decimal("0.02"),
        record_ledger=True,
    )
    accounting.complete_call(original)

    with pytest.raises(sqlite3.IntegrityError):
        accounting.complete_call(
            CallCompletion(
                call_id=call.id,
                status="error",
                resolved_model=plan.model,
                reasoning_effort="low",
                pricing=original.pricing,
                charged_usd=Decimal("0.9"),
                record_ledger=True,
            )
        )

    persisted = accounting.call(call.id)
    assert persisted.status == "ok"
    assert persisted.charged_usd == "0.100000"
    ledger = accounting.list_ledger(installation.id)
    assert len(ledger) == 1
    assert ledger[0].amount_usd == "-0.100000"


def test_stores_expose_typed_critical_runtime_contracts(tmp_path: Path) -> None:
    _database, configuration, accounting, plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Typed contracts")
    accounting.set_user_limit(installation.id, "user-one", Decimal("12.5"))

    principal = configuration.authenticate_principal(token)
    policy = configuration.load_runtime_policy(installation.id)
    limits = configuration.load_gateway_limits()
    budget = accounting.load_user_budget(installation.id, "user-one")

    assert isinstance(plan, PricingPlanReadModel)
    assert isinstance(installation, InstallationSummaryReadModel)
    assert isinstance(configuration.defaults(), GatewayDefaultsReadModel)
    assert isinstance(configuration.gateway_settings(), GatewaySettingsReadModel)
    assert isinstance(principal, InstallationPrincipal)
    assert principal.id == installation.id
    assert isinstance(policy, InstallationPolicy)
    assert policy.pricing.plan_id == installation.pricing_plan_id
    assert isinstance(limits, GatewayLimits)
    assert limits.max_output_tokens == 64_000
    assert isinstance(budget, UserBudget)
    assert budget.monthly_limit_usd == Decimal("12.500000")


def test_applying_defaults_to_installations_is_atomic(tmp_path: Path) -> None:
    _database, configuration, _accounting, original_plan = configured_stores(tmp_path)
    first, _first_token = configuration.create_installation("First")
    second, _second_token = configuration.create_installation("Second")
    replacement_plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Replacement",
            model="gpt-5.7",
            version="replacement-v1",
            provider_rates=DEFAULT_RATES,
            billed_rates=DEFAULT_RATES,
        )
    )
    configuration.update_defaults(pricing_plan_id=replacement_plan.id)

    with pytest.raises(FileNotFoundError, match="Installation not found"):
        configuration.apply_defaults_to_installations([first.id, "missing"])

    assert configuration.installation(first.id).pricing_plan_id == original_plan.id
    updated = configuration.apply_defaults_to_installations(
        [first.id, second.id]
    )
    assert {item.pricing_plan_id for item in updated} == {replacement_plan.id}
