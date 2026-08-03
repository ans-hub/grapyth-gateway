from __future__ import annotations

from typing import Any


class DomainValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


class ConflictError(RuntimeError):
    """A requested operation conflicts with already persisted state."""


class IdempotencyConflict(ConflictError):
    def __init__(self, call_id: str, status: str):
        super().__init__("Idempotency key has already been used")
        self.call_id = call_id
        self.status = status


class GatewayError(RuntimeError):
    """Application error mapped to the public HTTP error contract."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details or {}
