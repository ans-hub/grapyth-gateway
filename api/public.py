from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..domain import InstallationPrincipal, UserBudget, format_money
from ..errors import GatewayError
from .body import limited_body, safe_json
from .dependencies import (
    accounting_from,
    configuration_from,
    require_installation,
    service_from,
    trace_context_from,
)
from .schemas import UserLimitRequest, parse_model


router = APIRouter(prefix="/v1")


@router.post("/responses")
async def responses(
    request: Request,
    installation: Annotated[InstallationPrincipal, Depends(require_installation)],
):
    configuration = configuration_from(request)
    raw = await limited_body(
        request,
        configuration.load_gateway_limits().max_request_bytes,
    )
    if not raw:
        raise GatewayError(413, "request_too_large", "AI request body is empty or too large")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GatewayError(400, "invalid_json", "AI request body is not valid JSON") from exc
    response, headers = await run_in_threadpool(
        service_from(request).execute,
        installation,
        request.headers.get("idempotency-key") or "",
        payload,
        trace_context=trace_context_from(request),
    )
    return JSONResponse(response, headers=headers)


@router.get("/account")
async def account(
    request: Request,
    authenticated: Annotated[InstallationPrincipal, Depends(require_installation)],
):
    configuration = configuration_from(request)
    item = configuration.installation(authenticated.id)
    plan = configuration.pricing_plan(item.pricing_plan_id)
    usage = item.usage
    public_item = {
        "installationId": item.id,
        "name": item.name,
        "enabled": item.enabled,
        "billingMode": item.billing_mode,
        "balanceUsd": (
            format_money(item.balance_usd)
            if item.billing_mode == "prepaid"
            else None
        ),
        "usage": {
            "calls": usage.calls,
            "inputTokens": usage.input_tokens,
            "outputTokens": usage.output_tokens,
            "billedCostUsd": format_money(usage.billed_cost_usd),
        },
        "model": item.model,
        "reasoningEffort": item.reasoning_effort,
        "pricingPlan": {"name": plan.name, "version": plan.version},
        "limits": configuration.gateway_settings().to_payload(),
    }
    return {"item": public_item}


@router.get("/account/users")
async def account_users(
    request: Request,
    authenticated: Annotated[InstallationPrincipal, Depends(require_installation)],
):
    return {
        "items": [
            item.to_payload()
            for item in accounting_from(request).user_usage(authenticated.id)
        ]
    }


@router.put("/account/users/{user_id}/limit")
async def account_user_limit(
    user_id: str,
    request: Request,
    authenticated: Annotated[InstallationPrincipal, Depends(require_installation)],
):
    if request.headers.get("x-grapyth-admin") != "1":
        raise GatewayError(403, "admin_required", "An administrator request is required")
    value = parse_model(UserLimitRequest, await safe_json(request))
    budget = accounting_from(request).set_user_limit(
        authenticated.id,
        user_id,
        value.monthly_limit_usd,
    )
    return {"item": _user_budget_payload(budget)}


def _user_budget_payload(value: UserBudget) -> dict[str, str | None]:
    return {
        "userId": value.user_id,
        "monthlyLimitUsd": (
            format_money(value.monthly_limit_usd)
            if value.monthly_limit_usd is not None
            else None
        ),
        "updatedAt": value.updated_at,
    }
