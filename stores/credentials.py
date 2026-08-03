from __future__ import annotations

import uuid
from contextlib import closing

from ..crypto import SecretCipher
from ..database import GatewayDatabase, utc_now
from ..persistence import credentials as credential_repository
from ..persistence.models import ProviderCredentialReadModel
from .audit import write_audit_event


class ProviderCredentialStore:
    """Persist provider credentials and keep secret reads explicit."""

    def __init__(self, database: GatewayDatabase):
        self.database = database

    def create_provider_credential(
        self,
        name: str,
        encrypted_api_key: str,
        key_hint: str,
    ) -> ProviderCredentialReadModel:
        resolved_name = str(name or "").strip()[:160]
        if not resolved_name:
            raise ValueError("Provider credential name is required")
        if not str(encrypted_api_key or "").strip():
            raise ValueError("Encrypted provider credential is required")
        credential_id = "provider-" + uuid.uuid4().hex
        with self.database.transaction() as connection:
            credential_repository.insert(
                connection,
                credential_id=credential_id,
                name=resolved_name,
                encrypted_api_key=encrypted_api_key,
                key_hint=str(key_hint or "")[-8:],
                created_at=utc_now(),
            )
            write_audit_event(
                connection,
                "provider_credential.created",
                "provider_credential",
                credential_id,
                {"name": resolved_name},
            )
            record = credential_repository.get(connection, credential_id)
        assert record is not None
        return record

    def update_provider_credential(
        self,
        credential_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        encrypted_api_key: str | None = None,
        key_hint: str | None = None,
    ) -> ProviderCredentialReadModel:
        with self.database.transaction() as connection:
            current = credential_repository.get(connection, credential_id)
            if not current:
                raise FileNotFoundError("Provider credential not found")
            resolved_name = current.name if name is None else str(name).strip()[:160]
            if not resolved_name:
                raise ValueError("Provider credential name is required")
            resolved_enabled = current.enabled if enabled is None else bool(enabled)
            credential_repository.update(
                connection,
                credential_id=credential_id,
                name=resolved_name,
                enabled=resolved_enabled,
                encrypted_api_key=encrypted_api_key,
                key_hint=str(key_hint or "")[-8:] if key_hint is not None else None,
                updated_at=utc_now(),
            )
            write_audit_event(
                connection,
                "provider_credential.updated",
                "provider_credential",
                credential_id,
                {
                    "name": resolved_name,
                    "enabled": resolved_enabled,
                    "keyRotated": encrypted_api_key is not None,
                },
            )
            record = credential_repository.get(connection, credential_id)
        assert record is not None
        return record

    def provider_credential(
        self,
        credential_id: str,
    ) -> ProviderCredentialReadModel:
        with closing(self.database.connect()) as connection:
            record = credential_repository.get(connection, credential_id)
        if not record:
            raise FileNotFoundError("Provider credential not found")
        return record

    def provider_credential_secret(self, credential_id: str) -> str:
        with closing(self.database.connect()) as connection:
            secret = credential_repository.secret(connection, credential_id)
        if not secret:
            raise FileNotFoundError("Provider credential not found")
        encrypted_api_key, enabled = secret
        if not enabled:
            raise ValueError("Provider credential is disabled")
        return encrypted_api_key

    def list_provider_credentials(self) -> list[ProviderCredentialReadModel]:
        with closing(self.database.connect()) as connection:
            return credential_repository.list_all(connection)

    def verify_enabled_provider_credentials(self, cipher: SecretCipher) -> None:
        with closing(self.database.connect()) as connection:
            for encrypted_secret in credential_repository.iter_enabled_secrets(connection):
                cipher.decrypt(encrypted_secret)
