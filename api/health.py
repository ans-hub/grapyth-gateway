from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..persistence.migrations import SchemaCompatibilityError
from .dependencies import database_from


router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    issues: list[str] = []
    try:
        database_from(request).verify_schema()
    except SchemaCompatibilityError:
        issues.append("database_schema_incompatible")
    except Exception:
        issues.append("database_unavailable")
    if request.app.state.provider_override is None:
        cipher = request.app.state.secret_cipher
        if cipher is None:
            issues.append("master_key_not_configured")
        else:
            try:
                request.app.state.credentials.verify_enabled_provider_credentials(cipher)
            except RuntimeError:
                issues.append("provider_credentials_undecryptable")
            except Exception:
                issues.append("database_unavailable")
    return JSONResponse(
        {"status": "error" if issues else "ok", "issues": issues},
        status_code=503 if issues else 200,
    )
