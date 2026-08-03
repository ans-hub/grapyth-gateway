from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Protocol

from ..crypto import SecretCipher
from ..domain import InstallationPolicy
from .base import GatewayProvider
from .openai import OpenAIProvider


ProviderFactory = Callable[..., GatewayProvider]


class ProviderCredentialReader(Protocol):
    def provider_credential_secret(self, credential_id: str) -> str: ...


class CredentialProviderRegistry:
    """Resolve and invalidate cached providers from current encrypted credentials."""

    def __init__(
        self,
        credentials: ProviderCredentialReader,
        cipher: SecretCipher | None,
        *,
        timeout_seconds: float,
        max_retries: int,
        provider_factory: ProviderFactory = OpenAIProvider,
    ):
        self._credentials = credentials
        self._cipher = cipher
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._provider_factory = provider_factory
        self._cache: dict[str, tuple[str, GatewayProvider]] = {}
        self._lock = Lock()

    def resolve(self, policy: InstallationPolicy) -> GatewayProvider:
        credential_id = policy.provider_credential_id
        if not credential_id:
            raise RuntimeError("This installation has no provider credential")
        return self.for_credential(credential_id)

    def for_credential(self, credential_id: str) -> GatewayProvider:
        if self._cipher is None:
            raise RuntimeError("GRAPYTH_GATEWAY_MASTER_KEY is not configured")
        encrypted = self._credentials.provider_credential_secret(credential_id)
        with self._lock:
            cached = self._cache.get(credential_id)
            if cached and cached[0] == encrypted:
                return cached[1]
            resolved = self._provider_factory(
                self._cipher.decrypt(encrypted),
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
            )
            self._cache[credential_id] = (encrypted, resolved)
            return resolved

    def invalidate(self, credential_id: str) -> None:
        with self._lock:
            self._cache.pop(credential_id, None)
