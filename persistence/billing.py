from __future__ import annotations

import sqlite3
from collections import defaultdict
from decimal import Decimal

from ..domain import UserBudget, format_money
from .models import LedgerReadModel, UserUsageReadModel


def balance(connection: sqlite3.Connection, installation_id: str) -> Decimal:
    row = connection.execute(
        "SELECT balance_microusd FROM installation_balances "
        "WHERE installation_id=:installation_id",
        {"installation_id": installation_id},
    ).fetchone()
    if not row:
        raise RuntimeError("Installation balance row is missing")
    return Decimal(int(row["balance_microusd"])) / Decimal(1_000_000)


def apply_balance_delta(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    amount_usd: str,
) -> None:
    balance_microusd = int(format_money(amount_usd).replace(".", ""))
    result = connection.execute(
        "UPDATE installation_balances SET "
        "balance_microusd=balance_microusd + :balance_microusd "
        "WHERE installation_id=:installation_id",
        {
            "installation_id": installation_id,
            "balance_microusd": balance_microusd,
        },
    )
    if result.rowcount != 1:
        raise RuntimeError("Installation balance row is missing")


def insert_credit(
    connection: sqlite3.Connection,
    *,
    ledger_id: str,
    installation_id: str,
    amount_usd: str,
    note: str,
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO credit_ledger(id, installation_id, amount_usd, kind, call_id, note, created_at) "
        "VALUES (:id, :installation_id, :amount_usd, 'manual_credit', NULL, :note, :created_at)",
        {
            "id": ledger_id,
            "installation_id": installation_id,
            "amount_usd": amount_usd,
            "note": note,
            "created_at": created_at,
        },
    )
    apply_balance_delta(
        connection,
        installation_id=installation_id,
        amount_usd=amount_usd,
    )


def list_ledger(
    connection: sqlite3.Connection, *, installation_id: str, limit: int
) -> list[LedgerReadModel]:
    rows = connection.execute(
        "SELECT id, amount_usd, kind, call_id, note, created_at FROM credit_ledger "
        "WHERE installation_id=:installation_id ORDER BY created_at DESC LIMIT :limit",
        {"installation_id": installation_id, "limit": limit},
    ).fetchall()
    return [
        LedgerReadModel(
            id=str(row["id"]),
            amount_usd=str(row["amount_usd"]),
            kind=str(row["kind"]),
            call_id=str(row["call_id"]) if row["call_id"] is not None else None,
            note=str(row["note"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def get_user_budget(
    connection: sqlite3.Connection, *, installation_id: str, user_id: str
) -> UserBudget:
    row = connection.execute(
        "SELECT monthly_limit_usd, updated_at FROM user_limits "
        "WHERE installation_id=:installation_id AND user_id=:user_id",
        {"installation_id": installation_id, "user_id": user_id},
    ).fetchone()
    return UserBudget(
        user_id=user_id,
        monthly_limit_usd=(
            Decimal(str(row["monthly_limit_usd"]))
            if row and row["monthly_limit_usd"] is not None
            else None
        ),
        updated_at=str(row["updated_at"]) if row else None,
    )


def upsert_user_limit(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    user_id: str,
    monthly_limit_usd: str | None,
    updated_at: str,
) -> None:
    connection.execute(
        "INSERT INTO user_limits(installation_id, user_id, monthly_limit_usd, updated_at) "
        "VALUES (:installation_id, :user_id, :monthly_limit_usd, :updated_at) "
        "ON CONFLICT(installation_id, user_id) DO UPDATE SET "
        "monthly_limit_usd=excluded.monthly_limit_usd, updated_at=excluded.updated_at",
        {
            "installation_id": installation_id,
            "user_id": user_id,
            "monthly_limit_usd": monthly_limit_usd,
            "updated_at": updated_at,
        },
    )


def monthly_spend(
    connection: sqlite3.Connection, *, installation_id: str, user_id: str, month_start: str
) -> Decimal:
    rows = connection.execute(
        "SELECT charged_usd FROM calls WHERE installation_id=:installation_id "
        "AND user_id=:user_id AND status='ok' AND created_at>=:month_start",
        {
            "installation_id": installation_id,
            "user_id": user_id,
            "month_start": month_start,
        },
    ).fetchall()
    return sum(
        (Decimal(str(row["charged_usd"])) for row in rows if row["charged_usd"] is not None),
        Decimal("0"),
    )


def list_user_usage(
    connection: sqlite3.Connection, *, installation_id: str, month_start: str
) -> list[UserUsageReadModel]:
    # Money is stored as fixed-point text. Summing Decimal values in Python avoids SQLite REAL coercion.
    call_rows = connection.execute(
        "SELECT user_id, input_tokens, output_tokens, charged_usd FROM calls "
        "WHERE installation_id=:installation_id AND user_id<>'' AND status='ok' "
        "AND created_at>=:month_start",
        {"installation_id": installation_id, "month_start": month_start},
    ).fetchall()
    limit_rows = connection.execute(
        "SELECT user_id, monthly_limit_usd, updated_at FROM user_limits "
        "WHERE installation_id=:installation_id",
        {"installation_id": installation_id},
    ).fetchall()

    usage: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "spent_usd": Decimal("0"),
            "monthly_limit_usd": None,
            "updated_at": None,
        }
    )
    for row in call_rows:
        item = usage[str(row["user_id"])]
        item["calls"] = int(item["calls"]) + 1
        item["input_tokens"] = int(item["input_tokens"]) + int(row["input_tokens"] or 0)
        item["output_tokens"] = int(item["output_tokens"]) + int(row["output_tokens"] or 0)
        if row["charged_usd"] is not None:
            item["spent_usd"] = Decimal(str(item["spent_usd"])) + Decimal(str(row["charged_usd"]))
    for row in limit_rows:
        item = usage[str(row["user_id"])]
        item["monthly_limit_usd"] = row["monthly_limit_usd"]
        item["updated_at"] = row["updated_at"]

    results = [
        UserUsageReadModel(
            user_id=user_id,
            calls=int(value["calls"]),
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            spent_usd=Decimal(str(value["spent_usd"])),
            monthly_limit_usd=(
                str(value["monthly_limit_usd"])
                if value["monthly_limit_usd"] is not None
                else None
            ),
            updated_at=str(value["updated_at"]) if value["updated_at"] is not None else None,
        )
        for user_id, value in usage.items()
    ]
    return sorted(results, key=lambda item: item.spent_usd, reverse=True)
