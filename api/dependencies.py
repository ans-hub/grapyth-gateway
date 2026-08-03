from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..config import GatewaySettings
from ..crypto import SecretCipher
from ..database import GatewayDatabase
from ..domain import InstallationPrincipal, TraceContext
from ..errors import GatewayError
from ..providers.registry import CredentialProviderRegistry
from ..service import GatewayService
from ..stores import (
    GatewayAccountingStore,
    GatewayConfigurationStore,
    ProviderCredentialStore,
)


basic_security = HTTPBasic(auto_error=False)


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    username: str


def database_from(request: Request) -> GatewayDatabase:
    return request.app.state.database


def credentials_from(request: Request) -> ProviderCredentialStore:
    return request.app.state.credentials


def configuration_from(request: Request) -> GatewayConfigurationStore:
    return request.app.state.configuration


def accounting_from(request: Request) -> GatewayAccountingStore:
    return request.app.state.accounting


def service_from(request: Request) -> GatewayService:
    return request.app.state.service


def settings_from(request: Request) -> GatewaySettings:
    return request.app.state.settings


def provider_registry_from(request: Request) -> CredentialProviderRegistry:
    return request.app.state.provider_registry


def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_security)],
) -> AdminPrincipal:
    settings = settings_from(request)
    valid = bool(
        settings.admin_password
        and credentials
        and secrets.compare_digest(credentials.username, "admin")
        and secrets.compare_digest(credentials.password, settings.admin_password)
    )
    if not valid:
        raise HTTPException(
            401,
            "Admin authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return AdminPrincipal(username="admin")


def require_admin_mutation(
    request: Request,
    _admin: Annotated[AdminPrincipal, Depends(require_admin)],
) -> AdminPrincipal:
    if request.headers.get("x-grapyth-admin") != "1":
        raise HTTPException(403, "Missing admin request header")
    return _admin


def require_installation(request: Request) -> InstallationPrincipal:
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    principal = (
        configuration_from(request).authenticate_principal(token.strip())
        if scheme.lower() == "bearer"
        else None
    )
    if not principal:
        raise GatewayError(
            401,
            "invalid_installation_token",
            "Installation authentication failed",
        )
    return principal


def require_cipher(request: Request) -> SecretCipher:
    cipher = request.app.state.secret_cipher
    if cipher is None:
        raise ValueError(
            "GRAPYTH_GATEWAY_MASTER_KEY must be configured before provider keys can be saved"
        )
    return cipher


def trace_context_from(request: Request) -> TraceContext:
    return TraceContext(
        request_id=getattr(request.state, "request_id", "")[:128],
        correlation_id=getattr(request.state, "correlation_id", "")[:128],
        app_request_id=getattr(request.state, "app_request_id", "")[:128],
        app_ai_call_id=getattr(request.state, "app_ai_call_id", "")[:128],
        end_user_id=getattr(request.state, "end_user_id", "")[:128],
        board_id=getattr(request.state, "board_id", "")[:128],
        chat_id=getattr(request.state, "chat_id", "")[:128],
    )


def trace_fields(request: Request) -> dict[str, str]:
    return trace_context_from(request).to_log_fields()
