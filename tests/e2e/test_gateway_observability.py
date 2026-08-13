from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from gateway.domain import ProviderRequest, ProviderResult
from gateway.providers.base import ProviderRequestError
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
async def test_http_tool_loop_records_request_and_outcome_kinds(tmp_path: Path) -> None:
    class ToolLoopProvider(Provider):
        def __init__(self):
            self.invocation_count = 0

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            self.invocation_count += 1
            output = (
                [
                    {
                        "type": "function_call",
                        "call_id": "tool-call-e2e",
                        "name": "read_database_sample",
                        "arguments": "{}",
                    }
                ]
                if self.invocation_count == 1
                else []
            )
            return ProviderResult.from_response(
                {
                    "id": f"provider-tool-{self.invocation_count}",
                    "model": request.payload["model"],
                    "output": output,
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                },
                {"x-request-id": f"provider-tool-request-{self.invocation_count}"},
                fallback_model=request.fallback_model,
            )

    app = create_app(GatewaySettings(tmp_path, "admin-secret"), ToolLoopProvider())
    installation, token = app.state.configuration.create_installation("Tool loop client")
    app.state.accounting.add_credit(installation.id, Decimal("10"), "tool loop credit")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        first = await client.post(
            "/v1/responses",
            headers={**headers, "Idempotency-Key": "tool-loop-first"},
            json={
                "input": [{"role": "user", "content": "read a sample"}],
                "max_output_tokens": 20,
            },
        )
        second = await client.post(
            "/v1/responses",
            headers={**headers, "Idempotency-Key": "tool-loop-second"},
            json={
                "input": [
                    {
                        "type": "function_call_output",
                        "call_id": "tool-call-e2e",
                        "output": "{}",
                    }
                ],
                "max_output_tokens": 20,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    first_call = app.state.accounting.call(first.headers["x-grapyth-gateway-call-id"])
    second_call = app.state.accounting.call(second.headers["x-grapyth-gateway-call-id"])
    assert first_call.request_kind == "standard"
    assert first_call.outcome_kind == "tool_requested"
    assert first_call.requested_tool_call_count == 1
    assert second_call.request_kind == "tool_continuation"
    assert second_call.outcome_kind == "final_response"
    usage_entries = [
        item
        for item in app.state.accounting.list_ledger(installation.id)
        if item.kind == "ai_usage"
    ]
    assert len(usage_entries) == 2


@pytest.mark.anyio
async def test_provider_quota_error_is_safe_and_has_no_retry_guidance(
    tmp_path: Path,
    caplog,
) -> None:
    class QuotaLimitedProvider(Provider):
        @staticmethod
        def invoke(_request: ProviderRequest) -> ProviderResult:
            raise ProviderRequestError(
                error_code="credit_balance_exhausted",
                error_type="insufficient_quota",
                status_code=429,
                request_id="req-provider-credit-e2e",
                retry_after_seconds=9,
            )

    caplog.set_level(logging.INFO, logger=LOGGER.name)
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), QuotaLimitedProvider())
    installation, token = app.state.configuration.create_installation("Quota client")
    app.state.accounting.add_credit(installation.id, Decimal("10"), "gateway credit")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        response = await client.post(
            "/v1/responses",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "provider-quota-e2e",
                "X-Correlation-ID": "support-provider-quota",
            },
            json={
                "input": [{"role": "user", "content": "private quota payload"}],
                "max_output_tokens": 20,
            },
        )

    assert response.status_code == 402
    assert "retry-after" not in response.headers
    error = response.json()["error"]
    assert error == {
        "message": "The AI provider quota is unavailable",
        "type": "grapyth_gateway_error",
        "code": "provider_quota_exceeded",
        "providerErrorCode": "credit_balance_exhausted",
        "providerErrorType": "insufficient_quota",
        "providerStatusCode": 429,
        "providerRequestId": "req-provider-credit-e2e",
        "gatewayCallId": app.state.accounting.list_calls(installation.id)[0].id,
        "gatewayPhase": "invoking_provider",
        "failureKind": "upstream_rejection",
    }
    assert app.state.accounting.list_calls(installation.id)[0].error_code == (
        "credit_balance_exhausted"
    )

    events = []
    for record in caplog.records:
        try:
            events.append(json.loads(record.getMessage()))
        except json.JSONDecodeError:
            continue
    request_event = next(
        event
        for event in events
        if event.get("event") == "gateway.http_request"
        and event.get("correlationId") == "support-provider-quota"
    )
    assert request_event["errorCode"] == "provider_quota_exceeded"
    assert request_event["providerErrorCode"] == "credit_balance_exhausted"
    assert request_event["providerErrorType"] == "insufficient_quota"
    assert request_event["providerStatusCode"] == 429
    assert request_event["providerRequestId"] == "req-provider-credit-e2e"
    assert "retryAfterSeconds" not in request_event
    ai_event = next(
        event
        for event in events
        if event.get("event") == "gateway.ai_call"
        and event.get("correlationId") == "support-provider-quota"
    )
    assert ai_event["errorCode"] == "provider_quota_exceeded"
    assert ai_event["providerErrorCode"] == "credit_balance_exhausted"
    assert "retryAfterSeconds" not in ai_event
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "private quota payload" not in rendered_logs


@pytest.mark.anyio
async def test_provider_rate_limit_for_token_count_preserves_retry_after(
    tmp_path: Path,
) -> None:
    class RateLimitedCountProvider(Provider):
        @staticmethod
        def count_input_tokens(_request: ProviderRequest) -> int:
            raise ProviderRequestError(
                error_code="rate_limit_exceeded",
                error_type="rate_limit_error",
                    status_code=429,
                    request_id="req-provider-rate-e2e",
                    param="input[4].status",
                    retry_after_seconds=3,
            )

    app = create_app(GatewaySettings(tmp_path, "admin-secret"), RateLimitedCountProvider())
    installation, token = app.state.configuration.create_installation("Rate client")
    app.state.accounting.add_credit(installation.id, Decimal("10"), "gateway credit")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        response = await client.post(
            "/v1/responses",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "provider-rate-e2e",
            },
            json={
                "input": [{"role": "user", "content": "rate limited"}],
                "max_output_tokens": 20,
            },
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"
    assert response.json()["error"]["code"] == "provider_rate_limited"
    assert response.json()["error"]["providerErrorCode"] == "rate_limit_exceeded"
    assert response.json()["error"]["providerErrorParam"] == "input[4].status"
    call = app.state.accounting.list_calls(installation.id)[0]
    assert response.headers["x-grapyth-gateway-call-id"] == call.id
    assert call.status == "error"
    assert call.gateway_error_code == "provider_rate_limited"
    assert call.provider_request_id == "req-provider-rate-e2e"
    assert call.to_payload()["failure"] == {
        "phase": "counting_tokens",
        "kind": "upstream_rejection",
        "provider": {
            "code": "rate_limit_exceeded",
            "type": "rate_limit_error",
            "statusCode": 429,
            "param": "input[4].status",
            "retryAfterSeconds": 3,
        },
    }


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
