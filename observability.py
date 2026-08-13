from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal


LogLevel = Literal["info", "warning", "error"]
TRACE_FIELDS = {
    "requestId",
    "correlationId",
    "appRequestId",
    "appAiCallId",
    "endUserId",
    "boardId",
    "chatId",
}
PROVIDER_DIAGNOSTIC_FIELDS = frozenset(
    {
        "providerErrorCode",
        "providerErrorType",
        "providerStatusCode",
        "providerRequestId",
        "providerErrorParam",
        "retryAfterSeconds",
    }
)
FAILURE_DIAGNOSTIC_FIELDS = PROVIDER_DIAGNOSTIC_FIELDS | {
    "gatewayPhase",
    "failureKind",
}
EVENT_FIELDS = {
    "gateway.ai_call": TRACE_FIELDS
    | FAILURE_DIAGNOSTIC_FIELDS
    | {
        "gatewayCallId",
        "installationId",
        "status",
        "billingMode",
        "model",
        "inputTokens",
        "outputTokens",
        "providerCostUsd",
        "billedCostUsd",
        "marginUsd",
        "belowCost",
        "durationMs",
        "providerRequestId",
        "errorCode",
        "requestKind",
        "outcomeKind",
        "requestedToolCallCount",
    },
    "gateway.http_request": TRACE_FIELDS
    | FAILURE_DIAGNOSTIC_FIELDS
    | {
        "method",
        "path",
        "status",
        "durationMs",
        "errorCode",
    },
    "gateway.unhandled_error": TRACE_FIELDS
    | {
        "errorCode",
        "errorType",
        "stack",
    },
    "gateway.provider_test": TRACE_FIELDS
    | {
        "providerCredentialId",
        "model",
        "status",
        "errorCode",
        "durationMs",
    },
}


def emit_structured_event(
    logger: logging.Logger,
    *,
    event: str,
    level: LogLevel,
    fields: dict[str, Any],
) -> None:
    allowed = EVENT_FIELDS.get(event)
    if allowed is None:
        raise ValueError(f"Unknown structured event: {event}")
    unexpected = sorted(set(fields) - allowed)
    if unexpected:
        raise ValueError(f"Unexpected fields for {event}: {', '.join(unexpected)}")
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": "grapyth-gateway",
        "event": event,
        **fields,
    }
    getattr(logger, level)(json.dumps(payload, ensure_ascii=False))
