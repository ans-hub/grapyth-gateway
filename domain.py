from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal, cast

from .errors import DomainValidationError


MONEY_QUANTUM = Decimal("0.000001")
# OpenAI uses a separate long-context pricing tier above this input size.
MAX_STANDARD_PRICING_INPUT_TOKENS = 272_000
ReasoningEffort = Literal["low", "medium", "high"]
BillingMode = Literal["prepaid", "meter_only"]
CallStatus = Literal["running", "ok", "error"]


def quantize_money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def format_money(value: Decimal | str | int | float) -> str:
    return format(quantize_money(value), "f")


def parse_rate(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DomainValidationError(f"{label} must be a number") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal("1000000"):
        raise DomainValidationError(f"{label} is outside the supported range")
    return quantize_money(parsed)


@dataclass(frozen=True, slots=True)
class PricingRates:
    input: Decimal
    cached: Decimal
    cache_write: Decimal
    output: Decimal

    def __post_init__(self) -> None:
        for name in ("input", "cached", "cache_write", "output"):
            object.__setattr__(self, name, parse_rate(getattr(self, name), f"rates.{name}"))

    @classmethod
    def from_payload(cls, value: Mapping[str, Any], *, label: str = "rates") -> "PricingRates":
        if not isinstance(value, Mapping):
            raise DomainValidationError(f"{label} must be an object")
        return cls(
            input=parse_rate(value.get("input"), f"{label}.input"),
            cached=parse_rate(value.get("cached"), f"{label}.cached"),
            cache_write=parse_rate(value.get("cacheWrite"), f"{label}.cacheWrite"),
            output=parse_rate(value.get("output"), f"{label}.output"),
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "input": format_money(self.input),
            "cached": format_money(self.cached),
            "cacheWrite": format_money(self.cache_write),
            "output": format_money(self.output),
        }


def pricing_is_below_cost(provider_rates: PricingRates, billed_rates: PricingRates) -> bool:
    return any(
        billed < provider
        for provider, billed in (
            (provider_rates.input, billed_rates.input),
            (provider_rates.cached, billed_rates.cached),
            (provider_rates.cache_write, billed_rates.cache_write),
            (provider_rates.output, billed_rates.output),
        )
    )


def validate_below_cost_pricing(
    provider_rates: PricingRates,
    billed_rates: PricingRates,
    *,
    allow_below_cost: bool,
    reason: str,
) -> tuple[bool, str]:
    below_cost = pricing_is_below_cost(provider_rates, billed_rates)
    if below_cost and not allow_below_cost:
        raise DomainValidationError(
            "Billed rates below provider cost require explicit allowBelowCost approval"
        )
    if below_cost and not reason:
        raise DomainValidationError("A below-cost pricing reason is required")
    return below_cost, reason if below_cost else ""


@dataclass(frozen=True, slots=True)
class PricingPlanSpec:
    name: str
    model: str
    version: str
    provider_rates: PricingRates
    billed_rates: PricingRates
    allow_below_cost: bool = False
    below_cost_reason: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()[:160]
        model = str(self.model or "").strip()[:160]
        version = str(self.version or "").strip()[:160]
        if not name or not model or not version:
            raise DomainValidationError(
                "Pricing plan name, model, and version are required"
            )
        _, reason = validate_below_cost_pricing(
            self.provider_rates,
            self.billed_rates,
            allow_below_cost=self.allow_below_cost,
            reason=str(self.below_cost_reason or "").strip()[:1000],
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "below_cost_reason", reason)

    @property
    def below_cost(self) -> bool:
        return pricing_is_below_cost(self.provider_rates, self.billed_rates)


@dataclass(frozen=True, slots=True)
class PricingPlanChanges:
    name: str | None = None
    model: str | None = None
    version: str | None = None
    provider_rates: PricingRates | None = None
    billed_rates: PricingRates | None = None
    allow_below_cost: bool | None = None
    below_cost_reason: str | None = None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    plan_id: str
    version: str
    provider_rates: PricingRates
    billed_rates: PricingRates
    below_cost: bool


@dataclass(frozen=True, slots=True)
class InstallationPrincipal:
    id: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class InstallationPolicyAssignment:
    provider_credential_id: str
    pricing_plan_id: str
    reasoning_effort: ReasoningEffort
    billing_mode: BillingMode

    def __post_init__(self) -> None:
        reasoning_effort = str(self.reasoning_effort).lower()
        billing_mode = str(self.billing_mode).lower()
        if reasoning_effort not in {"low", "medium", "high"}:
            raise DomainValidationError(
                "reasoningEffort must be low, medium, or high"
            )
        if billing_mode not in {"prepaid", "meter_only"}:
            raise DomainValidationError("billingMode must be prepaid or meter_only")
        object.__setattr__(
            self,
            "provider_credential_id",
            str(self.provider_credential_id or "").strip(),
        )
        object.__setattr__(
            self,
            "pricing_plan_id",
            str(self.pricing_plan_id or "").strip(),
        )
        object.__setattr__(
            self,
            "reasoning_effort",
            cast(ReasoningEffort, reasoning_effort),
        )
        object.__setattr__(self, "billing_mode", cast(BillingMode, billing_mode))


@dataclass(frozen=True, slots=True)
class InstallationPolicyChanges:
    provider_credential_id: str | None = None
    pricing_plan_id: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    billing_mode: BillingMode | None = None


@dataclass(frozen=True, slots=True)
class InstallationPolicy:
    installation_id: str
    enabled: bool
    provider_credential_id: str
    model: str
    reasoning_effort: ReasoningEffort
    billing_mode: BillingMode
    pricing: PricingSnapshot


@dataclass(frozen=True, slots=True)
class GatewayLimits:
    max_output_tokens: int
    max_request_bytes: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class UserBudget:
    user_id: str
    monthly_limit_usd: Decimal | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> "ProviderUsage":
        usage = response.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        input_details = usage.get("input_tokens_details")
        input_details = input_details if isinstance(input_details, Mapping) else {}
        output_details = usage.get("output_tokens_details")
        output_details = output_details if isinstance(output_details, Mapping) else {}
        return cls(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            cache_write_tokens=int(input_details.get("cache_write_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
        )

    def to_payload(self) -> dict[str, int]:
        return {
            "inputTokens": self.input_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "totalTokens": self.total_tokens,
        }


@dataclass(frozen=True, slots=True)
class ProviderResult:
    response: dict[str, Any] = field(repr=False)
    usage: ProviderUsage
    provider_request_id: str
    resolved_model: str

    @classmethod
    def from_response(
        cls, response: Mapping[str, Any], headers: Mapping[str, str], *, fallback_model: str
    ) -> "ProviderResult":
        body = dict(response)
        return cls(
            response=body,
            usage=ProviderUsage.from_response(body),
            provider_request_id=str(headers.get("x-request-id") or body.get("id") or ""),
            resolved_model=str(body.get("model") or fallback_model),
        )


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    payload: Mapping[str, Any] = field(repr=False)
    gateway_call_id: str
    fallback_model: str


@dataclass(frozen=True, slots=True)
class TraceContext:
    request_id: str = ""
    correlation_id: str = ""
    app_request_id: str = ""
    app_ai_call_id: str = ""
    end_user_id: str = ""
    board_id: str = ""
    chat_id: str = ""

    def to_log_fields(self) -> dict[str, str]:
        return {
            "requestId": self.request_id,
            "correlationId": self.correlation_id,
            "appRequestId": self.app_request_id,
            "appAiCallId": self.app_ai_call_id,
            "endUserId": self.end_user_id,
            "boardId": self.board_id,
            "chatId": self.chat_id,
        }


@dataclass(frozen=True, slots=True)
class CallStart:
    installation_id: str
    idempotency_key: str
    requested_model: str
    user_id: str = ""
    board_id: str = ""
    chat_id: str = ""
    app_ai_call_id: str = ""


@dataclass(frozen=True, slots=True)
class CallCompletion:
    call_id: str
    status: Literal["ok", "error"]
    resolved_model: str
    reasoning_effort: ReasoningEffort
    pricing: PricingSnapshot
    usage: ProviderUsage = ProviderUsage()
    provider_request_id: str = ""
    provider_cost_usd: Decimal | None = None
    charged_usd: Decimal | None = None
    margin_usd: Decimal | None = None
    below_cost: bool = False
    duration_ms: float = 0
    error_code: str = ""
    record_ledger: bool = False


@dataclass(frozen=True, slots=True)
class CallRecord:
    id: str
    status: CallStatus
    resolved_model: str
    input_tokens: int
    output_tokens: int
    provider_request_id: str
    provider_cost_usd: str | None
    charged_usd: str | None
    margin_usd: str | None
    below_cost: bool
    duration_ms: float
    error_code: str


def normalize_provider_request(
    payload: Any,
    *,
    model: str,
    reasoning_effort: ReasoningEffort,
    max_output_tokens_limit: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DomainValidationError("Expected a JSON object")
    if not isinstance(payload.get("input"), list) or not payload["input"]:
        raise DomainValidationError("Responses input is required")
    try:
        max_output = int(payload.get("max_output_tokens") or 0)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("max_output_tokens must be an integer") from exc
    if max_output < 1:
        raise DomainValidationError(
            "max_output_tokens must be at least 1", code="output_limit_exceeded"
        )
    allowed_fields = {
        "input",
        "instructions",
        "max_output_tokens",
        "prompt_cache_key",
        "prompt_cache_options",
        "text",
    }
    normalized = {key: value for key, value in payload.items() if key in allowed_fields}
    normalized["model"] = model
    normalized["reasoning"] = {"effort": reasoning_effort}
    normalized["max_output_tokens"] = min(max_output, max_output_tokens_limit)
    normalized["service_tier"] = "default"
    normalized["store"] = False
    return normalized


def worst_case_cost(
    input_tokens: int,
    max_output_tokens: int,
    rates: PricingRates,
) -> Decimal:
    input_rate = max(rates.input, rates.cached, rates.cache_write)
    value = (
        Decimal(max(0, int(input_tokens))) * input_rate
        + Decimal(max(0, int(max_output_tokens))) * rates.output
    ) / Decimal(1_000_000)
    return quantize_money(value)


def usage_cost(rates: PricingRates, usage: ProviderUsage) -> Decimal:
    input_tokens = max(0, usage.input_tokens)
    cached = min(max(0, usage.cached_input_tokens), input_tokens)
    cache_write = min(max(0, usage.cache_write_tokens), input_tokens - cached)
    ordinary = input_tokens - cached - cache_write
    output = max(0, usage.output_tokens)
    value = (
        Decimal(ordinary) * rates.input
        + Decimal(cached) * rates.cached
        + Decimal(cache_write) * rates.cache_write
        + Decimal(output) * rates.output
    ) / Decimal(1_000_000)
    return quantize_money(value)
