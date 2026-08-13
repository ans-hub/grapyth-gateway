from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing
from decimal import Decimal

from ..database import GatewayDatabase, utc_now
from ..domain import (
    CallCompletion,
    CallRecord,
    CallStart,
    UserBudget,
    format_money,
)
from ..errors import IdempotencyConflict
from ..persistence import audit as audit_repository
from ..persistence import billing as billing_repository
from ..persistence import calls as call_repository
from ..persistence import installations as installation_repository
from ..persistence.models import (
    AuditReadModel,
    CallReadModel,
    InstallationSummaryReadModel,
    LedgerReadModel,
    UserUsageReadModel,
)
from .audit import write_audit_event


class GatewayAccountingStore:
    """Persist calls, credits, budgets, ledger entries, and usage views."""

    def __init__(self, database: GatewayDatabase):
        self.database = database

    def balance(self, installation_id: str) -> Decimal:
        with closing(self.database.connect()) as connection:
            return billing_repository.balance(connection, installation_id)

    def reconcile_interrupted_calls(self) -> int:
        with self.database.transaction() as connection:
            return call_repository.fail_running(connection, completed_at=utc_now())

    def add_credit(
        self,
        installation_id: str,
        amount: Decimal,
        note: str = "",
    ) -> InstallationSummaryReadModel:
        if not amount.is_finite() or amount == 0:
            raise ValueError("Credit adjustment must not be zero")
        with self.database.transaction() as connection:
            if not installation_repository.exists(connection, installation_id):
                raise FileNotFoundError("Installation not found")
            resolved_note = str(note or "")[:1000]
            billing_repository.insert_credit(
                connection,
                ledger_id="ledger-" + uuid.uuid4().hex,
                installation_id=installation_id,
                amount_usd=format_money(amount),
                note=resolved_note,
                created_at=utc_now(),
            )
            write_audit_event(
                connection,
                "credit.adjusted",
                "installation",
                installation_id,
                {
                    "amountUsd": format_money(amount),
                    "note": resolved_note,
                },
            )
            result = installation_repository.get_summary(connection, installation_id)
        assert result is not None
        return result

    def begin_call(self, value: CallStart) -> CallRecord:
        call_id = "gw-call-" + uuid.uuid4().hex
        try:
            with self.database.transaction() as connection:
                call_repository.insert_running(
                    connection,
                    call_id=call_id,
                    installation_id=value.installation_id,
                    idempotency_key=value.idempotency_key,
                    user_id=value.user_id[:200],
                    board_id=value.board_id[:200],
                    chat_id=value.chat_id[:200],
                    app_ai_call_id=value.app_ai_call_id[:200],
                    requested_model=value.requested_model[:160],
                    request_kind=value.request_kind,
                    created_at=utc_now(),
                )
                record = call_repository.get(connection, call_id)
        except sqlite3.IntegrityError as exc:
            with closing(self.database.connect()) as connection:
                existing = call_repository.find_idempotent(
                    connection,
                    installation_id=value.installation_id,
                    idempotency_key=value.idempotency_key,
                )
            if existing:
                raise IdempotencyConflict(existing[0], existing[1]) from exc
            raise
        assert record is not None
        return self._call_record(record)

    def complete_call(self, value: CallCompletion) -> CallRecord:
        with self.database.transaction() as connection:
            if not call_repository.complete(
                connection,
                value=value,
                now=utc_now,
            ):
                raise FileNotFoundError("Gateway call not found")
            record = call_repository.get(connection, value.call_id)
        assert record is not None
        return self._call_record(record)

    def call(self, call_id: str) -> CallReadModel:
        with closing(self.database.connect()) as connection:
            record = call_repository.get(connection, call_id)
        if not record:
            raise FileNotFoundError("Gateway call not found")
        return record

    def load_call_record(self, call_id: str) -> CallRecord:
        return self._call_record(self.call(call_id))

    @staticmethod
    def _call_record(value: CallReadModel) -> CallRecord:
        return CallRecord(
            id=value.id,
            status=value.status,
            resolved_model=value.resolved_model,
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
            provider_request_id=value.provider_request_id,
            provider_cost_usd=value.provider_cost_usd,
            charged_usd=value.charged_usd,
            margin_usd=value.margin_usd,
            below_cost=value.below_cost,
            duration_ms=value.duration_ms,
            error_code=value.error_code,
            gateway_error_code=value.gateway_error_code,
            failure=value.failure,
            request_kind=value.request_kind,
            outcome_kind=value.outcome_kind,
            requested_tool_call_count=value.requested_tool_call_count,
        )

    def list_calls(
        self,
        installation_id: str,
        limit: int = 100,
    ) -> list[CallReadModel]:
        with closing(self.database.connect()) as connection:
            if not installation_repository.exists(connection, installation_id):
                raise FileNotFoundError("Installation not found")
            return call_repository.list_for_installation(
                connection,
                installation_id=installation_id,
                limit=max(1, min(int(limit), 500)),
            )

    def list_ledger(
        self,
        installation_id: str,
        limit: int = 100,
    ) -> list[LedgerReadModel]:
        with closing(self.database.connect()) as connection:
            if not installation_repository.exists(connection, installation_id):
                raise FileNotFoundError("Installation not found")
            return billing_repository.list_ledger(
                connection,
                installation_id=installation_id,
                limit=max(1, min(int(limit), 500)),
            )

    def load_user_budget(self, installation_id: str, user_id: str) -> UserBudget:
        normalized_user = str(user_id or "").strip()[:200]
        with closing(self.database.connect()) as connection:
            return billing_repository.get_user_budget(
                connection,
                installation_id=installation_id,
                user_id=normalized_user,
            )

    def set_user_limit(
        self,
        installation_id: str,
        user_id: str,
        monthly_limit_usd: Decimal | None,
    ) -> UserBudget:
        normalized_user = str(user_id or "").strip()[:200]
        if not normalized_user:
            raise ValueError("userId is required")
        if monthly_limit_usd is None:
            resolved = None
        else:
            if (
                not monthly_limit_usd.is_finite()
                or monthly_limit_usd < 0
                or monthly_limit_usd > Decimal("1000000")
            ):
                raise ValueError("monthlyLimitUsd is outside the supported range")
            resolved = format_money(monthly_limit_usd)
        with self.database.transaction() as connection:
            if not installation_repository.exists(connection, installation_id):
                raise FileNotFoundError("Installation not found")
            billing_repository.upsert_user_limit(
                connection,
                installation_id=installation_id,
                user_id=normalized_user,
                monthly_limit_usd=resolved,
                updated_at=utc_now(),
            )
            write_audit_event(
                connection,
                "user_limit.updated",
                "user",
                normalized_user,
                {
                    "installationId": installation_id,
                    "monthlyLimitUsd": resolved,
                },
            )
            return billing_repository.get_user_budget(
                connection,
                installation_id=installation_id,
                user_id=normalized_user,
            )

    @staticmethod
    def current_month_start() -> str:
        return time.strftime("%Y-%m-01T00:00:00Z", time.gmtime())

    def user_monthly_spend(self, installation_id: str, user_id: str) -> Decimal:
        with closing(self.database.connect()) as connection:
            return billing_repository.monthly_spend(
                connection,
                installation_id=installation_id,
                user_id=user_id,
                month_start=self.current_month_start(),
            )

    def user_usage(self, installation_id: str) -> list[UserUsageReadModel]:
        with closing(self.database.connect()) as connection:
            if not installation_repository.exists(connection, installation_id):
                raise FileNotFoundError("Installation not found")
            return billing_repository.list_user_usage(
                connection,
                installation_id=installation_id,
                month_start=self.current_month_start(),
            )

    def list_audit(self, limit: int = 100) -> list[AuditReadModel]:
        with closing(self.database.connect()) as connection:
            return audit_repository.list_recent(
                connection,
                limit=max(1, min(int(limit), 500)),
            )
