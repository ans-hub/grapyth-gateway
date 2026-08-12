from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import logging
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from openai import RateLimitError

from gateway.database import GatewayDatabase
from gateway.config import DEFAULT_PRICING_VERSION, DEFAULT_RATES
from gateway.domain import (
    BillingMode,
    InstallationPolicy,
    InstallationPrincipal,
    MAX_STANDARD_PRICING_INPUT_TOKENS,
    PricingPlanChanges,
    PricingPlanSpec,
    PricingRates,
    ProviderRequest,
    ProviderResult,
    ProviderUsage,
    normalize_provider_request,
    worst_case_cost,
)
from gateway.providers.base import (
    MAX_RETRY_AFTER_SECONDS,
    FixedProviderSelector,
    ProviderRequestError,
)
from gateway.errors import GatewayError
from gateway.providers.openai import OpenAIProvider
from gateway.service import GatewayService
from gateway.services.calls import LOGGER
from gateway.stores import GatewayAccountingStore, GatewayConfigurationStore


class RecordingProvider:
    def __init__(self):
        self.payloads = []
        self.token_count_requests = []
        self.input_token_count = 1000

    def count_input_tokens(self, request: ProviderRequest) -> int:
        self.token_count_requests.append(request)
        return self.input_token_count

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        payload = dict(request.payload)
        self.payloads.append(payload)
        response = {
            "id": "response-one",
            "model": "gpt-5.6-terra",
            "output_text": "done",
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
                "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": 20},
                "total_tokens": 1100,
            },
        }
        return ProviderResult.from_response(
            response,
            {"x-request-id": "provider-one"},
            fallback_model=request.fallback_model,
        )


def configured_stores(
    tmp_path: Path,
    *,
    billed_rates: PricingRates | None = None,
    billing_mode: BillingMode = "prepaid",
):
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    configuration = GatewayConfigurationStore(database)
    accounting = GatewayAccountingStore(database)
    plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Test pricing",
            model="gpt-5.6-terra",
            version=DEFAULT_PRICING_VERSION,
            provider_rates=DEFAULT_RATES,
            billed_rates=billed_rates or DEFAULT_RATES,
            allow_below_cost=billed_rates is not None,
            below_cost_reason=(
                "Approved test discount" if billed_rates is not None else ""
            ),
        )
    )
    configuration.update_defaults(
        pricing_plan_id=plan.id,
        reasoning_effort="low",
        billing_mode=billing_mode,
    )
    return database, configuration, accounting, plan


def make_service(tmp_path: Path):
    database, configuration, accounting, _plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Client One")
    accounting.add_credit(installation.id, Decimal("10"), "pilot")
    provider = RecordingProvider()
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )
    return (
        database,
        configuration,
        accounting,
        service,
        provider,
        configuration.authenticate_principal(token),
    )


def request_payload(message: str = "confidential board content") -> dict:
    return {
        "model": "gpt-5.6-terra",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": message}]}],
        "max_output_tokens": 4096,
        "store": True,
    }


def test_typed_orchestrator_keeps_the_managed_call_steps_in_order(
    tmp_path: Path, monkeypatch
) -> None:
    _database, configuration, accounting, _plan = configured_stores(
        tmp_path,
        billing_mode="meter_only",
    )
    installation, token = configuration.create_installation("Typed provider")
    events: list[str] = []
    provider_requests: list[ProviderRequest] = []

    class TypedProvider:
        def count_input_tokens(self, request: ProviderRequest) -> int:
            events.append("count_input_tokens")
            assert request.gateway_call_id == ""
            return 10

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            events.append("provider")
            provider_requests.append(request)
            return ProviderResult(
                response={"id": "typed-response", "output_text": "done"},
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                provider_request_id="typed-request",
                resolved_model="gpt-5.6-terra",
            )

    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(TypedProvider()),
    )

    def record_store_call(store, name: str) -> None:
        original = getattr(store, name)

        def wrapped(*args, **kwargs):
            events.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(store, name, wrapped)

    for store, method in (
        (configuration, "load_runtime_policy"),
        (configuration, "load_gateway_limits"),
        (accounting, "begin_call"),
        (accounting, "complete_call"),
        (accounting, "balance"),
    ):
        record_store_call(store, method)

    monkeypatch.setattr(
        service.admission,
        "enforce_rate_limit",
        lambda _installation_id: events.append("rate_limit"),
    )

    @contextmanager
    def installation_slot(_installation_id: str):
        events.append("slot_enter")
        try:
            yield
        finally:
            events.append("slot_exit")

    monkeypatch.setattr(service.admission, "installation_slot", installation_slot)

    response, _headers = service.execute(
        configuration.authenticate_principal(token),
        "typed-call",
        request_payload(),
    )

    assert response["id"] == "typed-response"
    assert events == [
        "load_runtime_policy",
        "load_gateway_limits",
        "rate_limit",
        "slot_enter",
        "count_input_tokens",
        "begin_call",
        "provider",
        "complete_call",
        "balance",
        "slot_exit",
    ]
    assert provider_requests[0].gateway_call_id.startswith("gw-call-")
    assert "_grapyth_gateway_call_id" not in provider_requests[0].payload
    assert accounting.list_calls(installation.id)[0].status == "ok"


def test_gateway_forces_no_provider_storage_and_records_only_metering(tmp_path: Path, caplog) -> None:
    database, configuration, accounting, service, provider, installation = make_service(
        tmp_path
    )
    caplog.set_level(logging.INFO, logger=LOGGER.name)

    response, headers = service.execute(installation, "request-one", request_payload())

    assert response["output_text"] == "done"
    assert provider.payloads[0]["store"] is False
    assert provider.payloads[0]["service_tier"] == "default"
    assert headers["x-grapyth-gateway-call-id"].startswith("gw-call-")
    assert headers["x-grapyth-billed-cost-usd"] == "0.002990"
    assert "x-grapyth-estimated-provider-cost-usd" not in headers
    assert headers["x-grapyth-budget-remaining-usd"] == "9.997010"
    assert headers["x-grapyth-policy-model"] == "gpt-5.6-terra"
    assert headers["x-grapyth-policy-reasoning-effort"] == "low"
    call = accounting.call(headers["x-grapyth-gateway-call-id"])
    assert call.status == "ok"
    assert call.input_tokens == 1000
    assert call.cached_input_tokens == 200
    assert call.cache_write_tokens == 300
    assert call.output_tokens == 100
    assert call.reasoning_tokens == 20
    assert call.charged_usd == "0.002990"
    assert call.provider_cost_usd == "0.002990"
    assert call.margin_usd == "0.000000"
    assert call.pricing_version == DEFAULT_PRICING_VERSION
    assert call.provider_request_id == "provider-one"
    usage_rows = [
        row
        for row in accounting.list_ledger(installation.id)
        if row.kind == "ai_usage"
    ]
    assert len(usage_rows) == 1
    assert usage_rows[0].call_id == call.id
    assert usage_rows[0].amount_usd == "-0.002990"
    assert configuration.installation(installation.id).balance_usd == Decimal(
        "9.997010"
    )
    assert LOGGER.name.startswith("uvicorn.error.")
    assert '"event": "gateway.ai_call"' in caplog.text
    assert "confidential board content" not in caplog.text
    with database.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
        stored = json.dumps([dict(row) for row in connection.execute("SELECT * FROM calls")])
    assert not {"request", "response", "prompt", "payload"} & columns
    assert "confidential board content" not in stored


def test_gateway_setting_caps_output_immediately_without_restart(tmp_path: Path) -> None:
    _database, configuration, _accounting, service, provider, installation = make_service(
        tmp_path
    )
    configuration.update_gateway_settings(max_output_tokens=1024)

    service.execute(installation, "capped-output", request_payload())

    assert provider.payloads[0]["max_output_tokens"] == 1024


def test_gateway_info_events_reach_uvicorn_stderr() -> None:
    script = (
        "import logging.config; "
        "from uvicorn.config import LOGGING_CONFIG; "
        "from gateway.services.calls import LOGGER; "
        "logging.config.dictConfig(LOGGING_CONFIG); "
        "LOGGER.info('{\"event\":\"gateway.ai_call\",\"status\":\"ok\"}')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    assert '"event":"gateway.ai_call"' in completed.stderr
    assert '"status":"ok"' in completed.stderr


def test_gateway_ignores_client_model_and_enforces_assigned_credit_policy(tmp_path: Path) -> None:
    _database, configuration, accounting, _plan = configured_stores(tmp_path)
    _item, token = configuration.create_installation("No credit")
    installation = configuration.authenticate_principal(token)
    provider = RecordingProvider()
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )
    unknown = {
        **request_payload(),
        "model": "arbitrary-expensive-model",
        "service_tier": "priority",
        "background": True,
    }

    with pytest.raises(GatewayError, match="insufficient") as credit_error:
        service.execute(installation, "unknown", unknown)
    assert credit_error.value.status_code == 402
    assert credit_error.value.code == "insufficient_credit"
    accounting.add_credit(installation.id, Decimal("10"), "paid")
    service.execute(installation, "unknown-paid", unknown)
    assert provider.payloads[0]["model"] == "gpt-5.6-terra"
    assert provider.payloads[0]["reasoning"] == {"effort": "low"}
    assert provider.payloads[0]["service_tier"] == "default"
    assert "background" not in provider.payloads[0]
    assert (
        accounting.list_calls(installation.id)[0].requested_model
        == "arbitrary-expensive-model"
    )


def test_exact_input_count_limit_is_enforced_before_call_creation(tmp_path: Path) -> None:
    _database, configuration, accounting, _plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Input boundary")
    accounting.add_credit(installation.id, Decimal("10"), "boundary credit")
    provider = RecordingProvider()
    provider.input_token_count = MAX_STANDARD_PRICING_INPUT_TOKENS + 1
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )

    with pytest.raises(GatewayError) as rejected:
        service.execute(
            configuration.authenticate_principal(token),
            "input-over-limit",
            request_payload(),
        )

    assert rejected.value.status_code == 400
    assert rejected.value.code == "input_limit_exceeded"
    assert rejected.value.details == {
        "inputTokens": MAX_STANDARD_PRICING_INPUT_TOKENS + 1,
        "maxInputTokens": MAX_STANDARD_PRICING_INPUT_TOKENS,
    }
    assert provider.payloads == []
    assert accounting.list_calls(installation.id) == []

    provider.input_token_count = MAX_STANDARD_PRICING_INPUT_TOKENS
    service.execute(
        configuration.authenticate_principal(token),
        "input-at-limit",
        request_payload(),
    )
    assert len(provider.payloads) == 1
    assert len(accounting.list_calls(installation.id)) == 1


def test_provider_token_count_failure_is_safe_and_not_billable(tmp_path: Path) -> None:
    class FailingTokenCountProvider(RecordingProvider):
        @staticmethod
        def count_input_tokens(_request: ProviderRequest) -> int:
            raise TimeoutError("private provider diagnostic")

    _database, configuration, accounting, _plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Count failure")
    accounting.add_credit(installation.id, Decimal("10"), "count credit")
    provider = FailingTokenCountProvider()
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )

    with pytest.raises(GatewayError) as failed:
        service.execute(
            configuration.authenticate_principal(token),
            "count-failed",
            request_payload(),
        )

    assert failed.value.status_code == 502
    assert failed.value.code == "provider_token_count_error"
    assert failed.value.details == {"providerErrorCode": "TimeoutError"}
    assert provider.payloads == []
    assert accounting.list_calls(installation.id) == []
    assert accounting.balance(installation.id) == Decimal("10.000000")


def test_provider_token_count_rate_limit_preserves_retry_guidance(tmp_path: Path) -> None:
    class RateLimitedTokenCountProvider(RecordingProvider):
        @staticmethod
        def count_input_tokens(_request: ProviderRequest) -> int:
            raise ProviderRequestError(
                error_code="rate_limit_exceeded",
                error_type="rate_limit_error",
                status_code=429,
                request_id="req-token-count-rate-limit",
                retry_after_seconds=7,
            )

    _database, configuration, accounting, _plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Count rate limit")
    accounting.add_credit(installation.id, Decimal("10"), "count rate credit")
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(RateLimitedTokenCountProvider()),
    )

    with pytest.raises(GatewayError) as failed:
        service.execute(
            configuration.authenticate_principal(token),
            "count-rate-limited",
            request_payload(),
        )

    assert failed.value.status_code == 429
    assert failed.value.code == "provider_rate_limited"
    assert failed.value.details == {
        "providerErrorCode": "rate_limit_exceeded",
        "providerErrorType": "rate_limit_error",
        "providerStatusCode": 429,
        "providerRequestId": "req-token-count-rate-limit",
        "retryAfterSeconds": 7,
    }
    assert accounting.list_calls(installation.id) == []
    assert accounting.balance(installation.id) == Decimal("10.000000")


def test_billed_charge_cannot_exceed_the_admitted_reserve(tmp_path: Path) -> None:
    _database, configuration, accounting, plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Reserve cap")
    provider = RecordingProvider()
    provider.input_token_count = 1
    payload = request_payload()
    payload["max_output_tokens"] = 1
    reserve = worst_case_cost(1, 1, plan.billed_rates)
    accounting.add_credit(installation.id, reserve, "exact reserve")
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )

    _response, headers = service.execute(
        configuration.authenticate_principal(token),
        "reserve-capped",
        payload,
    )

    assert headers["x-grapyth-billed-cost-usd"] == "0.000015"
    assert accounting.balance(installation.id) == Decimal("0")
    call = accounting.list_calls(installation.id)[0]
    assert call.provider_cost_usd == "0.002990"
    assert call.charged_usd == "0.000015"
    assert call.margin_usd == "-0.002975"
    assert call.below_cost is True


def test_gateway_idempotency_prevents_a_second_provider_charge(tmp_path: Path) -> None:
    _database, configuration, accounting, service, provider, installation = make_service(
        tmp_path
    )

    _response, headers = service.execute(installation, "same-request", request_payload())
    with pytest.raises(GatewayError) as duplicate:
        service.execute(installation, "same-request", request_payload())

    assert duplicate.value.status_code == 409
    assert duplicate.value.code == "idempotency_conflict"
    assert len(provider.payloads) == 1
    assert len(accounting.list_calls(installation.id)) == 1
    usage_rows = [
        row
        for row in accounting.list_ledger(installation.id)
        if row.kind == "ai_usage"
    ]
    assert len(usage_rows) == 1
    assert usage_rows[0].call_id == headers["x-grapyth-gateway-call-id"]
    assert usage_rows[0].amount_usd == "-0.002990"
    assert configuration.installation(installation.id).balance_usd == Decimal(
        "9.997010"
    )


def test_provider_failure_records_error_without_charging_credit(tmp_path: Path) -> None:
    class FailingProvider:
        calls = 0

        @staticmethod
        def count_input_tokens(_request: ProviderRequest) -> int:
            return 1000

        def invoke(self, _request: ProviderRequest) -> ProviderResult:
            self.calls += 1
            raise TimeoutError("provider timed out with private request content")

    _database, configuration, accounting, _plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Failure-safe client")
    accounting.add_credit(installation.id, Decimal("10"), "pilot")
    provider = FailingProvider()
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )

    with pytest.raises(GatewayError) as failure:
        service.execute(
            configuration.authenticate_principal(token),
            "provider-failure",
            request_payload(),
        )
    with pytest.raises(GatewayError) as duplicate:
        service.execute(
            configuration.authenticate_principal(token),
            "provider-failure",
            request_payload(),
        )

    assert failure.value.status_code == 502
    assert failure.value.code == "provider_error"
    assert failure.value.details["providerErrorCode"] == "TimeoutError"
    assert duplicate.value.status_code == 502
    assert duplicate.value.code == "previous_attempt_failed"
    assert duplicate.value.details["providerErrorCode"] == "TimeoutError"
    assert provider.calls == 1
    calls = accounting.list_calls(installation.id)
    assert len(calls) == 1
    assert calls[0].status == "error"
    assert calls[0].error_code == "TimeoutError"
    assert calls[0].charged_usd is None
    assert [row.kind for row in accounting.list_ledger(installation.id)] == [
        "manual_credit"
    ]
    assert configuration.installation(installation.id).balance_usd == Decimal(
        "10.000000"
    )


@pytest.mark.parametrize(
    ("provider_error_code", "provider_error_type"),
    [
        ("credit_balance_exhausted", "insufficient_quota"),
        ("organization_spend_limit_exceeded", "insufficient_quota"),
        ("project_spend_limit_exceeded", "insufficient_quota"),
        ("organization_usage_limit_exceeded", "insufficient_quota"),
        ("insufficient_quota", ""),
        ("unlisted_quota_code", "insufficient_quota"),
    ],
)
def test_provider_quota_failures_are_distinct_from_a_retryable_rate_limit(
    tmp_path: Path,
    caplog,
    provider_error_code: str,
    provider_error_type: str,
) -> None:
    class QuotaLimitedProvider(RecordingProvider):
        @staticmethod
        def invoke(_request: ProviderRequest) -> ProviderResult:
            raise ProviderRequestError(
                error_code=provider_error_code,
                error_type=provider_error_type,
                status_code=429,
                request_id="req-provider-credit",
                retry_after_seconds=11,
            )

    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    _database, configuration, accounting, _plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Provider quota")
    accounting.add_credit(installation.id, Decimal("10"), "gateway credit remains")
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(QuotaLimitedProvider()),
    )

    with pytest.raises(GatewayError) as failed:
        service.execute(
            configuration.authenticate_principal(token),
            "provider-quota",
            request_payload(),
        )

    assert failed.value.status_code == 402
    assert failed.value.code == "provider_quota_exceeded"
    expected_details = {
        "providerErrorCode": provider_error_code,
        "providerStatusCode": 429,
        "providerRequestId": "req-provider-credit",
        "gatewayCallId": accounting.list_calls(installation.id)[0].id,
    }
    if provider_error_type:
        expected_details["providerErrorType"] = provider_error_type
    assert failed.value.details == expected_details
    call = accounting.list_calls(installation.id)[0]
    assert call.status == "error"
    assert call.error_code == provider_error_code
    assert call.charged_usd is None
    assert accounting.balance(installation.id) == Decimal("10.000000")
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert f'"providerErrorCode": "{provider_error_code}"' in rendered_logs
    if provider_error_type:
        assert f'"providerErrorType": "{provider_error_type}"' in rendered_logs
    assert '"providerRequestId": "req-provider-credit"' in rendered_logs
    assert '"retryAfterSeconds"' not in rendered_logs


def test_parallel_calls_are_rejected_without_queueing_or_overspending_credit(tmp_path: Path) -> None:
    class BlockingProvider(RecordingProvider):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("test provider was not released")
            return super().invoke(request)

    _database, configuration, accounting, plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Concurrent client")
    provider = BlockingProvider()
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
    )
    authenticated = configuration.authenticate_principal(token)
    payload = request_payload()
    policy = configuration.load_runtime_policy(installation.id)
    normalized = normalize_provider_request(
        payload,
        model=policy.model,
        reasoning_effort=policy.reasoning_effort,
        max_output_tokens_limit=configuration.load_gateway_limits().max_output_tokens,
    )
    reserve = worst_case_cost(
        provider.input_token_count,
        normalized["max_output_tokens"],
        plan.billed_rates,
    )
    accounting.add_credit(
        installation.id,
        reserve + Decimal("0.001000"),
        "one-call reserve",
    )
    second_started = threading.Event()

    def second_call():
        second_started.set()
        return service.execute(authenticated, "parallel-two", payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.execute, authenticated, "parallel-one", payload)
        assert provider.entered.wait(timeout=5)
        second = executor.submit(second_call)
        assert second_started.wait(timeout=5)
        with pytest.raises(GatewayError) as denied:
            second.result(timeout=5)
        provider.release.set()
        _first_response, first_headers = first.result(timeout=5)

    assert denied.value.status_code == 429
    assert denied.value.code == "installation_concurrency_limited"
    assert len(provider.payloads) == 1
    calls = accounting.list_calls(installation.id)
    assert [call.id for call in calls] == [first_headers["x-grapyth-gateway-call-id"]]
    usage_rows = [
        row
        for row in accounting.list_ledger(installation.id)
        if row.kind == "ai_usage"
    ]
    assert len(usage_rows) == 1
    assert usage_rows[0].amount_usd == "-0.002990"
    assert accounting.balance(installation.id) >= 0


def test_installation_rate_limit_rejects_excess_calls_before_call_creation(tmp_path: Path) -> None:
    _database, configuration, accounting, _service, provider, installation = make_service(
        tmp_path
    )
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider),
        requests_per_minute=1,
    )

    service.execute(installation, "rate-one", request_payload())
    with pytest.raises(GatewayError) as denied:
        service.execute(installation, "rate-two", request_payload())

    assert denied.value.status_code == 429
    assert denied.value.code == "installation_rate_limited"
    assert denied.value.details["retryAfterSeconds"] >= 1
    assert len(provider.payloads) == 1
    assert len(accounting.list_calls(installation.id)) == 1


def test_disabled_installation_is_rejected_before_call_creation(tmp_path: Path) -> None:
    _database, configuration, accounting, service, provider, installation = make_service(
        tmp_path
    )
    configuration.update_installation(installation.id, enabled=False)
    installation = InstallationPrincipal(installation.id, enabled=False)

    with pytest.raises(GatewayError) as denied:
        service.execute(installation, "disabled", request_payload())

    assert denied.value.status_code == 403
    assert provider.payloads == []
    assert accounting.list_calls(installation.id) == []


def test_meter_only_records_cost_without_credit_or_ledger_debit(tmp_path: Path) -> None:
    _database, configuration, accounting, _plan = configured_stores(
        tmp_path,
        billing_mode="meter_only",
    )
    installation, token = configuration.create_installation(
        "Customer-hosted gateway"
    )
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(RecordingProvider()),
    )

    _response, headers = service.execute(
        configuration.authenticate_principal(token),
        "meter-only-one",
        request_payload(),
    )

    assert headers["x-grapyth-billed-cost-usd"] == "0.002990"
    assert "x-grapyth-budget-remaining-usd" not in headers
    assert accounting.balance(installation.id) == 0
    assert accounting.list_ledger(installation.id) == []
    call = accounting.list_calls(installation.id)[0]
    assert call.provider_cost_usd == "0.002990"
    assert call.charged_usd == "0.002990"
    assert call.margin_usd == "0.000000"


def test_below_cost_pricing_requires_approval_and_is_visible_per_call(tmp_path: Path) -> None:
    database = GatewayDatabase(tmp_path / "gateway.db")
    database.initialize()
    configuration = GatewayConfigurationStore(database)
    accounting = GatewayAccountingStore(database)
    discounted = PricingRates(
        input="1",
        cached="0.1",
        cache_write="1.25",
        output="6",
    )
    with pytest.raises(ValueError, match="explicit"):
        configuration.create_pricing_plan(
            PricingPlanSpec(
                name="Discounted",
                model="gpt-5.6-terra",
                version="discount-v1",
                provider_rates=DEFAULT_RATES,
                billed_rates=discounted,
            )
        )
    with pytest.raises(ValueError, match="reason"):
        configuration.create_pricing_plan(
            PricingPlanSpec(
                name="Discounted",
                model="gpt-5.6-terra",
                version="discount-v1",
                provider_rates=DEFAULT_RATES,
                billed_rates=discounted,
                allow_below_cost=True,
            )
        )
    plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Discounted",
            model="gpt-5.6-terra",
            version="discount-v1",
            provider_rates=DEFAULT_RATES,
            billed_rates=discounted,
            allow_below_cost=True,
            below_cost_reason="Founder-approved pilot",
        )
    )
    configuration.update_defaults(pricing_plan_id=plan.id)
    installation, token = configuration.create_installation("Discount pilot")
    accounting.add_credit(installation.id, Decimal("10"), "pilot")

    GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(RecordingProvider()),
    ).execute(
        configuration.authenticate_principal(token),
        "discount-one",
        request_payload(),
    )

    call = accounting.list_calls(installation.id)[0]
    assert call.provider_cost_usd == "0.002990"
    assert call.charged_usd == "0.001495"
    assert call.margin_usd == "-0.001495"
    assert call.below_cost is True
    assert json.loads(call.provider_rates_json)["output"] == "12.000000"
    assert json.loads(call.billed_rates_json)["output"] == "6.000000"
    usage = configuration.installation(installation.id).usage
    assert usage.provider_cost_usd == Decimal("0.002990")
    assert usage.billed_cost_usd == Decimal("0.001495")
    assert usage.margin_usd == Decimal("-0.001495")
    usage_debit = next(
        row
        for row in accounting.list_ledger(installation.id)
        if row.kind == "ai_usage"
    )
    assert usage_debit.amount_usd == "-0.001495"


def test_each_client_gets_its_assigned_model_reasoning_and_provider(tmp_path: Path) -> None:
    _database, configuration, accounting, default_plan = configured_stores(tmp_path)
    second_plan = configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Second model",
            model="gpt-5.2",
            version="second-v1",
            provider_rates=DEFAULT_RATES,
            billed_rates=DEFAULT_RATES,
        )
    )
    first, first_token = configuration.create_installation("First")
    second, second_token = configuration.create_installation(
        "Second",
        pricing_plan_id=second_plan.id,
        reasoning_effort="high",
    )
    accounting.add_credit(first.id, Decimal("10"), "first")
    accounting.add_credit(second.id, Decimal("10"), "second")
    selected: list[str] = []
    providers: dict[str, RecordingProvider] = {}

    class RecordingSelector:
        def resolve(self, policy: InstallationPolicy) -> RecordingProvider:
            selected.append(policy.installation_id)
            return providers.setdefault(policy.installation_id, RecordingProvider())

    service = GatewayService(configuration, accounting, RecordingSelector())
    service.execute(
        configuration.authenticate_principal(first_token),
        "first-one",
        request_payload(),
    )
    service.execute(
        configuration.authenticate_principal(second_token),
        "second-one",
        request_payload(),
    )

    assert selected == [first.id, second.id]
    assert providers[first.id].payloads[0]["model"] == default_plan.model
    assert providers[first.id].payloads[0]["reasoning"] == {"effort": "low"}
    assert providers[second.id].payloads[0]["model"] == "gpt-5.2"
    assert providers[second.id].payloads[0]["reasoning"] == {"effort": "high"}


def test_each_call_keeps_the_pricing_snapshot_used_for_its_charge(tmp_path: Path) -> None:
    _database, configuration, accounting, plan = configured_stores(tmp_path)
    installation, token = configuration.create_installation("Versioned pricing")
    accounting.add_credit(installation.id, Decimal("10"), "credit")
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(RecordingProvider()),
    )

    service.execute(
        configuration.authenticate_principal(token),
        "price-v1",
        request_payload(),
    )
    doubled = PricingRates(
        input=DEFAULT_RATES.input * 2,
        cached=DEFAULT_RATES.cached * 2,
        cache_write=DEFAULT_RATES.cache_write * 2,
        output=DEFAULT_RATES.output * 2,
    )
    configuration.update_pricing_plan(
        plan.id,
        PricingPlanChanges(
            version="pricing-v2",
            provider_rates=DEFAULT_RATES,
            billed_rates=doubled,
        ),
    )
    service.execute(
        configuration.authenticate_principal(token),
        "price-v2",
        request_payload(),
    )

    calls = {
        call.idempotency_key: call
        for call in accounting.list_calls(installation.id)
    }
    assert calls["price-v1"].pricing_version == DEFAULT_PRICING_VERSION
    assert calls["price-v1"].charged_usd == "0.002990"
    assert json.loads(calls["price-v1"].billed_rates_json)["output"] == "12.000000"
    assert calls["price-v2"].pricing_version == "pricing-v2"
    assert calls["price-v2"].charged_usd == "0.005980"
    assert json.loads(calls["price-v2"].billed_rates_json)["output"] == "24.000000"


def test_upstream_provider_receives_gateway_call_as_idempotency_key() -> None:
    received = {}

    class Raw:
        headers = {}

        @staticmethod
        def parse():
            return type("Response", (), {"model_dump": lambda self, mode: {"id": "response"}})()

    class RawApi:
        @staticmethod
        def create(**kwargs):
            received.update(kwargs)
            return Raw()

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = type(
        "Client", (), {"responses": type("Responses", (), {"with_raw_response": RawApi()})()}
    )()

    provider.invoke(
        ProviderRequest(
            payload={"model": "gpt-5.6-terra", "input": []},
            gateway_call_id="gw-call-idempotent",
            fallback_model="gpt-5.6-terra",
        )
    )

    assert received["extra_headers"] == {"Idempotency-Key": "gw-call-idempotent"}
    assert "_grapyth_gateway_call_id" not in received


def test_openai_adapter_normalizes_the_typed_provider_port_result() -> None:
    received = {}

    class Raw:
        headers = {"x-request-id": "provider-typed"}

        @staticmethod
        def parse():
            return type(
                "Response",
                (),
                {
                    "model_dump": lambda self, mode: {
                        "id": "response-typed",
                        "model": "gpt-5.6-terra",
                        "usage": {"input_tokens": 12, "output_tokens": 3},
                    }
                },
            )()

    class RawApi:
        @staticmethod
        def create(**kwargs):
            received.update(kwargs)
            return Raw()

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = type(
        "Client", (), {"responses": type("Responses", (), {"with_raw_response": RawApi()})()}
    )()

    result = provider.invoke(
        ProviderRequest(
            payload={"model": "gpt-5.6-terra", "input": []},
            gateway_call_id="gw-call-typed",
            fallback_model="fallback",
        )
    )

    assert result.provider_request_id == "provider-typed"
    assert result.resolved_model == "gpt-5.6-terra"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert received["extra_headers"] == {"Idempotency-Key": "gw-call-typed"}


def test_openai_adapter_counts_the_normalized_input_without_generation_fields() -> None:
    received = {}

    class InputTokens:
        @staticmethod
        def count(**kwargs):
            received.update(kwargs)
            return type("Count", (), {"input_tokens": 321})()

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = type(
        "Client",
        (),
        {"responses": type("Responses", (), {"input_tokens": InputTokens()})()},
    )()

    count = provider.count_input_tokens(
        ProviderRequest(
            payload={
                "model": "gpt-5.6-terra",
                "input": [{"role": "user", "content": "private"}],
                "instructions": "Be concise",
                "reasoning": {"effort": "low"},
                "text": {"format": {"type": "text"}},
                "tools": [
                    {
                        "type": "function",
                        "name": "sample_database_query",
                        "description": "Return a bounded sample",
                        "parameters": {
                            "type": "object",
                            "properties": {"sql": {"type": "string"}},
                            "required": ["sql"],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    }
                ],
                "max_output_tokens": 100,
                "prompt_cache_key": "cache-key",
                "prompt_cache_options": {"retention": "24h"},
                "store": False,
            },
            gateway_call_id="",
            fallback_model="gpt-5.6-terra",
        )
    )

    assert count == 321
    assert received == {
        "model": "gpt-5.6-terra",
        "input": [{"role": "user", "content": "private"}],
        "instructions": "Be concise",
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "text"}},
        "tools": [
            {
                "type": "function",
                "name": "sample_database_query",
                "description": "Return a bounded sample",
                "parameters": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ],
    }


def test_openai_adapter_translates_rate_limit_errors_without_exposing_the_body() -> None:
    sensitive_message = "Provider detail with private request content"
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        429,
        request=request,
        headers={"x-request-id": "req-openai-credit", "retry-after": "4.2"},
    )
    provider_error = RateLimitError(
        sensitive_message,
        response=response,
        body={
            "message": sensitive_message,
            "type": "insufficient_quota",
            "code": "credit_balance_exhausted",
        },
    )

    class RawApi:
        @staticmethod
        def create(**_kwargs):
            raise provider_error

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = type(
        "Client",
        (),
        {"responses": type("Responses", (), {"with_raw_response": RawApi()})()},
    )()

    with pytest.raises(ProviderRequestError) as translated:
        provider.invoke(
            ProviderRequest(
                payload={"model": "gpt-5.6-terra", "input": []},
                gateway_call_id="gw-call-provider-error",
                fallback_model="gpt-5.6-terra",
            )
        )

    assert str(translated.value) == "The AI provider request failed"
    assert translated.value.error_code == "credit_balance_exhausted"
    assert translated.value.error_type == "insufficient_quota"
    assert translated.value.status_code == 429
    assert translated.value.request_id == "req-openai-credit"
    assert translated.value.retry_after_seconds == 5
    assert sensitive_message not in str(translated.value)


def test_provider_request_error_rejects_unsafe_diagnostics() -> None:
    error = ProviderRequestError(
        error_code="unsafe\nprivate payload",
        error_type="unsafe/type",
        status_code=True,
        request_id="https://private.example/request",
        retry_after_seconds=MAX_RETRY_AFTER_SECONDS + 1,
    )

    assert str(error) == "The AI provider request failed"
    assert error.error_code == "ProviderError"
    assert error.error_type == ""
    assert error.status_code is None
    assert error.request_id == ""
    assert error.retry_after_seconds is None


@pytest.mark.parametrize(
    ("header_value", "expected_seconds"),
    [
        ("", None),
        ("4", 4),
        ("4.2", 5),
        ("0", None),
        ("nan", None),
        (str(MAX_RETRY_AFTER_SECONDS + 1), MAX_RETRY_AFTER_SECONDS + 1),
    ],
)
def test_openai_retry_after_parser_accepts_positive_delta_seconds(
    header_value: str,
    expected_seconds: int | None,
) -> None:
    assert OpenAIProvider._parse_retry_after_seconds(header_value) == expected_seconds


def test_provider_connection_check_disables_sdk_retries_and_retrieves_selected_model() -> None:
    received = {}

    class Models:
        @staticmethod
        def retrieve(model):
            received["model"] = model
            return type("Model", (), {"id": model})()

    class DiagnosticClient:
        models = Models()

    class Client:
        @staticmethod
        def with_options(**options):
            received["options"] = options
            return DiagnosticClient()

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = Client()

    assert provider.check_model("gpt-5.6-terra") == "gpt-5.6-terra"
    assert received == {
        "model": "gpt-5.6-terra",
        "options": {"max_retries": 0, "timeout": 10.0},
    }


def test_openai_provider_uses_the_sdk_default_transport(monkeypatch) -> None:
    import openai

    received = {}

    class Client:
        def __init__(self, **options):
            received["openai"] = options

    monkeypatch.setattr(openai, "OpenAI", Client)

    provider = OpenAIProvider(
        "sk-test",
        timeout_seconds=30,
        max_retries=2,
    )

    assert isinstance(provider.client, Client)
    assert not hasattr(provider, "api_key")
    assert received["openai"] == {
        "api_key": "sk-test",
        "timeout": 30,
        "max_retries": 2,
    }
