from __future__ import annotations

import hashlib
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Callable, TypeVar

import pytest

from gateway.database import GatewayDatabase
from gateway.config import DEFAULT_MODEL, DEFAULT_PRICING_VERSION, DEFAULT_RATES
from gateway.domain import (
    CallCompletion,
    CallStart,
    PricingPlanSpec,
    PricingSnapshot,
)
from gateway.persistence.migrations import SchemaCompatibilityError
from gateway.stores import (
    GatewayAccountingStore,
    GatewayConfigurationStore,
    ProviderCredentialStore,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
T = TypeVar("T")


def test_builtin_pricing_matches_standard_short_context_terra_rates() -> None:
    assert DEFAULT_MODEL == "gpt-5.6-terra"
    assert DEFAULT_PRICING_VERSION == "grapyth-managed-2026-08-03"
    assert DEFAULT_RATES.to_payload() == {
        "input": "2.000000",
        "cached": "0.200000",
        "cacheWrite": "2.500000",
        "output": "12.000000",
    }


def configured_stores(tmp_path: Path):
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    credentials = ProviderCredentialStore(database)
    configuration = GatewayConfigurationStore(database)
    accounting = GatewayAccountingStore(database)
    plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Contract pricing",
            model=DEFAULT_MODEL,
            version=DEFAULT_PRICING_VERSION,
            provider_rates=DEFAULT_RATES,
            billed_rates=DEFAULT_RATES,
        )
    )
    configuration.update_defaults(pricing_plan_id=plan.id)
    return database, credentials, configuration, accounting, plan


def measured_selects(database: GatewayDatabase, operation: Callable[[], T]) -> tuple[T, list[str]]:
    statements: list[str] = []
    original_connect = database.connect

    def traced_connect() -> sqlite3.Connection:
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    database.connect = traced_connect  # type: ignore[method-assign]
    try:
        result = operation()
    finally:
        database.connect = original_connect  # type: ignore[method-assign]
    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    return result, selects


def test_current_unversioned_database_shape_and_data_are_rejected_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "current-unversioned.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((FIXTURES / "current_unversioned.sql").read_text(encoding="utf-8"))

    database = GatewayDatabase(path)

    with pytest.raises(SchemaCompatibilityError, match="unversioned schema"):
        database.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM installations WHERE id='inst-current'"
        ).fetchone()[0] == "Current installation"
        assert connection.execute(
            "SELECT pricing_version FROM calls WHERE id='call-current'"
        ).fetchone()[0] == "current-v1"
        assert connection.execute(
            "SELECT pricing_plan_id FROM gateway_defaults WHERE singleton=1"
        ).fetchone()[0] == "plan-current"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_installation_token_lifecycle_keeps_only_hash_and_hint_at_rest(tmp_path: Path) -> None:
    database, _credentials, configuration, accounting, _plan = configured_stores(
        tmp_path
    )
    installation, original_token = configuration.create_installation(
        "Token lifecycle"
    )

    assert re.fullmatch(r"gpi_[A-Za-z0-9_-]{43}", original_token)
    assert configuration.authenticate_principal(original_token).id == installation.id
    with database.connect() as connection:
        stored = connection.execute(
            "SELECT token_hash, token_hint FROM installations WHERE id=?", (installation.id,)
        ).fetchone()
    assert stored["token_hash"] == hashlib.sha256(original_token.encode("utf-8")).hexdigest()
    assert stored["token_hint"] == original_token[-6:]
    assert original_token not in str(accounting.list_audit())

    disabled = configuration.update_installation(installation.id, enabled=False)
    assert disabled.enabled is False
    assert configuration.authenticate_principal(original_token).enabled is False

    _rotated, replacement_token = configuration.rotate_token(installation.id)
    assert replacement_token != original_token
    assert configuration.authenticate_principal(original_token) is None
    assert configuration.authenticate_principal(replacement_token).id == installation.id
    persisted_files = [
        database.path,
        database.path.with_name(database.path.name + "-wal"),
        database.path.with_name(database.path.name + "-shm"),
    ]
    persisted = b"".join(path.read_bytes() for path in persisted_files if path.exists())
    assert original_token.encode("utf-8") not in persisted
    assert replacement_token.encode("utf-8") not in persisted


def test_read_query_costs_stay_within_the_recorded_ceiling(tmp_path: Path) -> None:
    database, credentials, configuration, accounting, plan = configured_stores(tmp_path)
    installations_and_tokens = [
        configuration.create_installation(
            f"Client {index}",
            pricing_plan_id=plan.id,
        )
        for index in range(3)
    ]
    for index in range(3):
        credentials.create_provider_credential(
            f"Provider {index}", f"encrypted-{index}", f"hint-{index}"
        )
    for index in range(2):
        configuration.create_pricing_plan(
            PricingPlanSpec(
                name=f"Additional pricing {index}",
                model=DEFAULT_MODEL,
                version=f"additional-{index}",
                provider_rates=DEFAULT_RATES,
                billed_rates=DEFAULT_RATES,
            )
        )
    usage_installation = installations_and_tokens[0][0]
    for index in range(2):
        call = accounting.begin_call(
            CallStart(
                installation_id=usage_installation.id,
                idempotency_key=f"user-call-{index}",
                requested_model="ignored",
                user_id=f"user-{index}",
            )
        )
        accounting.complete_call(
            CallCompletion(
                call_id=call.id,
                status="ok",
                resolved_model=DEFAULT_MODEL,
                reasoning_effort="low",
                pricing=PricingSnapshot(
                    plan_id=plan.id,
                    version=plan.version,
                    provider_rates=plan.provider_rates,
                    billed_rates=plan.billed_rates,
                    below_cost=plan.below_cost,
                ),
                charged_usd=Decimal("0.01"),
                provider_cost_usd=Decimal("0.01"),
                margin_usd=Decimal("0"),
            )
        )
        accounting.set_user_limit(
            usage_installation.id,
            f"user-{index}",
            Decimal("1"),
        )

    authenticated, authentication_queries = measured_selects(
        database,
        lambda: configuration.authenticate_principal(installations_and_tokens[0][1]),
    )
    _typed_policy, typed_policy_queries = measured_selects(
        database,
        lambda: configuration.load_runtime_policy(authenticated.id),
    )
    _item, installation_queries = measured_selects(
        database,
        lambda: configuration.installation(authenticated.id),
    )
    installations, installation_list_queries = measured_selects(
        database,
        configuration.list_installations,
    )
    providers, provider_list_queries = measured_selects(
        database,
        credentials.list_provider_credentials,
    )
    plans, pricing_list_queries = measured_selects(
        database,
        configuration.list_pricing_plans,
    )
    _users, user_usage_queries = measured_selects(
        database,
        lambda: accounting.user_usage(authenticated.id),
    )

    assert len(authentication_queries) == 1
    assert len(typed_policy_queries) == 1
    assert len(installation_queries) == 3
    assert len(installation_list_queries) == 3
    assert len(provider_list_queries) == 1
    assert len(pricing_list_queries) == 1
    assert len(user_usage_queries) == 3


def test_malformed_installation_tokens_are_rejected_without_database_work(tmp_path: Path) -> None:
    database, _credentials, configuration, _accounting, _plan = configured_stores(
        tmp_path
    )

    for token in ("", "not-a-token", "gpi_" + ("a" * 10_000)):
        result, queries = measured_selects(
            database,
            lambda token=token: configuration.authenticate_principal(token),
        )

        assert result is None
        assert queries == []
