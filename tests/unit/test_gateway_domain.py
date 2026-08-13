from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from gateway.domain import (
    CallFailure,
    CallCompletion,
    InstallationPolicy,
    PricingRates,
    PricingSnapshot,
    ProviderResult,
    ProviderUsage,
    TraceContext,
    format_money,
    normalize_provider_request,
    pricing_is_below_cost,
    usage_cost,
    validate_below_cost_pricing,
    worst_case_cost,
)
from gateway.errors import DomainValidationError


RATES = PricingRates(
    input=Decimal("4"),
    cached=Decimal("1"),
    cache_write=Decimal("2"),
    output=Decimal("8"),
)


def test_domain_and_error_modules_do_not_import_infrastructure_frameworks() -> None:
    gateway_root = Path(__file__).resolve().parents[2]
    forbidden = {"fastapi", "openai", "os", "pathlib", "sqlite3"}

    for filename in ("domain.py", "errors.py"):
        tree = ast.parse((gateway_root / filename).read_text(encoding="utf-8"))
        imported = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            str(node.module).partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        )
        assert imported.isdisjoint(forbidden)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0.000000"),
        ("1.2345674", "1.234567"),
        ("1.2345675", "1.234568"),
        (Decimal("-0.0000005"), "-0.000001"),
    ],
)
def test_money_format_is_centralized_and_uses_six_decimal_half_up_rounding(value, expected) -> None:
    assert format_money(value) == expected


def test_pricing_rates_validate_bounds_and_use_explicit_payload_conversion() -> None:
    rates = PricingRates.from_payload({
        "input": "2.5",
        "cached": "0.25",
        "cacheWrite": "3.125",
        "output": "15",
    })

    assert rates.to_payload() == {
        "input": "2.500000",
        "cached": "0.250000",
        "cacheWrite": "3.125000",
        "output": "15.000000",
    }
    with pytest.raises(DomainValidationError, match="rates.output must be a number"):
        PricingRates.from_payload({
            "input": "2.5", "cached": "0.25", "cacheWrite": "3.125", "output": None
        })
    with pytest.raises(DomainValidationError, match="outside the supported range"):
        PricingRates.from_payload({
            "input": "-1", "cached": "0.25", "cacheWrite": "3.125", "output": "15"
        })


def test_below_cost_pricing_requires_explicit_approval_and_reason() -> None:
    discounted = PricingRates(
        input=Decimal("3"),
        cached=Decimal("1"),
        cache_write=Decimal("2"),
        output=Decimal("8"),
    )

    assert pricing_is_below_cost(RATES, discounted) is True
    with pytest.raises(DomainValidationError, match="explicit"):
        validate_below_cost_pricing(
            RATES, discounted, allow_below_cost=False, reason=""
        )
    with pytest.raises(DomainValidationError, match="reason"):
        validate_below_cost_pricing(
            RATES, discounted, allow_below_cost=True, reason=""
        )
    assert validate_below_cost_pricing(
        RATES, discounted, allow_below_cost=True, reason="Approved pilot"
    ) == (True, "Approved pilot")
    assert validate_below_cost_pricing(
        RATES, RATES, allow_below_cost=True, reason="Not applicable"
    ) == (False, "")


def test_request_normalization_forces_gateway_policy_and_provider_non_storage() -> None:
    normalized = normalize_provider_request(
        {
            "model": "client-selected",
            "reasoning": {"effort": "high"},
            "store": True,
            "service_tier": "priority",
            "input": [{"role": "user", "content": "private"}],
            "instructions": "answer briefly",
            "max_output_tokens": 5000,
        },
        model="gateway-selected",
        reasoning_effort="low",
        max_output_tokens_limit=1024,
    )

    assert normalized == {
        "input": [{"role": "user", "content": "private"}],
        "instructions": "answer briefly",
        "max_output_tokens": 1024,
        "model": "gateway-selected",
        "reasoning": {"effort": "low"},
        "service_tier": "default",
        "store": False,
    }
    assert worst_case_cost(123, normalized["max_output_tokens"], RATES) == Decimal(
        "0.008684"
    )


def test_request_normalization_allows_only_strict_serial_function_tools() -> None:
    tool = {
        "type": "function",
        "name": "sample_database_query",
        "description": "Return a bounded database sample",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    }

    normalized = normalize_provider_request(
        {
            "input": [{"role": "user", "content": "Inspect values"}],
            "max_output_tokens": 100,
            "tools": [tool],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        },
        model="gateway-selected",
        reasoning_effort="low",
        max_output_tokens_limit=1024,
    )

    assert normalized["tools"] == [tool]
    assert normalized["tool_choice"] == "auto"
    assert normalized["parallel_tool_calls"] is False

    for invalid in (
        {"tools": [tool], "tool_choice": "auto", "parallel_tool_calls": True},
        {
            "tools": [{**tool, "strict": False}],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        },
        {
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        },
    ):
        with pytest.raises(DomainValidationError):
            normalize_provider_request(
                {
                    "input": [{"role": "user", "content": "Inspect values"}],
                    "max_output_tokens": 100,
                    **invalid,
                },
                model="gateway-selected",
                reasoning_effort="low",
                max_output_tokens_limit=1024,
            )


@pytest.mark.parametrize(
    ("payload", "code", "message"),
    [
        ([], "invalid_request", "Expected a JSON object"),
        ({}, "invalid_request", "Responses input is required"),
        (
            {"input": ["x"], "max_output_tokens": "invalid"},
            "invalid_request",
            "max_output_tokens must be an integer",
        ),
        (
            {"input": ["x"], "max_output_tokens": 0},
            "output_limit_exceeded",
            "max_output_tokens must be at least 1",
        ),
    ],
)
def test_request_validation_has_stable_layer_neutral_error_codes(payload, code, message) -> None:
    with pytest.raises(DomainValidationError) as failure:
        normalize_provider_request(
            payload,
            model="gateway-selected",
            reasoning_effort="low",
            max_output_tokens_limit=1024,
        )

    assert failure.value.code == code
    assert str(failure.value) == message


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (
            ProviderUsage(
                input_tokens=100,
                cached_input_tokens=20,
                cache_write_tokens=30,
                output_tokens=10,
            ),
            Decimal("0.000360"),
        ),
        (
            ProviderUsage(input_tokens=10, cached_input_tokens=20, cache_write_tokens=20),
            Decimal("0.000010"),
        ),
        (
            ProviderUsage(input_tokens=-10, cached_input_tokens=-3, output_tokens=-5),
            Decimal("0.000000"),
        ),
    ],
)
def test_usage_cost_is_non_negative_and_partitions_cached_tokens(usage, expected) -> None:
    assert usage_cost(RATES, usage) == expected


def test_worst_case_cost_uses_the_highest_possible_input_rate() -> None:
    rates = PricingRates(
        input="2.5",
        cached="0.25",
        cache_write="3.125",
        output="15",
    )

    assert worst_case_cost(1_000_000, 0, rates) == Decimal("3.125000")


def test_provider_usage_and_result_are_typed_without_exposing_ai_body_in_repr() -> None:
    response = {
        "id": "provider-response",
        "model": "resolved-model",
        "output_text": "confidential output",
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 30},
            "output_tokens": 10,
            "output_tokens_details": {"reasoning_tokens": 4},
            "total_tokens": 110,
        },
    }
    result = ProviderResult.from_response(
        response, {"x-request-id": "provider-request"}, fallback_model="fallback"
    )

    assert result.usage == ProviderUsage(100, 20, 30, 10, 4, 110)
    assert result.provider_request_id == "provider-request"
    assert result.resolved_model == "resolved-model"
    assert "confidential output" not in repr(result)


def test_policy_trace_and_completion_contracts_are_immutable_and_snake_case() -> None:
    policy = InstallationPolicy(
        installation_id="inst-one",
        enabled=True,
        provider_credential_id="provider-one",
        model="gpt-5.6-terra",
        reasoning_effort="low",
        billing_mode="prepaid",
        pricing=PricingSnapshot("plan-one", "v1", RATES, RATES, False),
    )
    trace = TraceContext(correlation_id="c" * 128, end_user_id="user-one")
    completion = CallCompletion(
        call_id="call-one",
        status="ok",
        resolved_model=policy.model,
        reasoning_effort=policy.reasoning_effort,
        pricing=PricingSnapshot(
            "plan-one", "v1", RATES, RATES, False
        ),
        charged_usd=Decimal("0.1"),
    )

    assert policy.installation_id == "inst-one"
    assert len(trace.correlation_id) == 128
    assert trace.end_user_id == "user-one"
    assert completion.charged_usd == Decimal("0.1")
    with pytest.raises(FrozenInstanceError):
        policy.model = "changed"  # type: ignore[misc]


def test_stored_call_failure_parser_accepts_only_bounded_diagnostics() -> None:
    failure = CallFailure.from_payload(
        {
            "phase": "counting_tokens",
            "kind": "upstream_rejection",
            "provider": {
                "code": "unknown_parameter",
                "type": "invalid_request_error",
                "statusCode": 400,
                "param": "input[4].status",
            },
        }
    )

    assert failure.to_payload()["provider"]["param"] == "input[4].status"
    with pytest.raises(ValueError, match="Stored provider diagnostic"):
        CallFailure.from_payload(
            {
                "phase": "counting_tokens",
                "kind": "upstream_rejection",
                "provider": {"param": "private parameter value with spaces"},
            }
        )


@pytest.mark.parametrize("provider", [None, [], 0, False, ""])
def test_stored_call_failure_parser_rejects_non_object_provider(provider: object) -> None:
    with pytest.raises(ValueError, match="Stored call failure diagnostics"):
        CallFailure.from_payload(
            {
                "phase": "counting_tokens",
                "kind": "upstream_rejection",
                "provider": provider,
            }
        )


def test_stored_call_failure_parser_rejects_non_string_phase() -> None:
    with pytest.raises(ValueError, match="Stored call failure diagnostics"):
        CallFailure.from_payload(
            {
                "phase": [],
                "kind": "upstream_rejection",
            }
        )
