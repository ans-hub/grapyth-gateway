from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from ..domain import (
    CallCompletion,
    CallRecord,
    CallStart,
    GatewayLimits,
    InstallationPolicy,
    InstallationPrincipal,
    MAX_STANDARD_PRICING_INPUT_TOKENS,
    ProviderRequest,
    TraceContext,
    UserBudget,
    format_money,
    normalize_provider_request,
    quantize_money,
    usage_cost,
    worst_case_cost,
)
from ..errors import DomainValidationError, GatewayError, IdempotencyConflict
from ..observability import emit_structured_event
from ..providers.base import ProviderPort, ProviderRequestError, ProviderSelector
from .admission import AdmissionController


LOGGER = logging.getLogger("uvicorn.error.grapyth.gateway")
PROVIDER_QUOTA_ERROR_CODES = {
    "credit_balance_exhausted",
    "insufficient_quota",
    "organization_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
    "project_spend_limit_exceeded",
}


class ManagedCallConfigurationStore(Protocol):
    def load_runtime_policy(self, installation_id: str) -> InstallationPolicy: ...

    def load_gateway_limits(self) -> GatewayLimits: ...


class ManagedCallAccountingStore(Protocol):
    def load_user_budget(self, installation_id: str, user_id: str) -> UserBudget: ...

    def user_monthly_spend(self, installation_id: str, user_id: str) -> Decimal: ...

    def balance(self, installation_id: str) -> Decimal: ...

    def begin_call(self, value: CallStart) -> CallRecord: ...

    def complete_call(self, value: CallCompletion) -> CallRecord: ...

    def load_call_record(self, call_id: str) -> CallRecord: ...


@dataclass(frozen=True, slots=True)
class ManagedCallResult:
    response: dict[str, Any]
    headers: dict[str, str]


class ManagedCallOrchestrator:
    def __init__(
        self,
        configuration: ManagedCallConfigurationStore,
        accounting: ManagedCallAccountingStore,
        providers: ProviderSelector,
        admission: AdmissionController,
        *,
        logger: logging.Logger = LOGGER,
    ):
        self.configuration = configuration
        self.accounting = accounting
        self.providers = providers
        self.admission = admission
        self.logger = logger

    def execute(
        self,
        principal: InstallationPrincipal,
        idempotency_key: str,
        payload: Any,
        *,
        trace: TraceContext,
    ) -> ManagedCallResult:
        policy = self._load_policy(principal)
        requested_model = self._requested_model(payload)
        self._validate_idempotency_key(idempotency_key)
        request_payload = self._normalize_request(payload, policy)

        self.admission.enforce_rate_limit(policy.installation_id)
        with self.admission.installation_slot(policy.installation_id):
            provider = self._resolve_provider(policy)
            input_tokens = self._count_input_tokens(provider, policy, request_payload)
            reserve = self._check_budgets(
                policy,
                input_tokens,
                request_payload["max_output_tokens"],
                trace.end_user_id,
            )
            call = self._begin_call(policy, idempotency_key, requested_model, trace)
            started = time.perf_counter()
            try:
                provider_result = provider.invoke(
                    ProviderRequest(
                        payload=request_payload,
                        gateway_call_id=call.id,
                        fallback_model=policy.model,
                    )
                )
                provider_cost = usage_cost(
                    policy.pricing.provider_rates, provider_result.usage
                )
                billed_cost = min(
                    usage_cost(policy.pricing.billed_rates, provider_result.usage),
                    reserve,
                )
                margin = quantize_money(billed_cost - provider_cost)
                completed = self.accounting.complete_call(
                    CallCompletion(
                        call_id=call.id,
                        status="ok",
                        resolved_model=provider_result.resolved_model,
                        reasoning_effort=policy.reasoning_effort,
                        pricing=policy.pricing,
                        usage=provider_result.usage,
                        provider_request_id=provider_result.provider_request_id,
                        provider_cost_usd=provider_cost,
                        charged_usd=billed_cost,
                        margin_usd=margin,
                        below_cost=policy.pricing.below_cost or margin < 0,
                        duration_ms=self._duration_ms(started),
                        record_ledger=policy.billing_mode == "prepaid",
                    )
                )
            except GatewayError:
                raise
            except Exception as exc:
                gateway_error = self._provider_gateway_error(
                    exc,
                    fallback_code="provider_error",
                    fallback_message="The AI provider request failed",
                    gateway_call_id=call.id,
                )
                self._complete_error(
                    call,
                    policy,
                    trace,
                    started,
                    gateway_error,
                )
                raise gateway_error from exc

            remaining = self.accounting.balance(policy.installation_id)
            self._log_success(call, completed, policy, trace)
            return ManagedCallResult(
                response=provider_result.response,
                headers=self._response_headers(call, completed, policy, remaining),
            )

    def _load_policy(self, principal: InstallationPrincipal) -> InstallationPolicy:
        try:
            policy = self.configuration.load_runtime_policy(principal.id)
        except (FileNotFoundError, ValueError) as exc:
            raise GatewayError(503, "installation_policy_invalid", str(exc)) from exc
        if not policy.enabled:
            raise GatewayError(
                403,
                "installation_disabled",
                "Managed AI is disabled for this installation",
            )
        return policy

    @staticmethod
    def _requested_model(payload: Any) -> str:
        return str(payload.get("model") or "")[:160] if isinstance(payload, dict) else ""

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str) -> None:
        if not idempotency_key or len(idempotency_key) > 200:
            raise GatewayError(
                400,
                "invalid_idempotency_key",
                "A valid Idempotency-Key header is required",
            )

    def _normalize_request(
        self, payload: Any, policy: InstallationPolicy
    ) -> dict[str, Any]:
        limits = self.configuration.load_gateway_limits()
        try:
            return normalize_provider_request(
                payload,
                model=policy.model,
                reasoning_effort=policy.reasoning_effort,
                max_output_tokens_limit=limits.max_output_tokens,
            )
        except DomainValidationError as exc:
            raise GatewayError(400, exc.code, str(exc)) from exc

    def _check_budgets(
        self,
        policy: InstallationPolicy,
        input_tokens: int,
        max_output_tokens: int,
        user_id: str,
    ) -> Decimal:
        if input_tokens > MAX_STANDARD_PRICING_INPUT_TOKENS:
            raise GatewayError(
                400,
                "input_limit_exceeded",
                "The request exceeds the supported input token limit",
                details={
                    "inputTokens": input_tokens,
                    "maxInputTokens": MAX_STANDARD_PRICING_INPUT_TOKENS,
                },
            )
        required = worst_case_cost(
            input_tokens,
            max_output_tokens,
            policy.pricing.billed_rates,
        )
        if user_id:
            budget = self.accounting.load_user_budget(policy.installation_id, user_id)
            if budget.monthly_limit_usd is not None:
                spent = self.accounting.user_monthly_spend(
                    policy.installation_id,
                    user_id,
                )
                if spent + required > budget.monthly_limit_usd:
                    raise GatewayError(
                        429,
                        "user_budget_exceeded",
                        "The monthly AI limit for this user has been reached",
                        details={
                            "spentUsd": format_money(spent),
                            "monthlyLimitUsd": format_money(budget.monthly_limit_usd),
                            "requiredReserveUsd": format_money(required),
                        },
                    )
        if policy.billing_mode == "prepaid":
            balance = self.accounting.balance(policy.installation_id)
            if balance < required:
                raise GatewayError(
                    402,
                    "insufficient_credit",
                    "Managed AI credit is insufficient for this request",
                    details={
                        "balanceUsd": format_money(balance),
                        "requiredReserveUsd": format_money(required),
                    },
                )
        return required

    @staticmethod
    def _count_input_tokens(
        provider: ProviderPort,
        policy: InstallationPolicy,
        request_payload: dict[str, Any],
    ) -> int:
        try:
            input_tokens = provider.count_input_tokens(
                ProviderRequest(
                    payload=request_payload,
                    gateway_call_id="",
                    fallback_model=policy.model,
                )
            )
            if isinstance(input_tokens, bool) or not isinstance(input_tokens, int):
                raise ValueError("The provider returned a non-integer input token count")
            if input_tokens < 0:
                raise ValueError("The provider returned a negative input token count")
            return input_tokens
        except Exception as exc:
            raise ManagedCallOrchestrator._provider_gateway_error(
                exc,
                fallback_code="provider_token_count_error",
                fallback_message="The AI provider could not count request tokens",
            ) from exc

    @staticmethod
    def _provider_gateway_error(
        error: Exception,
        *,
        fallback_code: str,
        fallback_message: str,
        gateway_call_id: str = "",
    ) -> GatewayError:
        details = ManagedCallOrchestrator._provider_error_details(error)
        if gateway_call_id:
            details["gatewayCallId"] = gateway_call_id
        if isinstance(error, ProviderRequestError):
            if (
                error.error_code.lower() in PROVIDER_QUOTA_ERROR_CODES
                or error.error_type.lower() == "insufficient_quota"
            ):
                details.pop("retryAfterSeconds", None)
                return GatewayError(
                    402,
                    "provider_quota_exceeded",
                    "The AI provider quota is unavailable",
                    details=details,
                )
            if error.status_code == 429:
                return GatewayError(
                    429,
                    "provider_rate_limited",
                    "The AI provider is temporarily rate limited",
                    details=details,
                )
        return GatewayError(502, fallback_code, fallback_message, details=details)

    @staticmethod
    def _provider_error_details(error: Exception) -> dict[str, Any]:
        if not isinstance(error, ProviderRequestError):
            return {"providerErrorCode": type(error).__name__[:80]}
        details: dict[str, Any] = {
            "providerErrorCode": error.error_code,
        }
        if error.error_type:
            details["providerErrorType"] = error.error_type
        if error.status_code is not None:
            details["providerStatusCode"] = error.status_code
        if error.request_id:
            details["providerRequestId"] = error.request_id
        if error.retry_after_seconds is not None:
            details["retryAfterSeconds"] = error.retry_after_seconds
        return details

    def _resolve_provider(self, policy: InstallationPolicy) -> ProviderPort:
        try:
            return self.providers.resolve(policy)
        except Exception as exc:
            raise GatewayError(
                502,
                "provider_configuration_error",
                "The AI provider is not available for this installation",
                details={"providerErrorCode": type(exc).__name__[:80]},
            ) from exc

    def _begin_call(
        self,
        policy: InstallationPolicy,
        idempotency_key: str,
        requested_model: str,
        trace: TraceContext,
    ) -> CallRecord:
        try:
            return self.accounting.begin_call(
                CallStart(
                    installation_id=policy.installation_id,
                    idempotency_key=idempotency_key,
                    requested_model=requested_model,
                    user_id=trace.end_user_id,
                    board_id=trace.board_id,
                    chat_id=trace.chat_id,
                    app_ai_call_id=trace.app_ai_call_id,
                )
            )
        except IdempotencyConflict as exc:
            if exc.status == "error":
                previous = self.accounting.load_call_record(exc.call_id)
                raise GatewayError(
                    502,
                    "previous_attempt_failed",
                    "The previous provider attempt failed; this request was not sent again",
                    details={
                        "gatewayCallId": exc.call_id,
                        "status": exc.status,
                        "providerErrorCode": previous.error_code or "ProviderError",
                    },
                ) from exc
            raise GatewayError(
                409,
                "idempotency_conflict",
                "This AI request has already been accepted",
                details={"gatewayCallId": exc.call_id, "status": exc.status},
            ) from exc

    def _complete_error(
        self,
        call: CallRecord,
        policy: InstallationPolicy,
        trace: TraceContext,
        started: float,
        gateway_error: GatewayError,
    ) -> None:
        diagnostics = {
            key: value
            for key, value in gateway_error.details.items()
            if key
            in {
                "providerErrorCode",
                "providerErrorType",
                "providerStatusCode",
                "providerRequestId",
                "retryAfterSeconds",
            }
        }
        provider_error_code = str(diagnostics["providerErrorCode"])
        self.accounting.complete_call(
            CallCompletion(
                call_id=call.id,
                status="error",
                resolved_model=policy.model,
                reasoning_effort=policy.reasoning_effort,
                pricing=policy.pricing,
                duration_ms=self._duration_ms(started),
                error_code=provider_error_code,
            )
        )
        emit_structured_event(
            self.logger,
            event="gateway.ai_call",
            level="warning",
            fields={
                **trace.to_log_fields(),
                "gatewayCallId": call.id,
                "installationId": policy.installation_id,
                "status": "error",
                "errorCode": gateway_error.code,
                **diagnostics,
            },
        )

    def _log_success(
        self,
        call: CallRecord,
        completed: CallRecord,
        policy: InstallationPolicy,
        trace: TraceContext,
    ) -> None:
        emit_structured_event(
            self.logger,
            event="gateway.ai_call",
            level="info",
            fields={
                **trace.to_log_fields(),
                "gatewayCallId": call.id,
                "installationId": policy.installation_id,
                "status": "ok",
                "billingMode": policy.billing_mode,
                "model": completed.resolved_model,
                "inputTokens": completed.input_tokens,
                "outputTokens": completed.output_tokens,
                "providerCostUsd": completed.provider_cost_usd,
                "billedCostUsd": completed.charged_usd,
                "marginUsd": completed.margin_usd,
                "belowCost": completed.below_cost,
                "durationMs": completed.duration_ms,
                "providerRequestId": completed.provider_request_id,
            },
        )

    @staticmethod
    def _response_headers(
        call: CallRecord,
        completed: CallRecord,
        policy: InstallationPolicy,
        remaining: Decimal,
    ) -> dict[str, str]:
        headers = {
            "x-grapyth-gateway-call-id": call.id,
            "x-grapyth-billed-cost-usd": completed.charged_usd or "0.000000",
            "x-grapyth-pricing-version": policy.pricing.version,
            "x-grapyth-policy-model": policy.model,
            "x-grapyth-policy-reasoning-effort": policy.reasoning_effort,
        }
        if policy.billing_mode == "prepaid":
            headers["x-grapyth-budget-remaining-usd"] = format_money(remaining)
        return headers

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)
