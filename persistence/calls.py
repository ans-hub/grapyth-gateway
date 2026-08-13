from __future__ import annotations

import json
import sqlite3
import uuid
from decimal import Decimal
from typing import Callable, cast

from ..domain import (
    CALL_OUTCOME_KINDS,
    CALL_REQUEST_KINDS,
    CallCompletion,
    CallFailure,
    CallOutcomeKind,
    CallRequestKind,
    CallStatus,
    ReasoningEffort,
    format_money,
)
from . import billing as billing_repository
from .models import CallReadModel


CALL_COLUMNS = (
    "id, installation_id, idempotency_key, user_id, board_id, chat_id, app_ai_call_id, status, "
    "requested_model, resolved_model, reasoning_effort, input_tokens, cached_input_tokens, "
    "cache_write_tokens, output_tokens, reasoning_tokens, total_tokens, provider_request_id, "
    "estimated_provider_cost_usd, provider_cost_usd, charged_usd, margin_usd, pricing_plan_id, "
    "pricing_version, provider_rates_json, billed_rates_json, below_cost, duration_ms, error_code, "
    "gateway_error_code, failure_json, request_kind, outcome_kind, requested_tool_call_count, "
    "created_at, completed_at"
)


def _map(row: sqlite3.Row) -> CallReadModel:
    request_kind = str(row["request_kind"])
    outcome_kind = str(row["outcome_kind"])
    if request_kind not in CALL_REQUEST_KINDS:
        raise ValueError("Stored call request kind is invalid")
    if outcome_kind not in CALL_OUTCOME_KINDS:
        raise ValueError("Stored call outcome kind is invalid")
    failure_payload = json.loads(str(row["failure_json"]))
    if not isinstance(failure_payload, dict):
        raise ValueError("Stored call failure diagnostics must be an object")
    failure = CallFailure.from_payload(failure_payload) if failure_payload else None
    return CallReadModel(
        id=str(row["id"]),
        installation_id=str(row["installation_id"]),
        idempotency_key=str(row["idempotency_key"]),
        user_id=str(row["user_id"]),
        board_id=str(row["board_id"]),
        chat_id=str(row["chat_id"]),
        app_ai_call_id=str(row["app_ai_call_id"]),
        status=cast(CallStatus, str(row["status"])),
        requested_model=str(row["requested_model"]),
        resolved_model=str(row["resolved_model"]),
        reasoning_effort=cast(
            ReasoningEffort,
            str(row["reasoning_effort"]),
        ),
        input_tokens=int(row["input_tokens"]),
        cached_input_tokens=int(row["cached_input_tokens"]),
        cache_write_tokens=int(row["cache_write_tokens"]),
        output_tokens=int(row["output_tokens"]),
        reasoning_tokens=int(row["reasoning_tokens"]),
        total_tokens=int(row["total_tokens"]),
        provider_request_id=str(row["provider_request_id"]),
        estimated_provider_cost_usd=row["estimated_provider_cost_usd"],
        provider_cost_usd=row["provider_cost_usd"],
        charged_usd=row["charged_usd"],
        margin_usd=row["margin_usd"],
        pricing_plan_id=str(row["pricing_plan_id"]),
        pricing_version=str(row["pricing_version"]),
        provider_rates_json=str(row["provider_rates_json"]),
        billed_rates_json=str(row["billed_rates_json"]),
        below_cost=bool(row["below_cost"]),
        duration_ms=float(row["duration_ms"]),
        error_code=str(row["error_code"]),
        gateway_error_code=str(row["gateway_error_code"]),
        failure=failure,
        request_kind=cast(CallRequestKind, request_kind),
        outcome_kind=cast(CallOutcomeKind, outcome_kind),
        requested_tool_call_count=int(row["requested_tool_call_count"]),
        created_at=str(row["created_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
    )


def get(connection: sqlite3.Connection, call_id: str) -> CallReadModel | None:
    row = connection.execute(
        f"SELECT {CALL_COLUMNS} FROM calls WHERE id=:call_id",
        {"call_id": call_id},
    ).fetchone()
    return _map(row) if row else None


def list_for_installation(
    connection: sqlite3.Connection, *, installation_id: str, limit: int
) -> list[CallReadModel]:
    rows = connection.execute(
        f"SELECT {CALL_COLUMNS} FROM calls WHERE installation_id=:installation_id "
        "ORDER BY created_at DESC LIMIT :limit",
        {"installation_id": installation_id, "limit": limit},
    ).fetchall()
    return [_map(row) for row in rows]


def insert_running(
    connection: sqlite3.Connection,
    *,
    call_id: str,
    installation_id: str,
    idempotency_key: str,
    user_id: str,
    board_id: str,
    chat_id: str,
    app_ai_call_id: str,
    requested_model: str,
    request_kind: str,
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO calls(id, installation_id, idempotency_key, user_id, board_id, chat_id, "
        "app_ai_call_id, status, requested_model, request_kind, created_at) VALUES (:id, :installation_id, "
        ":idempotency_key, :user_id, :board_id, :chat_id, :app_ai_call_id, 'running', "
        ":requested_model, :request_kind, :created_at)",
        {
            "id": call_id,
            "installation_id": installation_id,
            "idempotency_key": idempotency_key,
            "user_id": user_id,
            "board_id": board_id,
            "chat_id": chat_id,
            "app_ai_call_id": app_ai_call_id,
            "requested_model": requested_model,
            "request_kind": request_kind,
            "created_at": created_at,
        },
    )


def find_idempotent(
    connection: sqlite3.Connection, *, installation_id: str, idempotency_key: str
) -> tuple[str, str] | None:
    row = connection.execute(
        "SELECT id, status FROM calls WHERE installation_id=:installation_id "
        "AND idempotency_key=:idempotency_key",
        {"installation_id": installation_id, "idempotency_key": idempotency_key},
    ).fetchone()
    return (str(row["id"]), str(row["status"])) if row else None


def fail_running(connection: sqlite3.Connection, *, completed_at: str) -> int:
    failure_json = json.dumps(
        CallFailure(
            phase="unknown",
            kind="gateway_failure",
        ).to_payload(),
        separators=(",", ":"),
    )
    result = connection.execute(
        "UPDATE calls SET status='error', error_code='gateway_restarted', "
        "gateway_error_code='gateway_restarted', failure_json=:failure_json, "
        "completed_at=:completed_at WHERE status='running'",
        {
            "failure_json": failure_json,
            "completed_at": completed_at,
        },
    )
    return max(0, result.rowcount)


def complete(
    connection: sqlite3.Connection,
    *,
    value: CallCompletion,
    now: Callable[[], str],
) -> bool:
    call = connection.execute(
        "SELECT installation_id FROM calls WHERE id=:call_id",
        {"call_id": value.call_id},
    ).fetchone()
    if not call:
        return False
    provider_cost = (
        format_money(value.provider_cost_usd)
        if value.provider_cost_usd is not None
        else None
    )
    charge = format_money(value.charged_usd) if value.charged_usd is not None else None
    margin = format_money(value.margin_usd) if value.margin_usd is not None else None
    completed_at = now()
    connection.execute(
        "UPDATE calls SET status=:status, resolved_model=:resolved_model, "
        "reasoning_effort=:reasoning_effort, input_tokens=:input_tokens, "
        "cached_input_tokens=:cached_input_tokens, cache_write_tokens=:cache_write_tokens, "
        "output_tokens=:output_tokens, reasoning_tokens=:reasoning_tokens, total_tokens=:total_tokens, "
        "provider_request_id=:provider_request_id, estimated_provider_cost_usd=:provider_cost_usd, "
        "provider_cost_usd=:provider_cost_usd, charged_usd=:charged_usd, margin_usd=:margin_usd, "
        "pricing_plan_id=:pricing_plan_id, pricing_version=:pricing_version, "
        "provider_rates_json=:provider_rates_json, billed_rates_json=:billed_rates_json, "
        "below_cost=:below_cost, duration_ms=:duration_ms, error_code=:error_code, "
        "gateway_error_code=:gateway_error_code, failure_json=:failure_json, "
        "outcome_kind=:outcome_kind, requested_tool_call_count=:requested_tool_call_count, "
        "completed_at=:completed_at WHERE id=:call_id",
        {
            "call_id": value.call_id,
            "status": value.status,
            "resolved_model": value.resolved_model,
            "reasoning_effort": value.reasoning_effort,
            "input_tokens": value.usage.input_tokens,
            "cached_input_tokens": value.usage.cached_input_tokens,
            "cache_write_tokens": value.usage.cache_write_tokens,
            "output_tokens": value.usage.output_tokens,
            "reasoning_tokens": value.usage.reasoning_tokens,
            "total_tokens": value.usage.total_tokens,
            "provider_request_id": value.provider_request_id[:200],
            "provider_cost_usd": provider_cost,
            "charged_usd": charge,
            "margin_usd": margin,
            "pricing_plan_id": value.pricing.plan_id,
            "pricing_version": value.pricing.version,
            "provider_rates_json": json.dumps(
                value.pricing.provider_rates.to_payload(),
                separators=(",", ":"),
            ),
            "billed_rates_json": json.dumps(
                value.pricing.billed_rates.to_payload(),
                separators=(",", ":"),
            ),
            "below_cost": int(value.below_cost),
            "duration_ms": value.duration_ms,
            "error_code": value.error_code[:160],
            "gateway_error_code": value.gateway_error_code[:160],
            "failure_json": json.dumps(
                value.failure.to_payload() if value.failure is not None else {},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "outcome_kind": value.outcome_kind,
            "requested_tool_call_count": value.requested_tool_call_count,
            "completed_at": completed_at,
        },
    )
    if value.record_ledger and charge is not None and Decimal(charge) > 0:
        ledger_amount = format_money(-Decimal(str(charge)))
        connection.execute(
            "INSERT INTO credit_ledger(id, installation_id, amount_usd, kind, call_id, note, created_at) "
            "VALUES (:id, :installation_id, :amount_usd, 'ai_usage', :call_id, '', :created_at)",
            {
                "id": "ledger-" + uuid.uuid4().hex,
                "installation_id": str(call["installation_id"]),
                "amount_usd": ledger_amount,
                "call_id": value.call_id,
                "created_at": completed_at,
            },
        )
        billing_repository.apply_balance_delta(
            connection,
            installation_id=str(call["installation_id"]),
            amount_usd=ledger_amount,
        )
    return True
