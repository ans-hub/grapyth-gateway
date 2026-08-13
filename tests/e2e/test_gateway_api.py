from __future__ import annotations

import asyncio
import base64
import threading
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

import gateway.server as gateway_server
from gateway.domain import CallStart, ProviderRequest, ProviderResult
from gateway.server import GatewaySettings, create_app


class Provider:
    def __init__(self):
        self.calls = []
        self.model_checks = []

    @staticmethod
    def count_input_tokens(_request: ProviderRequest) -> int:
        return 30

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        payload = dict(request.payload)
        self.calls.append(payload)
        return ProviderResult.from_response(
            {
                "id": "provider-response",
                "model": payload["model"],
                "output_text": "ok",
                "usage": {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35},
            },
            {"x-request-id": "provider-request"},
            fallback_model=request.fallback_model,
        )

    def check_model(self, model: str) -> str:
        self.model_checks.append(model)
        return model


def basic_auth(password: str) -> dict[str, str]:
    raw = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}", "X-Grapyth-Admin": "1"}


def explicit_policy(app, provider_credential_id: str = "", **overrides) -> dict:
    plan = app.state.configuration.list_pricing_plans()[0]
    return {
        "providerCredentialId": provider_credential_id,
        "pricingPlanId": plan.id,
        "reasoningEffort": "low",
        "billingMode": "prepaid",
        **overrides,
    }


@pytest.mark.anyio
async def test_startup_fails_interrupted_calls_without_replaying_provider_requests(
    tmp_path: Path,
) -> None:
    settings = GatewaySettings(tmp_path, "admin-secret")
    first_app = create_app(settings, Provider())
    installation, token = first_app.state.configuration.create_installation(
        "Interrupted client"
    )
    first_app.state.accounting.add_credit(
        installation.id,
        Decimal("10"),
        "recovery credit",
    )
    running = first_app.state.accounting.begin_call(
        CallStart(
            installation_id=installation.id,
            idempotency_key="interrupted-call",
            requested_model="gpt-5.6-terra",
        )
    )

    second_provider = Provider()
    second_app = create_app(settings, second_provider)
    recovered = second_app.state.accounting.call(running.id)
    assert recovered.status == "error"
    assert recovered.error_code == "gateway_restarted"
    assert recovered.gateway_error_code == "gateway_restarted"
    assert recovered.failure is not None
    assert recovered.failure.to_payload() == {
        "phase": "unknown",
        "kind": "gateway_failure",
    }
    assert recovered.completed_at is not None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second_app),
        base_url="http://gateway.test",
    ) as client:
        duplicate = await client.post(
            "/v1/responses",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "interrupted-call",
            },
            json={
                "input": [{"role": "user", "content": "do not replay"}],
                "max_output_tokens": 10,
            },
        )

    assert duplicate.status_code == 502
    assert duplicate.json()["error"]["code"] == "previous_attempt_failed"
    assert second_provider.calls == []


@pytest.mark.anyio
async def test_provider_check_does_not_block_the_event_loop(tmp_path: Path) -> None:
    class BlockingCheckProvider(Provider):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def check_model(self, model: str) -> str:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("test provider was not released")
            return model

    provider = BlockingCheckProvider()
    app = create_app(
        GatewaySettings(
            tmp_path,
            "admin-secret",
            master_key=Fernet.generate_key().decode("ascii"),
        ),
        provider,
    )
    admin = basic_auth("admin-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        created = await client.post(
            "/admin/api/provider-credentials",
            headers=admin,
            json={"name": "Blocking check", "apiKey": "sk-test-blocking"},
        )
        credential_id = created.json()["item"]["id"]
        check = asyncio.create_task(
            client.post(
                f"/admin/api/provider-credentials/{credential_id}/test",
                headers=admin,
                json={"model": "gpt-5.6-terra"},
            )
        )
        assert await asyncio.to_thread(provider.entered.wait, 2)
        health = await asyncio.wait_for(client.get("/health"), timeout=2)
        provider.release.set()
        checked = await check

    assert health.status_code == 200
    assert checked.status_code == 200
    assert checked.json()["item"]["ok"] is True


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_admin_provisions_credit_and_client_calls_managed_ai(tmp_path: Path) -> None:
    provider = Provider()
    app = create_app(
        GatewaySettings(tmp_path, "admin-secret"),
        provider,
    )
    admin = basic_auth("admin-secret")
    assert app.state.credentials.list_provider_credentials() == []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/admin")).status_code == 401
        admin_assets = (
            "admin.css",
            "admin.js",
            "api.js",
            "audit.js",
            "clients.js",
            "pricing.js",
            "providers.js",
            "settings.js",
            "ui.js",
            "logo.png",
            "logo-single.png",
        )
        for asset_name in admin_assets:
            assert (await client.get(f"/admin/{asset_name}")).status_code == 401
        admin_page = await client.get("/admin", headers=admin)
        assert admin_page.status_code == 200
        assert "Test connection" in admin_page.text
        assert 'data-route="clients"' in admin_page.text
        assert 'data-route="providers"' in admin_page.text
        assert 'data-route="pricing"' in admin_page.text
        assert 'data-route="gateway"' in admin_page.text
        assert 'data-route="audit"' in admin_page.text
        assert "Defaults" not in admin_page.text
        assert 'id="max-output-tokens"' in admin_page.text
        assert 'id="max-request-mib"' in admin_page.text
        assert "unsafe-inline" not in admin_page.headers["content-security-policy"]
        assert "img-src 'self' data:" in admin_page.headers["content-security-policy"]
        authenticated_assets = {
            asset_name: await client.get(f"/admin/{asset_name}", headers=admin)
            for asset_name in admin_assets
        }
        assert all(
            response.status_code == 200
            for response in authenticated_assets.values()
        )
        admin_css = authenticated_assets["admin.css"]
        admin_js = authenticated_assets["admin.js"]
        assert "--bg:#16181c" in admin_css.text
        assert "--accent:#8eb2ff" in admin_css.text
        assert "--accent2:#ff6c8c" in admin_css.text
        assert "/admin/api/defaults" not in admin_js.text
        for logo_name in ("logo.png", "logo-single.png"):
            admin_logo = authenticated_assets[logo_name]
            assert admin_logo.headers["content-type"] == "image/png"
            assert admin_logo.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert (
            await client.get("/admin/not-an-asset.js", headers=admin)
        ).status_code == 404
        missing_policy = await client.post(
            "/admin/api/installations", headers=admin, json={"name": "Ambiguous client"}
        )
        assert missing_policy.status_code == 400
        assert "Explicit client policy is required" in missing_policy.json()["detail"]
        assert (await client.get("/admin/api/defaults", headers=admin)).status_code == 404
        settings = await client.patch(
            "/admin/api/settings",
            headers=admin,
            json={"maxOutputTokens": 8192, "maxRequestBytes": 2 * 1024 * 1024},
        )
        assert settings.status_code == 200
        assert settings.json()["item"]["maxOutputTokens"] == 8192
        assert settings.json()["item"]["maxRequestBytes"] == 2 * 1024 * 1024
        created = await client.post(
            "/admin/api/installations",
            headers=admin,
            json={"name": "Pilot Company", "note": "invoice-1", **explicit_policy(app)},
        )
        assert created.status_code == 201
        installation = created.json()["item"]
        token = created.json()["token"]
        assert token.startswith("gpi_")
        installations = await client.get("/admin/api/installations", headers=admin)
        assert token not in str(installations.json())

        credit = await client.post(
            f"/admin/api/installations/{installation['id']}/credit",
            headers=admin,
            json={"amountUsd": "25", "note": "paid manually"},
        )
        assert credit.status_code == 200
        assert credit.json()["item"]["balanceUsd"] == "25.000000"

        request = {
            "model": "gpt-5.6-terra",
            "input": [{"role": "user", "content": "private payload"}],
            "max_output_tokens": 4096,
            "tools": [
                {
                    "type": "function",
                    "name": "sample_database_query",
                    "description": "Return a bounded database sample",
                    "parameters": {
                        "type": "object",
                        "properties": {"sql": {"type": "string"}},
                        "required": ["sql"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": True,
        }
        ai = await client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "pilot-call-one"},
            json=request,
        )
        assert ai.status_code == 200
        assert ai.json()["output_text"] == "ok"
        assert ai.headers["x-grapyth-gateway-call-id"].startswith("gw-call-")
        assert provider.calls[0]["store"] is False
        assert provider.calls[0]["service_tier"] == "default"
        assert provider.calls[0]["tools"] == request["tools"]
        assert provider.calls[0]["tool_choice"] == "auto"
        assert provider.calls[0]["parallel_tool_calls"] is False

        account = await client.get("/v1/account", headers={"Authorization": f"Bearer {token}"})
        assert account.status_code == 200
        assert account.json()["item"]["usage"]["calls"] == 1
        assert account.json()["item"]["usage"]["billedCostUsd"] != "0.000000"
        assert account.json()["item"]["pricingPlan"]["version"]
        assert account.json()["item"]["limits"]["maxOutputTokens"] == 8192
        assert account.json()["item"]["limits"]["maxRequestBytes"] == 2 * 1024 * 1024
        assert "providerCostUsd" not in str(account.json())
        assert "marginUsd" not in str(account.json())
        assert float(account.json()["item"]["balanceUsd"]) < 25
        call_response = await client.get(
            f"/admin/api/installations/{installation['id']}/calls", headers=admin
        )
        call_rows = call_response.json()["items"]
        assert call_rows[0]["provider_request_id"] == "provider-request"
        assert "private payload" not in str(call_rows)
        ledger_response = await client.get(
            f"/admin/api/installations/{installation['id']}/ledger", headers=admin
        )
        ledger = ledger_response.json()["items"]
        assert {row["kind"] for row in ledger} == {"manual_credit", "ai_usage"}
        assert "private payload" not in str(ledger)


@pytest.mark.anyio
async def test_admin_mutations_require_csrf_resistant_header(tmp_path: Path) -> None:
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), Provider())
    raw = base64.b64encode(b"admin:admin-secret").decode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        response = await client.post(
            "/admin/api/installations",
            headers={"Authorization": f"Basic {raw}"},
            json={"name": "Denied"},
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_admin_validation_does_not_echo_secret_inputs(tmp_path: Path) -> None:
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), Provider())
    secret = "sk-validation-secret-must-not-return"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        response = await client.post(
            "/admin/api/provider-credentials",
            headers=basic_auth("admin-secret"),
            json={"name": "", "apiKey": secret, "unknownField": "ignored"},
        )

    assert response.status_code == 400
    assert secret not in response.text
    assert "unknownField" not in response.text


@pytest.mark.anyio
async def test_ai_body_limit_is_enforced_before_json_parsing(tmp_path: Path) -> None:
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), Provider())
    plan = app.state.configuration.list_pricing_plans()[0]
    installation, token = app.state.configuration.create_installation(
        "Limited client",
        pricing_plan_id=plan.id,
        reasoning_effort="low",
        billing_mode="prepaid",
    )
    app.state.configuration.update_gateway_settings(
        max_request_bytes=1024,
        max_output_tokens=100,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        response = await client.post(
            "/v1/responses",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "oversized-request",
                "Content-Type": "application/json",
            },
            content=b'{"input":[{"role":"user","content":"' + b"x" * 2048 + b'"}]}'
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert app.state.accounting.list_calls(installation.id) == []


@pytest.mark.anyio
async def test_disabled_default_provider_does_not_prevent_gateway_restart(tmp_path: Path) -> None:
    settings = GatewaySettings(tmp_path, "admin-secret")
    first_app = create_app(settings, Provider())
    credential = first_app.state.credentials.create_provider_credential(
        "Disabled after assignment", "encrypted-placeholder", "disabled"
    )
    first_app.state.configuration.update_defaults(
        provider_credential_id=credential.id
    )
    first_app.state.credentials.update_provider_credential(
        credential.id,
        enabled=False,
    )

    restarted_app = create_app(settings, Provider())

    assert (
        restarted_app.state.configuration.defaults().provider_credential_id
        == credential.id
    )
    assert (
        restarted_app.state.credentials.provider_credential(credential.id).enabled
        is False
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app), base_url="http://gateway.test"
    ) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/admin", headers=basic_auth("admin-secret"))).status_code == 200


@pytest.mark.anyio
async def test_disabled_installation_token_can_read_account_but_cannot_call_provider(tmp_path: Path) -> None:
    provider = Provider()
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), provider)
    plan = app.state.configuration.list_pricing_plans()[0]
    installation, token = app.state.configuration.create_installation(
        "Disabled account",
        pricing_plan_id=plan.id,
        reasoning_effort="low",
        billing_mode="prepaid",
    )
    app.state.configuration.update_installation(installation.id, enabled=False)
    bearer = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        account = await client.get("/v1/account", headers=bearer)
        denied = await client.post(
            "/v1/responses",
            headers={**bearer, "Idempotency-Key": "disabled-call"},
            json={
                "input": [{"role": "user", "content": "must not be sent"}],
                "max_output_tokens": 10,
            },
        )

    assert account.status_code == 200
    assert account.json()["item"]["enabled"] is False
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "installation_disabled"
    assert denied.headers["x-grapyth-error-code"] == "installation_disabled"
    assert provider.calls == []
    assert app.state.accounting.list_calls(installation.id) == []


@pytest.mark.anyio
async def test_client_tokens_isolate_accounts_calls_and_credit_ledgers(tmp_path: Path) -> None:
    app = create_app(GatewaySettings(tmp_path, "admin-secret"), Provider())
    admin = basic_auth("admin-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        first_created = await client.post(
            "/admin/api/installations",
            headers=admin,
            json={"name": "Tenant A", **explicit_policy(app)},
        )
        second_created = await client.post(
            "/admin/api/installations",
            headers=admin,
            json={"name": "Tenant B", **explicit_policy(app)},
        )
        first = first_created.json()
        second = second_created.json()
        await client.post(
            f"/admin/api/installations/{first['item']['id']}/credit",
            headers=admin,
            json={"amountUsd": "5", "note": "tenant-a-credit"},
        )
        await client.post(
            f"/admin/api/installations/{second['item']['id']}/credit",
            headers=admin,
            json={"amountUsd": "7", "note": "tenant-b-credit"},
        )

        request = {
            "model": "gpt-5.6-terra",
            "input": [{"role": "user", "content": "tenant-a-private"}],
            "max_output_tokens": 100,
        }
        first_call = await client.post(
            "/v1/responses",
            headers={
                "Authorization": f"Bearer {first['token']}",
                "Idempotency-Key": "tenant-a-call",
            },
            json=request,
        )
        assert first_call.status_code == 200

        first_account = await client.get(
            "/v1/account", headers={"Authorization": f"Bearer {first['token']}"}
        )
        second_account = await client.get(
            "/v1/account", headers={"Authorization": f"Bearer {second['token']}"}
        )
        assert first_account.json()["item"]["installationId"] == first["item"]["id"]
        assert first_account.json()["item"]["usage"]["calls"] == 1
        assert Decimal(first_account.json()["item"]["balanceUsd"]) < Decimal("5")
        assert second_account.json()["item"] == {
            "installationId": second["item"]["id"],
            "name": "Tenant B",
            "enabled": True,
            "balanceUsd": "7.000000",
            "billingMode": "prepaid",
            "usage": {"calls": 0, "inputTokens": 0, "outputTokens": 0, "billedCostUsd": "0.000000"},
            "model": "gpt-5.6-terra",
            "reasoningEffort": "low",
            "pricingPlan": {
                "name": "Default managed pricing",
                "version": "grapyth-managed-2026-08-03",
            },
            "limits": {
                "maxOutputTokens": 64000,
                "maxRequestBytes": 5 * 1024 * 1024,
                "updatedAt": second_account.json()["item"]["limits"]["updatedAt"],
            },
        }

        first_calls = await client.get(
            f"/admin/api/installations/{first['item']['id']}/calls", headers=admin
        )
        second_calls = await client.get(
            f"/admin/api/installations/{second['item']['id']}/calls", headers=admin
        )
        first_ledger = await client.get(
            f"/admin/api/installations/{first['item']['id']}/ledger", headers=admin
        )
        second_ledger = await client.get(
            f"/admin/api/installations/{second['item']['id']}/ledger", headers=admin
        )
        assert len(first_calls.json()["items"]) == 1
        assert second_calls.json()["items"] == []
        assert {row["kind"] for row in first_ledger.json()["items"]} == {"manual_credit", "ai_usage"}
        assert [row["kind"] for row in second_ledger.json()["items"]] == ["manual_credit"]
        assert "tenant-a-private" not in str(first_calls.json())
        assert "tenant-a-private" not in str(first_ledger.json())
        assert "tenant-a-private" not in str(second_account.json())


@pytest.mark.anyio
async def test_admin_encrypts_provider_keys_and_audits_policy_changes(tmp_path: Path) -> None:
    master_key = Fernet.generate_key().decode("ascii")
    provider = Provider()
    app = create_app(
        GatewaySettings(tmp_path, "admin-secret", master_key=master_key), provider
    )
    admin = basic_auth("admin-secret")
    secret = "sk-test-plaintext-must-never-reach-disk"
    rotated_secret = "sk-test-rotated-must-never-reach-disk"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        created_key = await client.post(
            "/admin/api/provider-credentials",
            headers=admin,
            json={"name": "Customer-owned key", "apiKey": secret},
        )
        assert created_key.status_code == 201
        credential = created_key.json()["item"]
        assert credential["keyHint"] == secret[-8:]
        assert secret not in str(created_key.json())
        listed_keys = await client.get("/admin/api/provider-credentials", headers=admin)
        assert secret not in str(listed_keys.json())
        connection = await client.post(
            f"/admin/api/provider-credentials/{credential['id']}/test",
            headers=admin,
            json={"model": "gpt-5.6-terra"},
        )
        assert connection.status_code == 200
        assert connection.json()["item"]["ok"] is True
        assert connection.json()["item"]["resolvedModel"] == "gpt-5.6-terra"
        assert connection.json()["item"]["checkedAt"]
        assert connection.json()["item"]["latencyMs"] >= 0
        assert provider.model_checks == ["gpt-5.6-terra"]

        plans = (await client.get("/admin/api/pricing-plans", headers=admin)).json()["items"]
        default_plan = plans[0]
        created_client = await client.post(
            "/admin/api/installations",
            headers=admin,
            json={
                "name": "BYOK customer",
                **explicit_policy(
                    app,
                    credential["id"],
                    pricingPlanId=default_plan["id"],
                    reasoningEffort="high",
                    billingMode="meter_only",
                ),
            },
        )
        item = created_client.json()["item"]
        token = created_client.json()["token"]
        assert item["providerCredentialId"] == credential["id"]
        assert item["reasoningEffort"] == "high"
        assert item["billingMode"] == "meter_only"

        account = await client.get(
            "/v1/account", headers={"Authorization": f"Bearer {token}"}
        )
        public = account.json()["item"]
        assert public["balanceUsd"] is None
        assert public["billingMode"] == "meter_only"
        assert "providerCredential" not in str(public)
        assert "providerCost" not in str(public)
        assert "margin" not in str(public)
        rotated = await client.patch(
            f"/admin/api/provider-credentials/{credential['id']}",
            headers=admin,
            json={"apiKey": rotated_secret},
        )
        assert rotated.status_code == 200
        assert rotated.json()["item"]["keyHint"] == rotated_secret[-8:]
        assert rotated_secret not in str(rotated.json())
        disabled = await client.patch(
            f"/admin/api/provider-credentials/{credential['id']}",
            headers=admin,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        disabled_check = await client.post(
            f"/admin/api/provider-credentials/{credential['id']}/test",
            headers=admin,
            json={"model": "gpt-5.6-terra"},
        )
        assert disabled_check.json()["item"] == {
            "ok": False,
            "model": "gpt-5.6-terra",
            "checkedAt": disabled_check.json()["item"]["checkedAt"],
            "latencyMs": 0,
            "errorCode": "credential_disabled",
            "message": "Provider credential is disabled",
        }
        assert provider.model_checks == ["gpt-5.6-terra"]
        reenabled = await client.patch(
            f"/admin/api/provider-credentials/{credential['id']}",
            headers=admin,
            json={"enabled": True},
        )
        assert reenabled.status_code == 200
        audit = (await client.get("/admin/api/audit", headers=admin)).json()["items"]
        assert {row["action"] for row in audit} >= {
            "provider_credential.created", "installation.created"
        }
        assert secret not in str(audit)

    persisted_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert secret.encode() not in persisted_bytes
    assert rotated_secret.encode() not in persisted_bytes
    encrypted = app.state.credentials.provider_credential_secret(credential["id"])
    assert encrypted != rotated_secret
    assert app.state.secret_cipher.decrypt(encrypted) == rotated_secret


@pytest.mark.anyio
async def test_provider_connection_failure_is_safe_and_does_not_create_billable_usage(tmp_path: Path) -> None:
    class AuthenticationError(RuntimeError):
        pass

    class FailingCheckProvider(Provider):
        def check_model(self, _model: str) -> str:
            raise AuthenticationError("private provider response must not leave the gateway")

    master_key = Fernet.generate_key().decode("ascii")
    app = create_app(
        GatewaySettings(tmp_path, "admin-secret", master_key=master_key),
        FailingCheckProvider(),
    )
    admin = basic_auth("admin-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        created = await client.post(
            "/admin/api/provider-credentials",
            headers=admin,
            json={"name": "Invalid key", "apiKey": "sk-invalid-secret"},
        )
        credential_id = created.json()["item"]["id"]
        checked = await client.post(
            f"/admin/api/provider-credentials/{credential_id}/test",
            headers=admin,
            json={"model": "gpt-5.6-terra"},
        )

    assert checked.status_code == 200
    result = checked.json()["item"]
    assert result["ok"] is False
    assert result["errorCode"] == "AuthenticationError"
    assert result["message"] == "Provider authentication failed"
    assert "private provider response" not in str(result)
    with app.state.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM credit_ledger").fetchone()[0] == 0


@pytest.mark.anyio
async def test_provider_check_uses_configured_provider_client_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    constructed = {}

    class ConfiguredProvider:
        def __init__(self, api_key: str, **options):
            constructed.update({"apiKey": api_key, **options})

        @staticmethod
        def check_model(model: str) -> str:
            return model

    monkeypatch.setattr(gateway_server, "OpenAIProvider", ConfiguredProvider)
    master_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("GRAPYTH_GATEWAY_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GRAPYTH_GATEWAY_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("GRAPYTH_GATEWAY_MASTER_KEY", master_key)
    app = create_app(GatewaySettings.from_environment())
    admin = basic_auth("admin-secret")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        created = await client.post(
            "/admin/api/provider-credentials",
            headers=admin,
            json={
                "name": "Provider settings",
                "apiKey": "sk-test-provider-settings",
            },
        )
        credential_id = created.json()["item"]["id"]
        checked = await client.post(
            f"/admin/api/provider-credentials/{credential_id}/test",
            headers=admin,
            json={"model": "gpt-5.6-terra"},
        )

    assert checked.status_code == 200
    assert checked.json()["item"]["ok"] is True
    assert constructed == {
        "apiKey": "sk-test-provider-settings",
        "timeout_seconds": 180.0,
        "max_retries": 1,
    }
