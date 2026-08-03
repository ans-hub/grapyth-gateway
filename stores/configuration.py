from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing

from ..database import GatewayDatabase, utc_now
from ..domain import (
    BillingMode,
    GatewayLimits,
    InstallationPolicy,
    InstallationPolicyAssignment,
    InstallationPolicyChanges,
    InstallationPrincipal,
    PricingPlanChanges,
    PricingPlanSpec,
    ReasoningEffort,
)
from ..persistence import credentials as credential_repository
from ..persistence import installations as installation_repository
from ..persistence import pricing as pricing_repository
from ..persistence.models import (
    GatewayDefaultsReadModel,
    GatewaySettingsReadModel,
    InstallationSummaryReadModel,
    PricingPlanReadModel,
)
from ..security import (
    create_installation_token,
    hash_installation_token,
    is_installation_token,
)
from .audit import write_audit_event


class GatewayConfigurationStore:
    """Persist pricing, limits, defaults, installations, and runtime policy."""

    def __init__(self, database: GatewayDatabase):
        self.database = database

    def create_pricing_plan(self, spec: PricingPlanSpec) -> PricingPlanReadModel:
        plan_id = "plan-" + uuid.uuid4().hex
        with self.database.transaction() as connection:
            pricing_repository.insert_plan(
                connection,
                plan_id=plan_id,
                value=spec,
                created_at=utc_now(),
            )
            self._audit_plan(connection, "pricing_plan.created", plan_id, spec)
            record = pricing_repository.get_plan(connection, plan_id)
        assert record is not None
        return record

    def update_pricing_plan(
        self,
        plan_id: str,
        changes: PricingPlanChanges,
    ) -> PricingPlanReadModel:
        with self.database.transaction() as connection:
            current = pricing_repository.get_plan(connection, plan_id)
            if not current:
                raise FileNotFoundError("Pricing plan not found")
            current_spec = current.spec
            spec = PricingPlanSpec(
                name=current_spec.name if changes.name is None else changes.name,
                model=current_spec.model if changes.model is None else changes.model,
                version=current_spec.version if changes.version is None else changes.version,
                provider_rates=(
                    current_spec.provider_rates
                    if changes.provider_rates is None
                    else changes.provider_rates
                ),
                billed_rates=(
                    current_spec.billed_rates
                    if changes.billed_rates is None
                    else changes.billed_rates
                ),
                allow_below_cost=(
                    current_spec.allow_below_cost
                    if changes.allow_below_cost is None
                    else changes.allow_below_cost
                ),
                below_cost_reason=(
                    current_spec.below_cost_reason
                    if changes.below_cost_reason is None
                    else changes.below_cost_reason
                ),
                enabled=(
                    current_spec.enabled
                    if changes.enabled is None
                    else changes.enabled
                ),
            )
            pricing_repository.update_plan(
                connection,
                plan_id=plan_id,
                value=spec,
                updated_at=utc_now(),
            )
            self._audit_plan(connection, "pricing_plan.updated", plan_id, spec)
            record = pricing_repository.get_plan(connection, plan_id)
        assert record is not None
        return record

    @staticmethod
    def _audit_plan(
        connection: sqlite3.Connection,
        action: str,
        plan_id: str,
        spec: PricingPlanSpec,
    ) -> None:
        write_audit_event(
            connection,
            action,
            "pricing_plan",
            plan_id,
            {
                "name": spec.name,
                "model": spec.model,
                "version": spec.version,
                "providerRates": spec.provider_rates.to_payload(),
                "billedRates": spec.billed_rates.to_payload(),
                "belowCost": spec.below_cost,
                "reason": spec.below_cost_reason,
            },
        )

    def pricing_plan(self, plan_id: str) -> PricingPlanReadModel:
        with closing(self.database.connect()) as connection:
            record = pricing_repository.get_plan(connection, plan_id)
        if not record:
            raise FileNotFoundError("Pricing plan not found")
        return record

    def list_pricing_plans(self) -> list[PricingPlanReadModel]:
        with closing(self.database.connect()) as connection:
            return pricing_repository.list_plans(connection)

    def gateway_settings(self) -> GatewaySettingsReadModel:
        with closing(self.database.connect()) as connection:
            return pricing_repository.get_settings(connection)

    def load_gateway_limits(self) -> GatewayLimits:
        value = self.gateway_settings()
        return GatewayLimits(
            max_output_tokens=value.max_output_tokens,
            max_request_bytes=value.max_request_bytes,
            updated_at=value.updated_at,
        )

    def update_gateway_settings(
        self,
        *,
        max_output_tokens: int | None = None,
        max_request_bytes: int | None = None,
    ) -> GatewaySettingsReadModel:
        with self.database.transaction() as connection:
            current = pricing_repository.get_settings(connection)
            resolved_output_tokens = self._setting_integer(
                current.max_output_tokens
                if max_output_tokens is None
                else max_output_tokens,
                "maxOutputTokens",
                1,
                128_000,
            )
            resolved_request_bytes = self._setting_integer(
                current.max_request_bytes
                if max_request_bytes is None
                else max_request_bytes,
                "maxRequestBytes",
                1024,
                100 * 1024 * 1024,
            )
            pricing_repository.update_settings(
                connection,
                max_output_tokens=resolved_output_tokens,
                max_request_bytes=resolved_request_bytes,
                updated_at=utc_now(),
            )
            write_audit_event(
                connection,
                "gateway_settings.updated",
                "gateway_settings",
                "1",
                {
                    "maxOutputTokens": resolved_output_tokens,
                    "maxRequestBytes": resolved_request_bytes,
                },
            )
            return pricing_repository.get_settings(connection)

    @staticmethod
    def _setting_integer(
        value: int,
        label: str,
        minimum: int,
        maximum: int,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must be an integer")
        if value < minimum or value > maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}")
        return value

    def defaults(self) -> GatewayDefaultsReadModel:
        with closing(self.database.connect()) as connection:
            return pricing_repository.get_defaults(connection)

    def initialize_default_pricing_plan(
        self,
        pricing_plan_id: str,
    ) -> GatewayDefaultsReadModel:
        """Fill the startup default plan without revalidating a provider assignment."""
        with self.database.transaction() as connection:
            if not pricing_repository.get_plan(connection, pricing_plan_id):
                raise FileNotFoundError("Pricing plan not found")
            pricing_repository.initialize_default_plan(
                connection,
                plan_id=pricing_plan_id,
                updated_at=utc_now(),
            )
            return pricing_repository.get_defaults(connection)

    def update_defaults(
        self,
        *,
        provider_credential_id: str | None = None,
        pricing_plan_id: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        billing_mode: BillingMode | None = None,
        audit: bool = True,
    ) -> GatewayDefaultsReadModel:
        changes = InstallationPolicyChanges(
            provider_credential_id=provider_credential_id,
            pricing_plan_id=pricing_plan_id,
            reasoning_effort=reasoning_effort,
            billing_mode=billing_mode,
        )
        with self.database.transaction() as connection:
            current = pricing_repository.get_defaults(connection).policy
            policy = self._resolve_policy(connection, current, changes)
            pricing_repository.update_defaults(
                connection,
                provider_credential_id=policy.provider_credential_id,
                pricing_plan_id=policy.pricing_plan_id,
                reasoning_effort=policy.reasoning_effort,
                billing_mode=policy.billing_mode,
                updated_at=utc_now(),
            )
            if audit:
                write_audit_event(
                    connection,
                    "defaults.updated",
                    "gateway_defaults",
                    "1",
                    self._policy_audit_fields(policy),
                )
            return pricing_repository.get_defaults(connection)

    @staticmethod
    def _resolve_policy(
        connection: sqlite3.Connection,
        current: InstallationPolicyAssignment,
        changes: InstallationPolicyChanges,
    ) -> InstallationPolicyAssignment:
        policy = InstallationPolicyAssignment(
            provider_credential_id=(
                current.provider_credential_id
                if changes.provider_credential_id is None
                else changes.provider_credential_id
            ),
            pricing_plan_id=(
                current.pricing_plan_id
                if changes.pricing_plan_id is None
                else changes.pricing_plan_id
            ),
            reasoning_effort=(
                current.reasoning_effort
                if changes.reasoning_effort is None
                else changes.reasoning_effort
            ),
            billing_mode=(
                current.billing_mode
                if changes.billing_mode is None
                else changes.billing_mode
            ),
        )
        if policy.provider_credential_id:
            credential = credential_repository.get(
                connection,
                policy.provider_credential_id,
            )
            if not credential:
                raise FileNotFoundError("Provider credential not found")
            if not credential.enabled:
                raise ValueError("Provider credential is disabled")
        if not policy.pricing_plan_id:
            raise ValueError("Pricing plan is required")
        plan = pricing_repository.get_plan(connection, policy.pricing_plan_id)
        if not plan:
            raise FileNotFoundError("Pricing plan not found")
        if not plan.enabled:
            raise ValueError("Pricing plan is disabled")
        return policy

    @staticmethod
    def _policy_audit_fields(
        policy: InstallationPolicyAssignment,
    ) -> dict[str, str]:
        return {
            "providerCredentialId": policy.provider_credential_id,
            "pricingPlanId": policy.pricing_plan_id,
            "reasoningEffort": policy.reasoning_effort,
            "billingMode": policy.billing_mode,
        }

    def create_installation(
        self,
        name: str,
        note: str = "",
        *,
        provider_credential_id: str | None = None,
        pricing_plan_id: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        billing_mode: BillingMode | None = None,
    ) -> tuple[InstallationSummaryReadModel, str]:
        resolved_name = str(name or "").strip()[:160]
        if not resolved_name:
            raise ValueError("Client name is required")
        token = create_installation_token()
        installation_id = "inst-" + uuid.uuid4().hex
        changes = InstallationPolicyChanges(
            provider_credential_id=provider_credential_id,
            pricing_plan_id=pricing_plan_id,
            reasoning_effort=reasoning_effort,
            billing_mode=billing_mode,
        )
        with self.database.transaction() as connection:
            defaults = pricing_repository.get_defaults(connection).policy
            policy = self._resolve_policy(connection, defaults, changes)
            installation_repository.insert(
                connection,
                installation_id=installation_id,
                name=resolved_name,
                token_hash=hash_installation_token(token),
                token_hint=token[-6:],
                note=str(note or "")[:1000],
                provider_credential_id=policy.provider_credential_id,
                pricing_plan_id=policy.pricing_plan_id,
                reasoning_effort=policy.reasoning_effort,
                billing_mode=policy.billing_mode,
                created_at=utc_now(),
            )
            write_audit_event(
                connection,
                "installation.created",
                "installation",
                installation_id,
                {
                    "name": resolved_name,
                    **self._policy_audit_fields(policy),
                },
            )
            result = installation_repository.get_summary(connection, installation_id)
        assert result is not None
        return result, token

    def rotate_token(
        self,
        installation_id: str,
    ) -> tuple[InstallationSummaryReadModel, str]:
        token = create_installation_token()
        with self.database.transaction() as connection:
            changed = installation_repository.rotate_token(
                connection,
                installation_id=installation_id,
                token_hash=hash_installation_token(token),
                token_hint=token[-6:],
                updated_at=utc_now(),
            )
            if not changed:
                raise FileNotFoundError("Installation not found")
            write_audit_event(
                connection,
                "installation.token_rotated",
                "installation",
                installation_id,
                {},
            )
            result = installation_repository.get_summary(connection, installation_id)
        assert result is not None
        return result, token

    def update_installation(
        self,
        installation_id: str,
        *,
        enabled: bool | None = None,
        name: str | None = None,
        note: str | None = None,
        provider_credential_id: str | None = None,
        pricing_plan_id: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        billing_mode: BillingMode | None = None,
    ) -> InstallationSummaryReadModel:
        changes = InstallationPolicyChanges(
            provider_credential_id=provider_credential_id,
            pricing_plan_id=pricing_plan_id,
            reasoning_effort=reasoning_effort,
            billing_mode=billing_mode,
        )
        with self.database.transaction() as connection:
            current = installation_repository.get_summary(connection, installation_id)
            if not current:
                raise FileNotFoundError("Installation not found")
            resolved_name = current.name if name is None else str(name).strip()[:160]
            if not resolved_name:
                raise ValueError("Client name is required")
            policy = self._resolve_policy(connection, current.policy, changes)
            resolved_enabled = current.enabled if enabled is None else bool(enabled)
            installation_repository.update(
                connection,
                installation_id=installation_id,
                name=resolved_name,
                note=current.note if note is None else str(note)[:1000],
                enabled=resolved_enabled,
                provider_credential_id=policy.provider_credential_id,
                pricing_plan_id=policy.pricing_plan_id,
                reasoning_effort=policy.reasoning_effort,
                billing_mode=policy.billing_mode,
                updated_at=utc_now(),
            )
            write_audit_event(
                connection,
                "installation.updated",
                "installation",
                installation_id,
                {
                    "name": resolved_name,
                    "enabled": resolved_enabled,
                    **self._policy_audit_fields(policy),
                },
            )
            result = installation_repository.get_summary(connection, installation_id)
        assert result is not None
        return result

    def apply_defaults_to_installations(
        self,
        installation_ids: list[str],
    ) -> list[InstallationSummaryReadModel]:
        ids = [str(value) for value in installation_ids if str(value)]
        with self.database.transaction() as connection:
            defaults = pricing_repository.get_defaults(connection).policy
            policy = self._resolve_policy(
                connection,
                defaults,
                InstallationPolicyChanges(),
            )
            current = {
                item.id: item
                for item in installation_repository.list_summaries(connection)
                if item.id in ids
            }
            for installation_id in ids:
                item = current.get(installation_id)
                if not item:
                    raise FileNotFoundError("Installation not found")
                installation_repository.update(
                    connection,
                    installation_id=installation_id,
                    name=item.name,
                    note=item.note,
                    enabled=item.enabled,
                    provider_credential_id=policy.provider_credential_id,
                    pricing_plan_id=policy.pricing_plan_id,
                    reasoning_effort=policy.reasoning_effort,
                    billing_mode=policy.billing_mode,
                    updated_at=utc_now(),
                )
                write_audit_event(
                    connection,
                    "installation.updated",
                    "installation",
                    installation_id,
                    {
                        "name": item.name,
                        "enabled": item.enabled,
                        **self._policy_audit_fields(policy),
                    },
                )
            updated = {
                item.id: item
                for item in installation_repository.list_summaries(connection)
            }
        return [updated[installation_id] for installation_id in ids]

    def authenticate_principal(self, token: str) -> InstallationPrincipal | None:
        if not token or not is_installation_token(token):
            return None
        with closing(self.database.connect()) as connection:
            return installation_repository.authenticate(
                connection,
                hash_installation_token(token),
            )

    def load_runtime_policy(self, installation_id: str) -> InstallationPolicy:
        with closing(self.database.connect()) as connection:
            policy = installation_repository.load_runtime_policy(
                connection,
                installation_id,
            )
        if not policy:
            raise FileNotFoundError("Installation not found")
        return policy

    def installation(self, installation_id: str) -> InstallationSummaryReadModel:
        with closing(self.database.connect()) as connection:
            record = installation_repository.get_summary(connection, installation_id)
        if not record:
            raise FileNotFoundError("Installation not found")
        return record

    def list_installations(self) -> list[InstallationSummaryReadModel]:
        with closing(self.database.connect()) as connection:
            return installation_repository.list_summaries(connection)
