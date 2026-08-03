from __future__ import annotations

import hashlib
import re
import secrets


INSTALLATION_TOKEN_PATTERN = re.compile(r"gpi_[A-Za-z0-9_-]{43}\Z")


def create_installation_token() -> str:
    return "gpi_" + secrets.token_urlsafe(32)


def is_installation_token(value: str) -> bool:
    return bool(INSTALLATION_TOKEN_PATTERN.fullmatch(value))


def hash_installation_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
