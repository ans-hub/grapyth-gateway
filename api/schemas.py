from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class BoundaryModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class InstallationCreateRequest(BoundaryModel):
    name: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=1000)
    provider_credential_id: str = Field(alias="providerCredentialId")
    pricing_plan_id: str = Field(alias="pricingPlanId", min_length=1)
    reasoning_effort: Literal["low", "medium", "high"] = Field(alias="reasoningEffort")
    billing_mode: Literal["prepaid", "meter_only"] = Field(alias="billingMode")


class InstallationUpdateRequest(BoundaryModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)
    provider_credential_id: str | None = Field(default=None, alias="providerCredentialId")
    pricing_plan_id: str | None = Field(default=None, alias="pricingPlanId", min_length=1)
    reasoning_effort: Literal["low", "medium", "high"] | None = Field(
        default=None, alias="reasoningEffort"
    )
    billing_mode: Literal["prepaid", "meter_only"] | None = Field(
        default=None, alias="billingMode"
    )


class CreditAdjustmentRequest(BoundaryModel):
    amount_usd: Decimal = Field(alias="amountUsd", ge=Decimal("-1000000"), le=Decimal("1000000"))
    note: str = Field(default="", max_length=1000)


class ProviderCredentialCreateRequest(BoundaryModel):
    name: str = Field(min_length=1, max_length=160)
    api_key: SecretStr = Field(alias="apiKey", min_length=1, max_length=4096)


class ProviderCredentialUpdateRequest(BoundaryModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None
    api_key: SecretStr | None = Field(default=None, alias="apiKey", min_length=1, max_length=4096)


class ProviderTestRequest(BoundaryModel):
    model: str = Field(min_length=1, max_length=160)


class PricingRatesRequest(BoundaryModel):
    input: Decimal = Field(ge=0, le=Decimal("1000000"))
    cached: Decimal = Field(ge=0, le=Decimal("1000000"))
    cache_write: Decimal = Field(alias="cacheWrite", ge=0, le=Decimal("1000000"))
    output: Decimal = Field(ge=0, le=Decimal("1000000"))


class PricingPlanCreateRequest(BoundaryModel):
    name: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=160)
    provider_rates: PricingRatesRequest = Field(alias="providerRates")
    billed_rates: PricingRatesRequest = Field(alias="billedRates")
    allow_below_cost: bool = Field(default=False, alias="allowBelowCost")
    below_cost_reason: str = Field(default="", alias="belowCostReason", max_length=1000)
    enabled: bool = True


class PricingPlanUpdateRequest(BoundaryModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    version: str | None = Field(default=None, min_length=1, max_length=160)
    provider_rates: PricingRatesRequest | None = Field(default=None, alias="providerRates")
    billed_rates: PricingRatesRequest | None = Field(default=None, alias="billedRates")
    allow_below_cost: bool | None = Field(default=None, alias="allowBelowCost")
    below_cost_reason: str | None = Field(default=None, alias="belowCostReason", max_length=1000)
    enabled: bool | None = None


class GatewaySettingsUpdateRequest(BoundaryModel):
    max_output_tokens: int | None = Field(
        default=None, alias="maxOutputTokens", ge=1, le=128_000
    )
    max_request_bytes: int | None = Field(
        default=None, alias="maxRequestBytes", ge=1024, le=100 * 1024 * 1024
    )


class UserLimitRequest(BoundaryModel):
    monthly_limit_usd: Decimal | None = Field(
        default=None,
        alias="monthlyLimitUsd",
        ge=0,
        le=Decimal("1000000"),
    )


ModelT = TypeVar("ModelT", bound=BoundaryModel)


def parse_model(model: type[ModelT], payload: dict[str, Any]) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors(include_input=False)[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = str(first.get("msg") or "Invalid request")
        detail = f"{location}: {message}" if location else message
        raise HTTPException(400, detail) from exc
