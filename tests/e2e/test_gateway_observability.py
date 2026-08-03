from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from gateway.domain import ProviderRequest, ProviderResult
from gateway.server import GatewaySettings, create_app
from gateway.services.calls import LOGGER


class Provider:
    @staticmethod
    def count_input_tokens(_request: ProviderRequest) -> int:
        return 10

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        return ProviderResult.from_response(
            {
                "id": "provider-response-observability",
                "model": request.payload["model"],
                "output_text": "ok",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {"x-request-id": "provider-request-observability"},
            fallback_model=request.fallback_model,
        )

    @staticmethod
    def check_model(model: str) -> str:
        return model


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_gateway_correlates_early_errors_and_provider_calls_without_payloads(
    tmp_path: Path, caplog
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER.name)
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), Provider())
    installation, token = app.state.configuration.create_installation(
        "Observable client"
    )
    app.state.accounting.add_credit(
        installation.id,
        Decimal("10"),
        "test credit",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        ready = await client.get("/ready", headers={"X-Correlation-ID": "ready-check"})
        assert ready.status_code == 200
        assert ready.json() == {"status": "ok", "issues": []}

        denied = await client.post(
            "/v1/responses",
            headers={
                "Authorization": "Bearer installation-secret-must-not-be-logged",
                "Idempotency-Key": "denied-call",
                "X-Correlation-ID": "support-denied",
                "X-Grapyth-App-Request-ID": "app-request-denied",
                "X-Grapyth-AI-Call-ID": "ai-denied",
            },
            json={"input": [{"role": "user", "content": "private denied payload"}]},
        )
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "invalid_installation_token"
        assert denied.json()["correlationId"] == "support-denied"
        assert denied.headers["x-grapyth-error-code"] == "invalid_installation_token"
        assert denied.headers["x-request-id"].startswith("gwr-")

        accepted = await client.post(
            "/v1/responses",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "accepted-call",
                "X-Correlation-ID": "support-accepted",
                "X-Grapyth-App-Request-ID": "app-request-accepted",
                "X-Grapyth-AI-Call-ID": "ai-accepted",
            },
            json={
                "model": "client-model-is-ignored",
                "input": [{"role": "user", "content": "private accepted payload"}],
                "max_output_tokens": 20,
            },
        )
        assert accepted.status_code == 200
        assert accepted.headers["x-correlation-id"] == "support-accepted"
        assert accepted.headers["x-grapyth-gateway-call-id"].startswith("gw-call-")

    events = []
    for record in caplog.records:
        try:
            events.append(json.loads(record.getMessage()))
        except json.JSONDecodeError:
            continue

    denied_event = next(
        event
        for event in events
        if event.get("event") == "gateway.http_request"
        and event.get("correlationId") == "support-denied"
    )
    assert denied_event["status"] == 401
    assert denied_event["errorCode"] == "invalid_installation_token"
    assert denied_event["appRequestId"] == "app-request-denied"
    assert denied_event["appAiCallId"] == "ai-denied"

    ai_event = next(
        event
        for event in events
        if event.get("event") == "gateway.ai_call"
        and event.get("correlationId") == "support-accepted"
    )
    assert ai_event["status"] == "ok"
    assert ai_event["appRequestId"] == "app-request-accepted"
    assert ai_event["appAiCallId"] == "ai-accepted"
    assert ai_event["providerRequestId"] == "provider-request-observability"

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "installation-secret-must-not-be-logged" not in rendered_logs
    assert "private denied payload" not in rendered_logs
    assert "private accepted payload" not in rendered_logs


@pytest.mark.anyio
async def test_readiness_reports_schema_drift_without_affecting_liveness(tmp_path: Path) -> None:
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), Provider())
    with app.state.database.connect() as connection:
        connection.execute("DROP INDEX audit_log_created")
        connection.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "error",
        "issues": ["database_schema_incompatible"],
    }


@pytest.mark.anyio
async def test_readiness_rejects_undecryptable_enabled_provider_credentials(
    tmp_path: Path,
) -> None:
    app = create_app(
        GatewaySettings(
            tmp_path,
            "admin-secret",
            master_key=Fernet.generate_key().decode("ascii"),
        )
    )
    app.state.credentials.create_provider_credential(
        "Broken provider",
        "not-a-valid-encrypted-secret",
        "invalid",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "error",
        "issues": ["provider_credentials_undecryptable"],
    }
