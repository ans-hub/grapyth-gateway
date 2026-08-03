from __future__ import annotations

from fastapi import FastAPI

from ..config import DEFAULT_MODEL, DEFAULT_PRICING_VERSION, DEFAULT_RATES, GatewaySettings
from ..crypto import SecretCipher
from ..database import GatewayDatabase
from ..domain import PricingPlanSpec
from ..providers.base import FixedProviderSelector, GatewayProvider
from ..providers.openai import OpenAIProvider
from ..providers.registry import CredentialProviderRegistry, ProviderFactory
from ..service import GatewayService
from ..stores import (
    GatewayAccountingStore,
    GatewayConfigurationStore,
    ProviderCredentialStore,
)
from .admin import router as admin_router
from .errors import register_error_handlers
from .health import router as health_router
from .middleware import register_request_middleware
from .public import router as public_router


def create_gateway_app(
    settings: GatewaySettings,
    provider: GatewayProvider | None = None,
    *,
    provider_factory: ProviderFactory = OpenAIProvider,
) -> FastAPI:
    database = GatewayDatabase(settings.data_root / "gateway.db")
    database.initialize()
    credentials = ProviderCredentialStore(database)
    configuration = GatewayConfigurationStore(database)
    accounting = GatewayAccountingStore(database)
    # A supported deployment has one process, so no live call can survive startup.
    accounting.reconcile_interrupted_calls()
    cipher = SecretCipher(settings.master_key) if settings.master_key else None
    _ensure_initial_configuration(configuration)
    provider_registry = CredentialProviderRegistry(
        credentials,
        cipher,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        provider_factory=provider_factory,
    )
    service = GatewayService(
        configuration,
        accounting,
        FixedProviderSelector(provider) if provider else provider_registry,
        requests_per_minute=settings.requests_per_minute,
        global_concurrent_calls=settings.global_concurrent_calls,
    )

    app = FastAPI(
        title="Grapyth AI Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.database = database
    app.state.credentials = credentials
    app.state.configuration = configuration
    app.state.accounting = accounting
    app.state.service = service
    app.state.settings = settings
    app.state.secret_cipher = cipher
    app.state.provider_override = provider
    app.state.provider_registry = provider_registry

    register_error_handlers(app)
    register_request_middleware(app)
    app.include_router(health_router)
    app.include_router(public_router)
    app.include_router(admin_router)
    return app


def _ensure_initial_configuration(configuration: GatewayConfigurationStore) -> None:
    plans = configuration.list_pricing_plans()
    if not plans:
        plans = [
            configuration.create_pricing_plan(
                PricingPlanSpec(
                    name="Default managed pricing",
                    model=DEFAULT_MODEL,
                    version=DEFAULT_PRICING_VERSION,
                    provider_rates=DEFAULT_RATES,
                    billed_rates=DEFAULT_RATES,
                )
            )
        ]
    defaults = configuration.defaults()
    if not defaults.pricing_plan_id:
        configuration.initialize_default_pricing_plan(plans[0].id)
