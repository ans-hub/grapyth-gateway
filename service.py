from __future__ import annotations

from typing import Any

from .domain import InstallationPrincipal, TraceContext
from .providers.base import ProviderSelector
from .services.admission import AdmissionController
from .services.calls import (
    LOGGER,
    ManagedCallAccountingStore,
    ManagedCallConfigurationStore,
    ManagedCallOrchestrator,
)


class GatewayService:
    """Application service for managed provider calls."""

    def __init__(
        self,
        configuration: ManagedCallConfigurationStore,
        accounting: ManagedCallAccountingStore,
        providers: ProviderSelector,
        *,
        requests_per_minute: int = 20,
        global_concurrent_calls: int = 4,
    ):
        self.configuration = configuration
        self.accounting = accounting
        self.admission = AdmissionController(
            requests_per_minute=requests_per_minute,
            global_concurrent_calls=global_concurrent_calls,
        )
        self.providers = providers
        self.orchestrator = ManagedCallOrchestrator(
            self.configuration,
            self.accounting,
            self.providers,
            self.admission,
            logger=LOGGER,
        )

    def execute(
        self,
        principal: InstallationPrincipal,
        idempotency_key: str,
        payload: Any,
        *,
        trace_context: TraceContext | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        result = self.orchestrator.execute(
            principal,
            idempotency_key,
            payload,
            trace=trace_context or TraceContext(),
        )
        return result.response, result.headers
