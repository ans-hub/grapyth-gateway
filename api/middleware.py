from __future__ import annotations

import re
import time
import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..observability import emit_structured_event
from ..services.calls import LOGGER
from .dependencies import trace_fields


TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def safe_trace_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized if TRACE_ID_PATTERN.fullmatch(normalized) else ""


def register_request_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        started = time.perf_counter()
        request.state.request_id = "gwr-" + uuid.uuid4().hex
        request.state.correlation_id = (
            safe_trace_id(request.headers.get("x-correlation-id"))
            or request.state.request_id
        )
        request.state.app_request_id = safe_trace_id(
            request.headers.get("x-grapyth-app-request-id")
        )
        request.state.app_ai_call_id = safe_trace_id(
            request.headers.get("x-grapyth-ai-call-id")
        )
        request.state.end_user_id = safe_trace_id(
            request.headers.get("x-grapyth-end-user-id")
        )
        request.state.board_id = safe_trace_id(request.headers.get("x-grapyth-board-id"))
        request.state.chat_id = safe_trace_id(request.headers.get("x-grapyth-chat-id"))
        request.state.error_code = ""
        request.state.failure_diagnostics = {}
        try:
            response = await call_next(request)
        except Exception as exc:
            request.state.error_code = "internal_error"
            emit_structured_event(
                LOGGER,
                event="gateway.unhandled_error",
                level="error",
                fields={
                    **trace_fields(request),
                    "errorCode": "internal_error",
                    "errorType": type(exc).__name__,
                    "stack": "".join(traceback.format_tb(exc.__traceback__)),
                },
            )
            response = JSONResponse(
                {
                    "error": {
                        "message": "Internal gateway error",
                        "type": "grapyth_gateway_error",
                        "code": "internal_error",
                    },
                    **trace_fields(request),
                },
                status_code=500,
            )

        _apply_security_headers(request, response)
        status = response.status_code
        level = "error" if status >= 500 else "warning" if status >= 400 else "info"
        emit_structured_event(
            LOGGER,
            event="gateway.http_request",
            level=level,
            fields={
                **trace_fields(request),
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "durationMs": round((time.perf_counter() - started) * 1000, 2),
                "errorCode": request.state.error_code,
                **request.state.failure_diagnostics,
            },
        )
        return response


def _apply_security_headers(request: Request, response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    if request.state.error_code:
        response.headers["X-Grapyth-Error-Code"] = request.state.error_code
    if request.url.path.startswith("/admin"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; form-action 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'"
        )
