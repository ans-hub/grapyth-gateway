from __future__ import annotations

from decimal import Decimal

from gateway.domain import InstallationPolicy, PricingRates, PricingSnapshot
from gateway.providers.registry import CredentialProviderRegistry


class Database:
    encrypted = "encrypted-one"
    enabled = True

    def provider_credential_secret(self, _credential_id: str) -> str:
        if not self.enabled:
            raise ValueError("Provider credential is disabled")
        return self.encrypted


class Cipher:
    @staticmethod
    def decrypt(value: str) -> str:
        return "plain:" + value


def test_provider_cache_reuses_current_key_and_invalidates_rotation_or_disablement() -> None:
    database = Database()
    constructed = []

    def factory(api_key: str, **options):
        provider = object()
        constructed.append((api_key, options, provider))
        return provider

    registry = CredentialProviderRegistry(
        database,  # type: ignore[arg-type]
        Cipher(),  # type: ignore[arg-type]
        timeout_seconds=30,
        max_retries=2,
        provider_factory=factory,  # type: ignore[arg-type]
    )
    rates = PricingRates(
        input=Decimal("1"),
        cached=Decimal("1"),
        cache_write=Decimal("1"),
        output=Decimal("1"),
    )
    policy = InstallationPolicy(
        installation_id="installation-one",
        enabled=True,
        provider_credential_id="provider-one",
        model="gpt-5.6-terra",
        reasoning_effort="low",
        billing_mode="prepaid",
        pricing=PricingSnapshot("plan-one", "version-one", rates, rates, False),
    )

    first = registry.resolve(policy)
    assert registry.for_credential("provider-one") is first
    database.encrypted = "encrypted-two"
    second = registry.for_credential("provider-one")
    assert second is not first
    registry.invalidate("provider-one")
    assert registry.for_credential("provider-one") is not second
    database.enabled = False
    try:
        registry.for_credential("provider-one")
    except ValueError as exc:
        assert str(exc) == "Provider credential is disabled"
    else:  # pragma: no cover - assertion diagnostic
        raise AssertionError("A disabled credential must never return a cached provider")

    assert [item[0] for item in constructed] == [
        "plain:encrypted-one",
        "plain:encrypted-two",
        "plain:encrypted-two",
    ]
    assert constructed[0][1] == {
        "timeout_seconds": 30,
        "max_retries": 2,
    }
