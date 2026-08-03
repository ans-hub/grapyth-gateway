from __future__ import annotations

import sqlite3
from typing import Any, cast

from ..domain import (
    BillingMode,
    PricingPlanSpec,
    PricingRates,
    ReasoningEffort,
    format_money,
)
from .models import GatewayDefaultsReadModel, GatewaySettingsReadModel, PricingPlanReadModel


PLAN_COLUMNS = (
    "id, name, model, version, provider_input_usd, provider_cached_usd, "
    "provider_cache_write_usd, provider_output_usd, billed_input_usd, billed_cached_usd, "
    "billed_cache_write_usd, billed_output_usd, allow_below_cost, below_cost_reason, enabled, "
    "created_at, updated_at"
)


def _map_plan(row: sqlite3.Row) -> PricingPlanReadModel:
    return PricingPlanReadModel(
        id=str(row["id"]),
        name=str(row["name"]),
        model=str(row["model"]),
        version=str(row["version"]),
        provider_rates=PricingRates(
            input=row["provider_input_usd"],
            cached=row["provider_cached_usd"],
            cache_write=row["provider_cache_write_usd"],
            output=row["provider_output_usd"],
        ),
        billed_rates=PricingRates(
            input=row["billed_input_usd"],
            cached=row["billed_cached_usd"],
            cache_write=row["billed_cache_write_usd"],
            output=row["billed_output_usd"],
        ),
        allow_below_cost=bool(row["allow_below_cost"]),
        below_cost_reason=str(row["below_cost_reason"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def get_plan(connection: sqlite3.Connection, plan_id: str) -> PricingPlanReadModel | None:
    row = connection.execute(
        f"SELECT {PLAN_COLUMNS} FROM pricing_plans WHERE id=:plan_id",
        {"plan_id": plan_id},
    ).fetchone()
    return _map_plan(row) if row else None


def list_plans(connection: sqlite3.Connection) -> list[PricingPlanReadModel]:
    rows = connection.execute(
        f"SELECT {PLAN_COLUMNS} FROM pricing_plans ORDER BY created_at"
    ).fetchall()
    return [_map_plan(row) for row in rows]


def insert_plan(
    connection: sqlite3.Connection,
    *,
    plan_id: str,
    value: PricingPlanSpec,
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO pricing_plans(id, name, model, version, provider_input_usd, provider_cached_usd, "
        "provider_cache_write_usd, provider_output_usd, billed_input_usd, billed_cached_usd, "
        "billed_cache_write_usd, billed_output_usd, allow_below_cost, below_cost_reason, enabled, "
        "created_at, updated_at) VALUES (:id, :name, :model, :version, :provider_input, "
        ":provider_cached, :provider_cache_write, :provider_output, :billed_input, :billed_cached, "
        ":billed_cache_write, :billed_output, :allow_below_cost, :below_cost_reason, :enabled, "
        ":created_at, :created_at)",
        _plan_parameters(plan_id, value, created_at),
    )


def update_plan(
    connection: sqlite3.Connection,
    *,
    plan_id: str,
    value: PricingPlanSpec,
    updated_at: str,
) -> None:
    connection.execute(
        "UPDATE pricing_plans SET name=:name, model=:model, version=:version, "
        "provider_input_usd=:provider_input, provider_cached_usd=:provider_cached, "
        "provider_cache_write_usd=:provider_cache_write, provider_output_usd=:provider_output, "
        "billed_input_usd=:billed_input, billed_cached_usd=:billed_cached, "
        "billed_cache_write_usd=:billed_cache_write, billed_output_usd=:billed_output, "
        "allow_below_cost=:allow_below_cost, below_cost_reason=:below_cost_reason, enabled=:enabled, "
        "updated_at=:created_at WHERE id=:id",
        _plan_parameters(plan_id, value, updated_at),
    )


def _plan_parameters(
    plan_id: str,
    value: PricingPlanSpec,
    timestamp: str,
) -> dict[str, Any]:
    return {
        "id": plan_id,
        "name": value.name,
        "model": value.model,
        "version": value.version,
        "provider_input": format_money(value.provider_rates.input),
        "provider_cached": format_money(value.provider_rates.cached),
        "provider_cache_write": format_money(value.provider_rates.cache_write),
        "provider_output": format_money(value.provider_rates.output),
        "billed_input": format_money(value.billed_rates.input),
        "billed_cached": format_money(value.billed_rates.cached),
        "billed_cache_write": format_money(value.billed_rates.cache_write),
        "billed_output": format_money(value.billed_rates.output),
        "allow_below_cost": int(value.allow_below_cost),
        "below_cost_reason": value.below_cost_reason,
        "enabled": int(value.enabled),
        "created_at": timestamp,
    }


def get_settings(connection: sqlite3.Connection) -> GatewaySettingsReadModel:
    row = connection.execute(
        "SELECT max_output_tokens, max_request_bytes, updated_at "
        "FROM gateway_settings WHERE singleton=1"
    ).fetchone()
    if not row:
        raise RuntimeError("Gateway settings are missing")
    return GatewaySettingsReadModel(
        max_output_tokens=int(row["max_output_tokens"]),
        max_request_bytes=int(row["max_request_bytes"]),
        updated_at=str(row["updated_at"]),
    )


def update_settings(
    connection: sqlite3.Connection, *, max_output_tokens: int, max_request_bytes: int, updated_at: str
) -> None:
    connection.execute(
        "UPDATE gateway_settings SET max_output_tokens=:max_output_tokens, "
        "max_request_bytes=:max_request_bytes, updated_at=:updated_at WHERE singleton=1",
        {
            "max_output_tokens": max_output_tokens,
            "max_request_bytes": max_request_bytes,
            "updated_at": updated_at,
        },
    )


def get_defaults(connection: sqlite3.Connection) -> GatewayDefaultsReadModel:
    row = connection.execute(
        "SELECT provider_credential_id, pricing_plan_id, reasoning_effort, billing_mode, updated_at "
        "FROM gateway_defaults WHERE singleton=1"
    ).fetchone()
    if not row:
        raise RuntimeError("Gateway defaults are missing")
    return GatewayDefaultsReadModel(
        provider_credential_id=str(row["provider_credential_id"]),
        pricing_plan_id=str(row["pricing_plan_id"]),
        reasoning_effort=cast(ReasoningEffort, str(row["reasoning_effort"])),
        billing_mode=cast(BillingMode, str(row["billing_mode"])),
        updated_at=str(row["updated_at"]),
    )


def initialize_default_plan(connection: sqlite3.Connection, *, plan_id: str, updated_at: str) -> None:
    connection.execute(
        "UPDATE gateway_defaults SET pricing_plan_id=:plan_id, updated_at=:updated_at "
        "WHERE singleton=1 AND pricing_plan_id=''",
        {"plan_id": plan_id, "updated_at": updated_at},
    )


def update_defaults(
    connection: sqlite3.Connection,
    *,
    provider_credential_id: str,
    pricing_plan_id: str,
    reasoning_effort: ReasoningEffort,
    billing_mode: BillingMode,
    updated_at: str,
) -> None:
    connection.execute(
        "UPDATE gateway_defaults SET provider_credential_id=:provider_credential_id, "
        "pricing_plan_id=:pricing_plan_id, reasoning_effort=:reasoning_effort, "
        "billing_mode=:billing_mode, updated_at=:updated_at WHERE singleton=1",
        {
            "provider_credential_id": provider_credential_id,
            "pricing_plan_id": pricing_plan_id,
            "reasoning_effort": reasoning_effort,
            "billing_mode": billing_mode,
            "updated_at": updated_at,
        },
    )
