from __future__ import annotations

from fastapi import FastAPI

from .api.app import create_gateway_app
from .config import GatewaySettings
from .providers.base import GatewayProvider
from .providers.openai import OpenAIProvider


def create_app(
    settings: GatewaySettings | None = None,
    provider: GatewayProvider | None = None,
) -> FastAPI:
    """Stable Gateway composition entry point."""
    return create_gateway_app(
        settings or GatewaySettings.from_environment(),
        provider,
        provider_factory=OpenAIProvider,
    )


__all__ = [
    "GatewaySettings",
    "OpenAIProvider",
    "GatewayProvider",
    "create_app",
]
