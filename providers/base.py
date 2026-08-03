from __future__ import annotations

from typing import Protocol

from ..domain import InstallationPolicy, ProviderRequest, ProviderResult


class ProviderPort(Protocol):
    def count_input_tokens(self, request: ProviderRequest) -> int: ...

    def invoke(self, request: ProviderRequest) -> ProviderResult: ...


class GatewayProvider(ProviderPort, Protocol):
    def check_model(self, model: str) -> str: ...


class ProviderSelector(Protocol):
    def resolve(self, policy: InstallationPolicy) -> ProviderPort: ...


class FixedProviderSelector:
    def __init__(self, provider: ProviderPort):
        self._provider = provider

    def resolve(self, _policy: InstallationPolicy) -> ProviderPort:
        return self._provider
