from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from gateway.server import GatewaySettings, create_app
from gateway.database import GatewayDatabase
from gateway.domain import ProviderRequest, ProviderResult, TraceContext
from gateway.errors import GatewayError
from gateway.persistence.migrations import SchemaCompatibilityError


class BillingProvider:
    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def count_input_tokens(_request: ProviderRequest) -> int:
        return 100

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.calls += 1
        return ProviderResult.from_response(
            {
                "id": "provider-billing",
                "model": "gpt-5.6-terra",
                "output_text": "{}",
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
                    "output_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 2},
                    "total_tokens": 110,
                },
            },
            {"x-request-id": "provider-request-billing"},
            fallback_model=request.fallback_model,
        )

    @staticmethod
    def check_model(model: str) -> str:
        return model


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_gateway_database_rejects_unversioned_calls_table_without_modifying_it(tmp_path) -> None:
    path = tmp_path / "unversioned-gateway.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((FIXTURES / "unversioned_calls.sql").read_text(encoding="utf-8"))
    database = GatewayDatabase(path)

    with pytest.raises(SchemaCompatibilityError, match="unversioned schema"):
        database.initialize()

    with database.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
    assert "user_id" not in columns


def test_gateway_tracks_user_dimensions_and_enforces_monthly_limit(tmp_path) -> None:
    provider = BillingProvider()
    app = create_app(GatewaySettings(tmp_path / "gateway", "admin"), provider)
    configuration = app.state.configuration
    accounting = app.state.accounting
    service = app.state.service
    installation, token = configuration.create_installation("Billing test")
    accounting.add_credit(installation.id, Decimal("100"), "test credit")
    authenticated = configuration.authenticate_principal(token)
    payload = {"input": [{"role": "user", "content": "Create a chart"}], "max_output_tokens": 100}
    trace = TraceContext(
        end_user_id="user-one",
        board_id="board-one",
        chat_id="chat-one",
        app_ai_call_id="ai-one",
    )

    accounting.set_user_limit(installation.id, "user-one", Decimal("0"))
    with pytest.raises(GatewayError) as rejected:
        service.execute(authenticated, "request-blocked", payload, trace_context=trace)
    assert rejected.value.status_code == 429
    assert rejected.value.code == "user_budget_exceeded"
    assert provider.calls == 0

    accounting.set_user_limit(installation.id, "user-one", Decimal("100"))
    _response, headers = service.execute(authenticated, "request-accepted", payload, trace_context=trace)
    stored = accounting.call(headers["x-grapyth-gateway-call-id"])
    assert stored.user_id == "user-one"
    assert stored.board_id == "board-one"
    assert stored.chat_id == "chat-one"
    assert stored.app_ai_call_id == "ai-one"
    usage = accounting.user_usage(installation.id)[0]
    assert usage.user_id == "user-one"
    assert usage.calls == 1
    assert usage.monthly_limit_usd == "100.000000"
    assert usage.spent_usd > 0


def test_credit_transaction_rolls_back_when_balance_invariant_is_broken(tmp_path) -> None:
    app = create_app(GatewaySettings(tmp_path / "gateway", "admin"), BillingProvider())
    installation, _token = app.state.configuration.create_installation("Broken balance")
    with app.state.database.connect() as connection:
        connection.execute(
            "DELETE FROM installation_balances WHERE installation_id=:installation_id",
            {"installation_id": installation.id},
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="balance row is missing"):
        app.state.accounting.add_credit(
            installation.id,
            Decimal("10"),
            "must roll back",
        )

    with app.state.database.connect() as connection:
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM credit_ledger WHERE installation_id=:installation_id",
            {"installation_id": installation.id},
        ).fetchone()[0]
    assert ledger_count == 0
