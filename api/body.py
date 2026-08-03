from __future__ import annotations

import json

from fastapi import HTTPException, Request

from ..errors import GatewayError


MAX_ADMIN_REQUEST_BYTES = 64 * 1024


async def limited_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length < 0:
                raise ValueError
            if parsed_content_length > max_bytes:
                raise GatewayError(413, "request_too_large", "Request body is too large")
        except ValueError as exc:
            raise GatewayError(
                400,
                "invalid_content_length",
                "Content-Length must be an integer",
            ) from exc
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise GatewayError(413, "request_too_large", "Request body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def safe_json(request: Request) -> dict:
    try:
        raw = await limited_body(request, MAX_ADMIN_REQUEST_BYTES)
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Expected JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "Expected a JSON object")
    return value
