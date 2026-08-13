from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..errors import GatewayError
from ..observability import FAILURE_DIAGNOSTIC_FIELDS
from .dependencies import trace_fields


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        request.state.error_code = exc.code
        for detail_name in FAILURE_DIAGNOSTIC_FIELDS:
            if detail_name in exc.details:
                request.state.failure_diagnostics[detail_name] = exc.details[detail_name]
        body = {
            "error": {
                "message": str(exc),
                "type": "grapyth_gateway_error",
                "code": exc.code,
                **exc.details,
            },
            **trace_fields(request),
        }
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {}
        if exc.details.get("retryAfterSeconds"):
            headers["Retry-After"] = str(exc.details["retryAfterSeconds"])
        if exc.details.get("gatewayCallId"):
            headers["X-Grapyth-Gateway-Call-Id"] = str(exc.details["gatewayCallId"])
        return JSONResponse(body, status_code=exc.status_code, headers=headers)

    @app.exception_handler(FileNotFoundError)
    async def not_found_handler(request: Request, exc: FileNotFoundError):
        request.state.error_code = "not_found"
        return JSONResponse(
            {"detail": str(exc), "errorCode": "not_found", **trace_fields(request)},
            status_code=404,
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        request.state.error_code = "invalid_request"
        return JSONResponse(
            {"detail": str(exc), "errorCode": "invalid_request", **trace_fields(request)},
            status_code=400,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        error_code = "http_" + str(exc.status_code)
        request.state.error_code = error_code
        return JSONResponse(
            {"detail": exc.detail, "errorCode": error_code, **trace_fields(request)},
            status_code=exc.status_code,
            headers=exc.headers,
        )
