from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from ..domain import (
    BillingMode,
    CallStatus,
    InstallationPolicyAssignment,
    PricingPlanSpec,
    PricingRates,
    ReasoningEffort,
    format_money,
    pricing_is_below_cost,
)


@dataclass(frozen=True, slots=True)
class ProviderCredentialReadModel:
    id: str
    name: str
    key_hint: str
    enabled: bool
    created_at: str
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "keyHint": self.key_hint,
            "enabled": self.enabled,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class PricingPlanReadModel:
    id: str
    name: str
    model: str
    version: str
    provider_rates: PricingRates
    billed_rates: PricingRates
    allow_below_cost: bool
    below_cost_reason: str
    enabled: bool
    created_at: str
    updated_at: str

    @property
    def below_cost(self) -> bool:
        return pricing_is_below_cost(self.provider_rates, self.billed_rates)

    @property
    def spec(self) -> PricingPlanSpec:
        return PricingPlanSpec(
            name=self.name,
            model=self.model,
            version=self.version,
            provider_rates=self.provider_rates,
            billed_rates=self.billed_rates,
            allow_below_cost=self.allow_below_cost,
            below_cost_reason=self.below_cost_reason,
            enabled=self.enabled,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "version": self.version,
            "providerRates": self.provider_rates.to_payload(),
            "billedRates": self.billed_rates.to_payload(),
            "allowBelowCost": self.allow_below_cost,
            "belowCostReason": self.below_cost_reason,
            "belowCost": self.below_cost,
            "enabled": self.enabled,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GatewaySettingsReadModel:
    max_output_tokens: int
    max_request_bytes: int
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "maxOutputTokens": self.max_output_tokens,
            "maxRequestBytes": self.max_request_bytes,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GatewayDefaultsReadModel:
    provider_credential_id: str
    pricing_plan_id: str
    reasoning_effort: ReasoningEffort
    billing_mode: BillingMode
    updated_at: str

    @property
    def policy(self) -> InstallationPolicyAssignment:
        return InstallationPolicyAssignment(
            provider_credential_id=self.provider_credential_id,
            pricing_plan_id=self.pricing_plan_id,
            reasoning_effort=self.reasoning_effort,
            billing_mode=self.billing_mode,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "providerCredentialId": self.provider_credential_id,
            "pricingPlanId": self.pricing_plan_id,
            "reasoningEffort": self.reasoning_effort,
            "billingMode": self.billing_mode,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class InstallationUsageReadModel:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    below_cost_calls: int = 0
    provider_cost_usd: Decimal = Decimal("0")
    billed_cost_usd: Decimal = Decimal("0")
    margin_usd: Decimal = Decimal("0")

    def to_payload(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "below_cost_calls": self.below_cost_calls,
            "provider_cost_usd": format_money(self.provider_cost_usd),
            "billed_cost_usd": format_money(self.billed_cost_usd),
            "margin_usd": format_money(self.margin_usd),
        }


@dataclass(frozen=True, slots=True)
class InstallationSummaryReadModel:
    id: str
    name: str
    token_hint: str
    enabled: bool
    note: str
    provider_credential_id: str
    pricing_plan_id: str
    model: str
    reasoning_effort: ReasoningEffort
    billing_mode: BillingMode
    created_at: str
    updated_at: str
    balance_usd: Decimal = Decimal("0")
    usage: InstallationUsageReadModel = InstallationUsageReadModel()

    @property
    def policy(self) -> InstallationPolicyAssignment:
        return InstallationPolicyAssignment(
            provider_credential_id=self.provider_credential_id,
            pricing_plan_id=self.pricing_plan_id,
            reasoning_effort=self.reasoning_effort,
            billing_mode=self.billing_mode,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tokenHint": self.token_hint,
            "enabled": self.enabled,
            "note": self.note,
            "providerCredentialId": self.provider_credential_id,
            "pricingPlanId": self.pricing_plan_id,
            "model": self.model,
            "reasoningEffort": self.reasoning_effort,
            "billingMode": self.billing_mode,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "balanceUsd": format_money(self.balance_usd),
            "usage": self.usage.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class CallReadModel:
    id: str
    installation_id: str
    idempotency_key: str
    user_id: str
    board_id: str
    chat_id: str
    app_ai_call_id: str
    status: CallStatus
    requested_model: str
    resolved_model: str
    reasoning_effort: ReasoningEffort
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    provider_request_id: str
    estimated_provider_cost_usd: str | None
    provider_cost_usd: str | None
    charged_usd: str | None
    margin_usd: str | None
    pricing_plan_id: str
    pricing_version: str
    provider_rates_json: str
    billed_rates_json: str
    below_cost: bool
    duration_ms: float
    error_code: str
    created_at: str
    completed_at: str | None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LedgerReadModel:
    id: str
    amount_usd: str
    kind: str
    call_id: str | None
    note: str
    created_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "amount_usd": self.amount_usd,
            "kind": self.kind,
            "call_id": self.call_id,
            "note": self.note,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class UserUsageReadModel:
    user_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    spent_usd: Decimal
    monthly_limit_usd: str | None
    updated_at: str | None

    def to_payload(self) -> dict[str, Any]:
        limit = Decimal(self.monthly_limit_usd) if self.monthly_limit_usd is not None else None
        remaining = max(Decimal("0"), limit - self.spent_usd) if limit is not None else None
        return {
            "userId": self.user_id,
            "calls": self.calls,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "spentUsd": format_money(self.spent_usd),
            "monthlyLimitUsd": self.monthly_limit_usd,
            "updatedAt": self.updated_at,
            "remainingUsd": format_money(remaining) if remaining is not None else None,
            "limitReached": limit is not None and self.spent_usd >= limit,
        }


@dataclass(frozen=True, slots=True)
class AuditReadModel:
    id: str
    action: str
    target_type: str
    target_id: str
    details_json: str
    details: dict[str, Any]
    created_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "details_json": self.details_json,
            "created_at": self.created_at,
            "details": self.details,
        }
