from __future__ import annotations

import socket
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from gateway.domain import (
    CallCompletion,
    CallStart,
    PricingPlanSpec,
    PricingRates,
    PricingSnapshot,
    ProviderUsage,
)
from gateway.server import GatewaySettings, create_app


def system_chromium() -> Path:
    candidates = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )
    browser_path = next((path for path in candidates if path.is_file()), None)
    if browser_path is None:
        raise AssertionError("A supported system Chromium browser is required for e2e tests")
    return browser_path


@pytest.fixture()
def gateway_admin_server(tmp_path: Path):
    app = create_app(GatewaySettings(tmp_path / "gateway-data", "admin-secret"))
    credentials = app.state.credentials
    configuration = app.state.configuration
    accounting = app.state.accounting
    primary = credentials.create_provider_credential(
        "Primary OpenAI",
        "encrypted-primary",
        "primary1",
    )
    credentials.create_provider_credential(
        "Backup OpenAI",
        "encrypted-backup",
        "backup22",
    )
    default_plan = configuration.list_pricing_plans()[0]
    configuration.create_pricing_plan(
        PricingPlanSpec(
            name="Premium managed pricing",
            model="gpt-5.6-sol",
            version="2026-07-premium",
            provider_rates=PricingRates(
                input="2",
                cached="0.2",
                cache_write="0",
                output="12",
            ),
            billed_rates=PricingRates(
                input="3",
                cached="0.3",
                cache_write="0",
                output="15",
            ),
        )
    )
    installation, _token = configuration.create_installation(
        "Layout client",
        provider_credential_id=primary.id,
        pricing_plan_id=default_plan.id,
        reasoning_effort="low",
        billing_mode="prepaid",
    )
    for index in range(30):
        accounting.add_credit(
            installation.id,
            Decimal("1"),
            f"Layout ledger entry {index + 1}",
        )
        call = accounting.begin_call(
            CallStart(
                installation_id=installation.id,
                idempotency_key=f"layout-call-{index + 1}",
                requested_model=default_plan.model,
            )
        )
        accounting.complete_call(
            CallCompletion(
                call_id=call.id,
                status="ok",
                resolved_model=default_plan.model,
                reasoning_effort="low",
                pricing=PricingSnapshot(
                    plan_id=default_plan.id,
                    version=default_plan.version,
                    provider_rates=default_plan.provider_rates,
                    billed_rates=default_plan.billed_rates,
                    below_cost=default_plan.below_cost,
                ),
                usage=ProviderUsage(
                    input_tokens=100 + index,
                    output_tokens=20 + index,
                    total_tokens=120 + (index * 2),
                ),
                provider_request_id=f"provider-layout-{index + 1}",
                provider_cost_usd=Decimal("0.001"),
                charged_usd=Decimal("0.002"),
                margin_usd=Decimal("0.001"),
                duration_ms=100 + index,
            )
        )
    failed_call = accounting.begin_call(
        CallStart(
            installation_id=installation.id,
            idempotency_key="layout-provider-failure",
            requested_model=default_plan.model,
        )
    )
    accounting.complete_call(
        CallCompletion(
            call_id=failed_call.id,
            status="error",
            resolved_model=default_plan.model,
            reasoning_effort="low",
            pricing=PricingSnapshot(
                plan_id=default_plan.id,
                version=default_plan.version,
                provider_rates=default_plan.provider_rates,
                billed_rates=default_plan.billed_rates,
                below_cost=default_plan.below_cost,
            ),
            error_code="RateLimitError",
            duration_ms=120,
        )
    )

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def test_entity_views_use_sidebars_and_fluid_detail_panels(gateway_admin_server: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(system_chromium()), headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            http_credentials={"username": "admin", "password": "admin-secret"},
        )
        page = context.new_page()
        try:
            page.goto(f"{gateway_admin_server}/admin")
            expect(page.locator("#client-list .entity-row")).to_have_count(1)
            expect(page.locator("#clients-pane .entity-sidebar")).to_be_visible()
            expect(page.locator(".entity-sidebar > header")).to_have_count(0)
            expect(page.locator(".sidebar-footer strong")).to_have_text("Admin")
            client_pane_box = page.locator("#clients-pane").bounding_box()
            client_search_box = page.locator("#clients-pane .list-search").bounding_box()
            assert client_pane_box is not None
            assert client_search_box is not None
            assert client_search_box["y"] - client_pane_box["y"] == pytest.approx(8, abs=1)
            logo = page.locator(".brand-logo")
            expect(logo).to_be_visible()
            desktop_logo = logo.evaluate("""element => ({
                complete: element.complete,
                currentSrc: element.currentSrc,
                naturalWidth: element.naturalWidth,
                naturalHeight: element.naturalHeight,
            })""")
            assert desktop_logo["complete"] is True
            assert desktop_logo["currentSrc"].endswith("/admin/logo.png")
            assert desktop_logo["naturalWidth"] == 967
            assert desktop_logo["naturalHeight"] == 267

            page.locator('[data-route="providers"]').click()
            expect(page.locator("#provider-list .entity-row")).to_have_count(2)
            expect(page.locator("#providers-pane .entity-sidebar")).to_be_visible()
            page.locator("#provider-list .entity-row").nth(1).click()
            expect(page.locator("#provider-detail .detail-heading h2")).to_have_text("Backup OpenAI")

            provider_widths = page.evaluate("""() => {
                const detail = document.querySelector('#provider-detail').getBoundingClientRect();
                const content = document.querySelector('#provider-detail .detail-content').getBoundingClientRect();
                return {detail: detail.width, content: content.width, maxWidth: getComputedStyle(document.querySelector('#provider-detail .detail-content')).maxWidth};
            }""")
            assert provider_widths["content"] == pytest.approx(provider_widths["detail"], abs=2)
            assert provider_widths["maxWidth"] == "none"

            page.locator('[data-route="pricing"]').click()
            expect(page.locator("#pricing-list .entity-row")).to_have_count(2)
            expect(page.locator("#pricing-pane .entity-sidebar")).to_be_visible()
            page.locator("#pricing-list .entity-row").nth(1).click()
            expect(page.locator("#pricing-detail .detail-heading h2")).to_have_text("Premium managed pricing")
            pricing_widths = page.evaluate("""() => {
                const detail = document.querySelector('#pricing-detail').getBoundingClientRect();
                const content = document.querySelector('#pricing-detail .detail-content').getBoundingClientRect();
                return {detail: detail.width, content: content.width, maxWidth: getComputedStyle(document.querySelector('#pricing-detail .detail-content')).maxWidth};
            }""")
            assert pricing_widths["content"] == pytest.approx(pricing_widths["detail"], abs=2)
            assert pricing_widths["maxWidth"] == "none"

            page.set_viewport_size({"width": 2100, "height": 900})
            shell = page.locator(".app-shell").bounding_box()
            assert shell is not None
            assert shell["width"] == pytest.approx(1920, abs=1)
            assert shell["x"] == pytest.approx(90, abs=1)

            page.set_viewport_size({"width": 700, "height": 800})
            expect(page.locator("#pricing-pane .entity-sidebar")).to_be_hidden()
            expect(logo).to_have_js_property(
                "currentSrc",
                f"{gateway_admin_server}/admin/logo-single.png",
            )
            narrow_branding = page.evaluate("""() => {
                const brand = document.querySelector('.sidebar-brand');
                const logo = document.querySelector('.brand-logo');
                const brandBox = brand.getBoundingClientRect();
                const logoBox = logo.getBoundingClientRect();
                return {
                    brandWidth: brandBox.width,
                    logoWidth: logoBox.width,
                    logoHeight: logoBox.height,
                    logoLeftOffset: logoBox.left - brandBox.left,
                    logoRightOffset: brandBox.right - logoBox.right,
                    naturalWidth: logo.naturalWidth,
                    naturalHeight: logo.naturalHeight,
                };
            }""")
            assert narrow_branding["brandWidth"] == pytest.approx(58, abs=1)
            assert narrow_branding["logoWidth"] == pytest.approx(40, abs=1)
            assert narrow_branding["logoHeight"] == pytest.approx(40, abs=1)
            assert narrow_branding["logoLeftOffset"] == pytest.approx(9, abs=1)
            assert narrow_branding["logoRightOffset"] == pytest.approx(9, abs=1)
            assert narrow_branding["naturalWidth"] == 318
            assert narrow_branding["naturalHeight"] == 318
            narrow_widths = page.evaluate("""() => {
                const pane = document.querySelector('#pricing-pane').getBoundingClientRect();
                const detail = document.querySelector('#pricing-detail').getBoundingClientRect();
                return {pane: pane.width, detail: detail.width};
            }""")
            assert narrow_widths["detail"] == pytest.approx(narrow_widths["pane"], abs=1)
        finally:
            context.close()
            browser.close()


def test_every_route_stays_in_the_bounded_workspace_row_with_tall_content(
    gateway_admin_server: str,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(system_chromium()), headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            http_credentials={"username": "admin", "password": "admin-secret"},
        )
        page = context.new_page()

        def assert_active_pane_fills_workspace(pane_selector: str, sidebar_selector: str = "") -> None:
            metrics = page.evaluate("""([paneSelector, sidebarSelector]) => {
                const workspace = document.querySelector('.workspace').getBoundingClientRect();
                const header = document.querySelector('.workspace-header').getBoundingClientRect();
                const pane = document.querySelector(paneSelector).getBoundingClientRect();
                const sidebar = sidebarSelector ? document.querySelector(sidebarSelector).getBoundingClientRect() : null;
                return {
                    workspaceBottom: workspace.bottom,
                    headerBottom: header.bottom,
                    paneTop: pane.top,
                    paneBottom: pane.bottom,
                    paneHeight: pane.height,
                    sidebarBottom: sidebar?.bottom ?? null,
                    sidebarHeight: sidebar?.height ?? null,
                };
            }""", [pane_selector, sidebar_selector])
            assert metrics["paneTop"] == pytest.approx(metrics["headerBottom"], abs=1)
            assert metrics["paneBottom"] == pytest.approx(metrics["workspaceBottom"], abs=1)
            if sidebar_selector:
                assert metrics["sidebarBottom"] == pytest.approx(metrics["workspaceBottom"], abs=1)
                assert metrics["sidebarHeight"] == pytest.approx(metrics["paneHeight"], abs=1)

        try:
            page.goto(f"{gateway_admin_server}/admin")
            expect(page.locator("#client-list .entity-row")).to_have_count(1)
            assert_active_pane_fills_workspace("#clients-pane", "#clients-pane .entity-sidebar")

            page.locator('[data-client-section="calls"]').click()
            expect(page.locator("#client-detail-content tbody tr")).to_have_count(31)
            expect(page.locator('[data-call-error]:text-is("RateLimitError")')).to_be_visible()
            assert_active_pane_fills_workspace("#clients-pane", "#clients-pane .entity-sidebar")

            page.locator('[data-client-section="ledger"]').click()
            expect(page.locator("#client-detail-content tbody tr")).to_have_count(30)
            assert_active_pane_fills_workspace("#clients-pane", "#clients-pane .entity-sidebar")

            for route, pane, sidebar in (
                ("providers", "#providers-pane", "#providers-pane .entity-sidebar"),
                ("pricing", "#pricing-pane", "#pricing-pane .entity-sidebar"),
                ("gateway", "#gateway-pane", ""),
                ("audit", "#audit-pane", ""),
            ):
                page.locator(f'[data-route="{route}"]').click()
                expect(page.locator(pane)).to_be_visible()
                assert_active_pane_fills_workspace(pane, sidebar)

            page.evaluate("""() => {
                const status = document.querySelector('#page-status');
                status.textContent = 'Layout status';
                status.classList.remove('hidden');
            }""")
            status_and_pane = page.evaluate("""() => {
                const status = document.querySelector('#page-status').getBoundingClientRect();
                const pane = document.querySelector('#audit-pane').getBoundingClientRect();
                const workspace = document.querySelector('.workspace').getBoundingClientRect();
                return {statusBottom: status.bottom, paneTop: pane.top, paneBottom: pane.bottom, workspaceBottom: workspace.bottom};
            }""")
            assert status_and_pane["paneTop"] == pytest.approx(status_and_pane["statusBottom"], abs=1)
            assert status_and_pane["paneBottom"] == pytest.approx(status_and_pane["workspaceBottom"], abs=1)
        finally:
            context.close()
            browser.close()


def test_admin_ui_escapes_injected_values_and_keeps_one_time_token_semantics(
    gateway_admin_server: str,
) -> None:
    injected = '<img src=x onerror="window.__gatewayInjected=true">'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(system_chromium()), headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            http_credentials={"username": "admin", "password": "admin-secret"},
        )
        page = context.new_page()
        page.add_init_script("window.__gatewayInjected = false")
        try:
            page.goto(f"{gateway_admin_server}/admin")
            expect(page.locator("#client-list .entity-row")).to_have_count(1)

            page.locator("#client-policy-form input[name=name]").fill(injected)
            page.locator("#client-policy-form textarea[name=note]").fill(injected)
            page.locator("#client-policy-form button[type=submit]").click()
            expect(page.locator("#client-detail .detail-heading h2")).to_have_text(injected)
            assert page.locator("#client-detail img").count() == 0
            assert page.evaluate("window.__gatewayInjected") is False

            page.locator('[data-route="audit"]').click()
            expect(page.locator("#audit-content")).to_contain_text("window.__gatewayInjected=true")
            assert page.locator("#audit-content img").count() == 0
            assert page.evaluate("window.__gatewayInjected") is False

            page.locator('[data-route="clients"]').click()
            page.locator("#view-action").click()
            expect(page.locator("#client-dialog")).to_be_visible()
            expect(page.locator("#client-name")).to_be_focused()
            page.keyboard.press("Escape")
            expect(page.locator("#client-dialog")).to_be_hidden()

            page.locator("#view-action").click()
            page.locator("#client-name").fill("One-time token client")
            page.locator("#client-form button[type=submit]").click()
            expect(page.locator("#token-dialog")).to_be_visible()
            token = page.locator("#token-value").text_content()
            assert token is not None and token.startswith("gpi_")
            page.locator("#token-dialog [data-dialog-close]").first.click()
            expect(page.locator("#token-dialog")).to_be_hidden()
            assert page.locator("#token-value").text_content() == ""
            assert token not in page.content()
        finally:
            context.close()
            browser.close()


def test_admin_api_request_is_injectable_and_serializes_json(
    gateway_admin_server: str,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(system_chromium()), headless=True)
        context = browser.new_context(
            viewport={"width": 1000, "height": 700},
            http_credentials={"username": "admin", "password": "admin-secret"},
        )
        page = context.new_page()
        try:
            page.goto(f"{gateway_admin_server}/admin")
            result = page.evaluate("""async () => {
                const {requestAdminApi} = await import('/admin/api.js');
                let request = null;
                const fakeFetch = async (path, options) => {
                    request = {path, options};
                    return {ok:true, status:200, json:async () => ({item:{ok:true}})};
                };
                const payload = await requestAdminApi(
                    '/admin/api/test',
                    {method:'POST', body:{enabled:true}},
                    fakeFetch,
                );
                return {payload, request};
            }""")
            assert result["payload"] == {"item": {"ok": True}}
            assert result["request"]["path"] == "/admin/api/test"
            assert result["request"]["options"]["body"] == '{"enabled":true}'
            assert result["request"]["options"]["headers"] == {
                "Content-Type": "application/json",
                "X-Grapyth-Admin": "1",
            }
        finally:
            context.close()
            browser.close()


def test_stale_client_route_response_does_not_replace_active_pricing_view(
    gateway_admin_server: str,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(system_chromium()), headless=True)
        context = browser.new_context(
            viewport={"width": 1000, "height": 700},
            http_credentials={"username": "admin", "password": "admin-secret"},
        )
        page = context.new_page()
        try:
            page.goto(f"{gateway_admin_server}/admin#audit")
            expect(page.locator("#audit-pane")).to_be_visible()
            page.evaluate("""() => {
                const originalFetch = window.fetch.bind(window);
                let releaseClientRequest;
                const clientRequestBlock = new Promise((resolve) => {
                    releaseClientRequest = resolve;
                });
                window.__clientRequestStarted = false;
                window.__releaseClientRequest = releaseClientRequest;
                window.fetch = async (...args) => {
                    if (String(args[0]).endsWith('/admin/api/installations')) {
                        window.__clientRequestStarted = true;
                        await clientRequestBlock;
                    }
                    return originalFetch(...args);
                };
            }""")

            page.locator('[data-route="clients"]').click()
            page.wait_for_function("window.__clientRequestStarted")
            page.locator('[data-route="pricing"]').click()
            expect(page.locator("#pricing-list .entity-row")).to_have_count(2)
            expect(page.locator("#view-title")).to_have_text("Pricing")

            page.evaluate("window.__releaseClientRequest()")
            page.wait_for_timeout(100)
            expect(page.locator("#pricing-pane")).to_be_visible()
            expect(page.locator("#clients-pane")).to_be_hidden()
            expect(page.locator("#view-title")).to_have_text("Pricing")
        finally:
            context.close()
            browser.close()
