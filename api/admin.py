from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ..crypto import SecretCipher
from ..database import utc_now
from ..domain import PricingPlanChanges, PricingPlanSpec, PricingRates
from ..observability import emit_structured_event
from ..services.calls import LOGGER
from .body import safe_json
from .dependencies import (
    AdminPrincipal,
    accounting_from,
    configuration_from,
    credentials_from,
    provider_registry_from,
    require_admin,
    require_admin_mutation,
    require_cipher,
    trace_fields,
)
from .schemas import (
    CreditAdjustmentRequest,
    GatewaySettingsUpdateRequest,
    InstallationCreateRequest,
    InstallationUpdateRequest,
    PricingPlanCreateRequest,
    PricingPlanUpdateRequest,
    PricingRatesRequest,
    ProviderCredentialCreateRequest,
    ProviderCredentialUpdateRequest,
    ProviderTestRequest,
    parse_model,
)


ADMIN_ROOT = Path(__file__).resolve().parents[1] / "admin"
ADMIN_ASSETS = {
    "admin.css": "text/css",
    "admin.js": "text/javascript",
    "api.js": "text/javascript",
    "audit.js": "text/javascript",
    "clients.js": "text/javascript",
    "pricing.js": "text/javascript",
    "providers.js": "text/javascript",
    "settings.js": "text/javascript",
    "ui.js": "text/javascript",
    "logo.png": "image/png",
    "logo-single.png": "image/png",
}
router = APIRouter(prefix="/admin")


@router.get("", response_class=HTMLResponse)
async def admin_page(_admin: Annotated[AdminPrincipal, Depends(require_admin)]):
    return HTMLResponse((ADMIN_ROOT / "index.html").read_text(encoding="utf-8"))


@router.get("/{asset_name}", response_class=FileResponse)
async def admin_asset(
    asset_name: str,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    media_type = ADMIN_ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(404, "Administration asset not found")
    return FileResponse(ADMIN_ROOT / asset_name, media_type=media_type)


@router.get("/api/installations")
async def admin_installations(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {
        "items": [
            item.to_payload()
            for item in configuration_from(request).list_installations()
        ]
    }


@router.post("/api/installations")
async def admin_create_installation(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    payload = await safe_json(request)
    required = ("providerCredentialId", "pricingPlanId", "reasoningEffort", "billingMode")
    missing = [field for field in required if field not in payload]
    if missing:
        raise HTTPException(
            400,
            f"Explicit client policy is required: {', '.join(missing)}",
        )
    value = parse_model(InstallationCreateRequest, payload)
    item, token = configuration_from(request).create_installation(
        value.name,
        value.note,
        provider_credential_id=value.provider_credential_id,
        pricing_plan_id=value.pricing_plan_id,
        reasoning_effort=value.reasoning_effort,
        billing_mode=value.billing_mode,
    )
    return JSONResponse(
        {"item": item.to_payload(), "token": token},
        status_code=201,
    )


@router.patch("/api/installations/{installation_id}")
async def admin_update_installation(
    installation_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(InstallationUpdateRequest, await safe_json(request))
    return {
        "item": configuration_from(request).update_installation(
            installation_id,
            enabled=value.enabled,
            name=value.name,
            note=value.note,
            provider_credential_id=value.provider_credential_id,
            pricing_plan_id=value.pricing_plan_id,
            reasoning_effort=value.reasoning_effort,
            billing_mode=value.billing_mode,
        ).to_payload()
    }


@router.post("/api/installations/{installation_id}/credit")
async def admin_add_credit(
    installation_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(CreditAdjustmentRequest, await safe_json(request))
    item = accounting_from(request).add_credit(
        installation_id,
        value.amount_usd,
        value.note,
    )
    return {"item": item.to_payload()}


@router.post("/api/installations/{installation_id}/rotate-token")
async def admin_rotate_token(
    installation_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    item, token = configuration_from(request).rotate_token(installation_id)
    return {"item": item.to_payload(), "token": token}


@router.get("/api/installations/{installation_id}/calls")
async def admin_calls(
    installation_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {
        "items": [
            item.to_payload()
            for item in accounting_from(request).list_calls(installation_id)
        ]
    }


@router.get("/api/installations/{installation_id}/ledger")
async def admin_ledger(
    installation_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {
        "items": [
            item.to_payload()
            for item in accounting_from(request).list_ledger(installation_id)
        ]
    }


@router.get("/api/provider-credentials")
async def admin_provider_credentials(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {
        "items": [
            item.to_payload()
            for item in credentials_from(request).list_provider_credentials()
        ]
    }


@router.post("/api/provider-credentials")
async def admin_create_provider_credential(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
    cipher: Annotated[SecretCipher, Depends(require_cipher)],
):
    value = parse_model(ProviderCredentialCreateRequest, await safe_json(request))
    api_key = value.api_key.get_secret_value()
    item = credentials_from(request).create_provider_credential(
        value.name, cipher.encrypt(api_key), api_key[-8:]
    )
    return JSONResponse({"item": item.to_payload()}, status_code=201)


@router.patch("/api/provider-credentials/{credential_id}")
async def admin_update_provider_credential(
    credential_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(ProviderCredentialUpdateRequest, await safe_json(request))
    api_key = value.api_key.get_secret_value() if value.api_key is not None else ""
    cipher = require_cipher(request) if api_key else None
    item = credentials_from(request).update_provider_credential(
        credential_id,
        name=value.name,
        enabled=value.enabled,
        encrypted_api_key=cipher.encrypt(api_key) if cipher else None,
        key_hint=api_key[-8:] if api_key else None,
    )
    provider_registry_from(request).invalidate(credential_id)
    return {"item": item.to_payload()}


@router.post("/api/provider-credentials/{credential_id}/test")
async def admin_test_provider_credential(
    credential_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(ProviderTestRequest, await safe_json(request))
    credential = credentials_from(request).provider_credential(credential_id)
    checked_at = utc_now()
    started = time.perf_counter()
    if not credential.enabled:
        return {
            "item": {
                "ok": False,
                "model": value.model,
                "checkedAt": checked_at,
                "latencyMs": 0,
                "errorCode": "credential_disabled",
                "message": "Provider credential is disabled",
            }
        }
    try:
        resolved_provider = request.app.state.provider_override or provider_registry_from(
            request
        ).for_credential(credential_id)
        resolved_model = await run_in_threadpool(resolved_provider.check_model, value.model)
        item = {
            "ok": True,
            "model": value.model,
            "resolvedModel": resolved_model,
            "checkedAt": checked_at,
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        error_code = type(exc).__name__[:80]
        item = {
            "ok": False,
            "model": value.model,
            "checkedAt": checked_at,
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
            "errorCode": error_code,
            "message": provider_test_message(error_code),
        }
        emit_structured_event(
            LOGGER,
            event="gateway.provider_test",
            level="warning",
            fields={
                **trace_fields(request),
                "providerCredentialId": credential_id,
                "model": value.model,
                "status": "error",
                "errorCode": error_code,
                "durationMs": item["latencyMs"],
            },
        )
    return {"item": item}


@router.get("/api/pricing-plans")
async def admin_pricing_plans(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {
        "items": [
            item.to_payload()
            for item in configuration_from(request).list_pricing_plans()
        ]
    }


@router.post("/api/pricing-plans")
async def admin_create_pricing_plan(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(PricingPlanCreateRequest, await safe_json(request))
    item = configuration_from(request).create_pricing_plan(
        PricingPlanSpec(
            name=value.name,
            model=value.model,
            version=value.version,
            provider_rates=_pricing_rates(value.provider_rates),
            billed_rates=_pricing_rates(value.billed_rates),
            allow_below_cost=value.allow_below_cost,
            below_cost_reason=value.below_cost_reason,
            enabled=value.enabled,
        )
    )
    return JSONResponse({"item": item.to_payload()}, status_code=201)


@router.patch("/api/pricing-plans/{plan_id}")
async def admin_update_pricing_plan(
    plan_id: str,
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(PricingPlanUpdateRequest, await safe_json(request))
    item = configuration_from(request).update_pricing_plan(
        plan_id,
        PricingPlanChanges(
            name=value.name,
            model=value.model,
            version=value.version,
            provider_rates=(
                _pricing_rates(value.provider_rates)
                if value.provider_rates is not None
                else None
            ),
            billed_rates=(
                _pricing_rates(value.billed_rates)
                if value.billed_rates is not None
                else None
            ),
            allow_below_cost=value.allow_below_cost,
            below_cost_reason=value.below_cost_reason,
            enabled=value.enabled,
        ),
    )
    return {"item": item.to_payload()}


@router.get("/api/settings")
async def admin_settings(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {"item": configuration_from(request).gateway_settings().to_payload()}


@router.patch("/api/settings")
async def admin_update_settings(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin_mutation)],
):
    value = parse_model(GatewaySettingsUpdateRequest, await safe_json(request))
    item = configuration_from(request).update_gateway_settings(
        max_output_tokens=value.max_output_tokens,
        max_request_bytes=value.max_request_bytes,
    )
    return {"item": item.to_payload()}


@router.get("/api/audit")
async def admin_audit(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
):
    return {
        "items": [
            item.to_payload() for item in accounting_from(request).list_audit()
        ]
    }


def _pricing_rates(value: PricingRatesRequest) -> PricingRates:
    return PricingRates(
        input=value.input,
        cached=value.cached,
        cache_write=value.cache_write,
        output=value.output,
    )


def provider_test_message(error_code: str) -> str:
    lowered = error_code.lower()
    if "authentication" in lowered:
        return "Provider authentication failed"
    if "permission" in lowered or "notfound" in lowered:
        return "The model is not available to this credential"
    if "connection" in lowered or "timeout" in lowered:
        return "The gateway could not connect to the provider"
    if "ratelimit" in lowered:
        return "The provider rate limit prevented the check"
    return "Provider connection check failed"
