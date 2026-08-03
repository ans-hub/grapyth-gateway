from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, master_key: str):
        try:
            value = str(master_key or "").strip().encode("ascii", errors="strict")
            self._fernet = Fernet(value)
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ValueError(
                "GRAPYTH_GATEWAY_MASTER_KEY must be a Fernet key generated with Fernet.generate_key()"
            ) from exc

    def encrypt(self, value: str) -> str:
        secret = str(value or "").strip()
        if not secret:
            raise ValueError("Provider API key is required")
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(str(value or "").encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise RuntimeError("Provider credential cannot be decrypted with the configured master key") from exc
