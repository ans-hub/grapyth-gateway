from __future__ import annotations

import sqlite3
from collections import defaultdict
from decimal import Decimal
from typing import Any, cast

from ..domain import (
    BillingMode,
    InstallationPolicy,
    InstallationPrincipal,
    PricingRates,
    PricingSnapshot,
    ReasoningEffort,
)
from .models import InstallationSummaryReadModel, InstallationUsageReadModel


INSTALLATION_COLUMNS = (
    "i.id, i.name, i.token_hint, i.enabled, i.note, i.provider_credential_id, "
    "i.pricing_plan_id, COALESCE(p.model, '') AS model, i.reasoning_effort, i.billing_mode, "
    "i.created_at, i.updated_at"
)


def authenticate(connection: sqlite3.Connection, hashed_token: str) -> InstallationPrincipal | None:
    row = connection.execute(
        "SELECT id, enabled FROM installations WHERE token_hash=:token_hash",
        {"token_hash": hashed_token},
    ).fetchone()
    if not row:
        return None
    return InstallationPrincipal(id=str(row["id"]), enabled=bool(row["enabled"]))


def exists(connection: sqlite3.Connection, installation_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM installations WHERE id=:installation_id",
        {"installation_id": installation_id},
    ).fetchone() is not None


def load_runtime_policy(
    connection: sqlite3.Connection, installation_id: str
) -> InstallationPolicy | None:
    row = connection.execute(
        "SELECT i.id, i.enabled, i.provider_credential_id, i.reasoning_effort, i.billing_mode, "
        "p.id AS plan_id, p.model, p.version, p.provider_input_usd, p.provider_cached_usd, "
        "p.provider_cache_write_usd, p.provider_output_usd, p.billed_input_usd, "
        "p.billed_cached_usd, p.billed_cache_write_usd, p.billed_output_usd, p.enabled AS plan_enabled "
        "FROM installations i LEFT JOIN pricing_plans p ON p.id=i.pricing_plan_id "
        "WHERE i.id=:installation_id",
        {"installation_id": installation_id},
    ).fetchone()
    if not row:
        return None
    if not row["plan_id"]:
        raise ValueError("Installation has no pricing plan")
    if not row["plan_enabled"]:
        raise ValueError("Installation pricing plan is disabled")
    provider_rates = PricingRates(
        input=row["provider_input_usd"],
        cached=row["provider_cached_usd"],
        cache_write=row["provider_cache_write_usd"],
        output=row["provider_output_usd"],
    )
    billed_rates = PricingRates(
        input=row["billed_input_usd"],
        cached=row["billed_cached_usd"],
        cache_write=row["billed_cache_write_usd"],
        output=row["billed_output_usd"],
    )
    return InstallationPolicy(
        installation_id=str(row["id"]),
        enabled=bool(row["enabled"]),
        provider_credential_id=str(row["provider_credential_id"]),
        model=str(row["model"]),
        reasoning_effort=cast(ReasoningEffort, str(row["reasoning_effort"])),
        billing_mode=cast(BillingMode, str(row["billing_mode"])),
        pricing=PricingSnapshot(
            plan_id=str(row["plan_id"]),
            version=str(row["version"]),
            provider_rates=provider_rates,
            billed_rates=billed_rates,
            below_cost=any(
                billed < provider
                for provider, billed in (
                    (provider_rates.input, billed_rates.input),
                    (provider_rates.cached, billed_rates.cached),
                    (provider_rates.cache_write, billed_rates.cache_write),
                    (provider_rates.output, billed_rates.output),
                )
            ),
        ),
    )


def get_summary(
    connection: sqlite3.Connection, installation_id: str
) -> InstallationSummaryReadModel | None:
    values = list_summaries(connection, installation_id=installation_id)
    return values[0] if values else None


def list_summaries(
    connection: sqlite3.Connection, *, installation_id: str | None = None
) -> list[InstallationSummaryReadModel]:
    if installation_id is None:
        installation_rows = connection.execute(
            f"SELECT {INSTALLATION_COLUMNS} FROM installations i "
            "LEFT JOIN pricing_plans p ON p.id=i.pricing_plan_id ORDER BY i.created_at DESC"
        ).fetchall()
        balance_rows = connection.execute(
            "SELECT installation_id, balance_microusd FROM installation_balances"
        ).fetchall()
        call_rows = connection.execute(
            "SELECT installation_id, input_tokens, output_tokens, below_cost, provider_cost_usd, "
            "charged_usd, margin_usd FROM calls WHERE status='ok'"
        ).fetchall()
    else:
        parameters = {"installation_id": installation_id}
        installation_rows = connection.execute(
            f"SELECT {INSTALLATION_COLUMNS} FROM installations i "
            "LEFT JOIN pricing_plans p ON p.id=i.pricing_plan_id WHERE i.id=:installation_id",
            parameters,
        ).fetchall()
        if not installation_rows:
            return []
        balance_rows = connection.execute(
            "SELECT installation_id, balance_microusd FROM installation_balances "
            "WHERE installation_id=:installation_id",
            parameters,
        ).fetchall()
        call_rows = connection.execute(
            "SELECT installation_id, input_tokens, output_tokens, below_cost, provider_cost_usd, "
            "charged_usd, margin_usd FROM calls "
            "WHERE installation_id=:installation_id AND status='ok'",
            parameters,
        ).fetchall()

    balances = {
        str(row["installation_id"]): Decimal(int(row["balance_microusd"]))
        / Decimal(1_000_000)
        for row in balance_rows
    }

    usage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "below_cost_calls": 0,
            "provider_cost_usd": Decimal("0"),
            "billed_cost_usd": Decimal("0"),
            "margin_usd": Decimal("0"),
        }
    )
    for row in call_rows:
        item = usage[str(row["installation_id"])]
        item["calls"] += 1
        item["input_tokens"] += int(row["input_tokens"] or 0)
        item["output_tokens"] += int(row["output_tokens"] or 0)
        item["below_cost_calls"] += int(row["below_cost"] or 0)
        if row["provider_cost_usd"] is not None:
            item["provider_cost_usd"] += Decimal(str(row["provider_cost_usd"]))
        if row["charged_usd"] is not None:
            item["billed_cost_usd"] += Decimal(str(row["charged_usd"]))
        if row["margin_usd"] is not None:
            item["margin_usd"] += Decimal(str(row["margin_usd"]))

    results: list[InstallationSummaryReadModel] = []
    for row in installation_rows:
        installation_key = str(row["id"])
        results.append(
            InstallationSummaryReadModel(
                id=installation_key,
                name=str(row["name"]),
                token_hint=str(row["token_hint"]),
                enabled=bool(row["enabled"]),
                note=str(row["note"]),
                provider_credential_id=str(row["provider_credential_id"]),
                pricing_plan_id=str(row["pricing_plan_id"]),
                model=str(row["model"]),
                reasoning_effort=cast(
                    ReasoningEffort,
                    str(row["reasoning_effort"]),
                ),
                billing_mode=cast(BillingMode, str(row["billing_mode"])),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                balance_usd=balances[installation_key],
                usage=InstallationUsageReadModel(**usage[installation_key]),
            )
        )
    return results


def insert(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    name: str,
    token_hash: str,
    token_hint: str,
    note: str,
    provider_credential_id: str,
    pricing_plan_id: str,
    reasoning_effort: ReasoningEffort,
    billing_mode: BillingMode,
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO installations(id, name, token_hash, token_hint, enabled, note, "
        "provider_credential_id, pricing_plan_id, reasoning_effort, billing_mode, created_at, updated_at) "
        "VALUES (:id, :name, :token_hash, :token_hint, 1, :note, :provider_credential_id, "
        ":pricing_plan_id, :reasoning_effort, :billing_mode, :created_at, :created_at)",
        {
            "id": installation_id,
            "name": name,
            "token_hash": token_hash,
            "token_hint": token_hint,
            "note": note,
            "provider_credential_id": provider_credential_id,
            "pricing_plan_id": pricing_plan_id,
            "reasoning_effort": reasoning_effort,
            "billing_mode": billing_mode,
            "created_at": created_at,
        },
    )
    connection.execute(
        "INSERT INTO installation_balances(installation_id, balance_microusd) "
        "VALUES (:installation_id, 0)",
        {"installation_id": installation_id},
    )


def rotate_token(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    token_hash: str,
    token_hint: str,
    updated_at: str,
) -> bool:
    return bool(
        connection.execute(
            "UPDATE installations SET token_hash=:token_hash, token_hint=:token_hint, "
            "updated_at=:updated_at WHERE id=:installation_id",
            {
                "installation_id": installation_id,
                "token_hash": token_hash,
                "token_hint": token_hint,
                "updated_at": updated_at,
            },
        ).rowcount
    )


def update(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    name: str,
    note: str,
    enabled: bool,
    provider_credential_id: str,
    pricing_plan_id: str,
    reasoning_effort: ReasoningEffort,
    billing_mode: BillingMode,
    updated_at: str,
) -> bool:
    return bool(
        connection.execute(
            "UPDATE installations SET name=:name, note=:note, enabled=:enabled, "
            "provider_credential_id=:provider_credential_id, pricing_plan_id=:pricing_plan_id, "
            "reasoning_effort=:reasoning_effort, billing_mode=:billing_mode, updated_at=:updated_at "
            "WHERE id=:installation_id",
            {
                "installation_id": installation_id,
                "name": name,
                "note": note,
                "enabled": int(enabled),
                "provider_credential_id": provider_credential_id,
                "pricing_plan_id": pricing_plan_id,
                "reasoning_effort": reasoning_effort,
                "billing_mode": billing_mode,
                "updated_at": updated_at,
            },
        ).rowcount
    )
