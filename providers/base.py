from __future__ import annotations

import re
from typing import Protocol

from ..domain import InstallationPolicy, ProviderRequest, ProviderResult


DIAGNOSTIC_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
MAX_PROVIDER_DIAGNOSTIC_LENGTH = 80
MAX_PROVIDER_REQUEST_ID_LENGTH = 200
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60


class ProviderRequestError(RuntimeError):
    """Sanitized provider failure safe to cross the provider boundary"""

    def __init__(
        self,
        *,
        error_code: str,
        error_type: str = "",
        status_code: int | None = None,
        request_id: str = "",
        retry_after_seconds: int | None = None,
    ):
        super().__init__("The AI provider request failed")
        self.error_type = self._safe_identifier(
            error_type,
            "",
            max_length=MAX_PROVIDER_DIAGNOSTIC_LENGTH,
        )
        self.error_code = self._safe_identifier(
            error_code,
            self.error_type or "ProviderError",
            max_length=MAX_PROVIDER_DIAGNOSTIC_LENGTH,
        )
        self.status_code = (
            status_code
            if not isinstance(status_code, bool)
            and isinstance(status_code, int)
            and 100 <= status_code <= 599
            else None
        )
        self.request_id = self._safe_identifier(
            request_id,
            "",
            max_length=MAX_PROVIDER_REQUEST_ID_LENGTH,
        )
        self.retry_after_seconds = (
            retry_after_seconds
            if not isinstance(retry_after_seconds, bool)
            and isinstance(retry_after_seconds, int)
            and 1 <= retry_after_seconds <= MAX_RETRY_AFTER_SECONDS
            else None
        )

    @staticmethod
    def _safe_identifier(
        value: object,
        fallback: str,
        *,
        max_length: int,
    ) -> str:
        normalized = str(value or "").strip()
        return (
            normalized
            if len(normalized) <= max_length
            and DIAGNOSTIC_IDENTIFIER_PATTERN.fullmatch(normalized)
            else fallback
        )


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
