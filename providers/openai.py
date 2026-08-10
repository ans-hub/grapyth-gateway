from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from ..domain import ProviderRequest, ProviderResult
from .base import ProviderRequestError


class OpenAIProvider:
    TOKEN_COUNT_FIELDS = {"input", "instructions", "model", "reasoning", "text"}

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 180.0,
        max_retries: int = 1,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - production diagnostic
            raise RuntimeError("The openai package is not installed") from exc
        client_options: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
            "max_retries": max_retries,
        }
        self.client = OpenAI(**client_options) if api_key else None

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        response, headers = self._create_response(
            dict(request.payload), gateway_call_id=request.gateway_call_id
        )
        return ProviderResult.from_response(
            response, headers, fallback_model=request.fallback_model
        )

    def count_input_tokens(self, request: ProviderRequest) -> int:
        if self.client is None:
            raise RuntimeError(
                "An OpenAI provider credential is not configured for this installation"
            )
        payload = {
            key: value
            for key, value in request.payload.items()
            if key in self.TOKEN_COUNT_FIELDS
        }
        try:
            response = self.client.responses.input_tokens.count(**payload)
        except Exception as exc:
            translated = self._translate_error(exc)
            if translated is None:
                raise
            raise translated from exc
        input_tokens = int(response.input_tokens)
        if input_tokens < 0:
            raise RuntimeError("The provider returned an invalid input token count")
        return input_tokens

    def _create_response(
        self, payload: dict[str, Any], *, gateway_call_id: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        if self.client is None:
            raise RuntimeError(
                "An OpenAI provider credential is not configured for this installation"
            )
        request_payload = dict(payload)
        if gateway_call_id:
            request_payload["extra_headers"] = {"Idempotency-Key": gateway_call_id}
        cache_options = request_payload.pop("prompt_cache_options", None)
        if cache_options is not None:
            request_payload["extra_body"] = {"prompt_cache_options": cache_options}
        try:
            raw = self.client.responses.with_raw_response.create(**request_payload)
        except Exception as exc:
            translated = self._translate_error(exc)
            if translated is None:
                raise
            raise translated from exc
        response = raw.parse()
        headers = {str(key).lower(): str(value) for key, value in raw.headers.items()}
        item = response.model_dump(mode="json")
        return item, headers

    def check_model(self, model: str) -> str:
        if self.client is None:
            raise RuntimeError("An OpenAI provider credential is not configured")
        diagnostic_client = self.client.with_options(max_retries=0, timeout=10.0)
        resolved = diagnostic_client.models.retrieve(model)
        return str(getattr(resolved, "id", "") or model)

    @staticmethod
    def _translate_error(error: Exception) -> ProviderRequestError | None:
        try:
            from openai import OpenAIError
        except ImportError:  # pragma: no cover - construction reports the missing package
            return None
        if not isinstance(error, OpenAIError):
            return None

        exception_type = type(error).__name__
        body = getattr(error, "body", None)
        provider_error = (
            body.get("error")
            if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping)
            else body
        )
        provider_code = ""
        provider_type = ""
        if isinstance(provider_error, Mapping):
            provider_code = str(provider_error.get("code") or "").strip()
            provider_type = str(provider_error.get("type") or "").strip()

        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        request_id = str(getattr(error, "request_id", "") or "")
        retry_after_seconds = None
        if isinstance(headers, Mapping):
            request_id = request_id or str(headers.get("x-request-id") or "")
            retry_after_seconds = OpenAIProvider._parse_retry_after_seconds(
                headers.get("retry-after")
            )

        raw_status = getattr(error, "status_code", None)
        status_code = (
            raw_status
            if not isinstance(raw_status, bool) and isinstance(raw_status, int)
            else None
        )
        return ProviderRequestError(
            error_code=provider_code or provider_type or exception_type,
            error_type=provider_type,
            status_code=status_code,
            request_id=request_id,
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _parse_retry_after_seconds(value: object) -> int | None:
        normalized = str(value or "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?", normalized):
            return None
        parsed = float(normalized)
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        return max(1, math.ceil(parsed))
