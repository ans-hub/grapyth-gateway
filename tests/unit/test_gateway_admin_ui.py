import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "admin"


def png_dimensions(path: Path) -> tuple[int, int]:
    content = path.read_bytes()
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")


def javascript_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(ADMIN.glob("*.js"))
    )


def test_gateway_package_includes_brand_logo() -> None:
    package_configuration = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    package_data = package_configuration["tool"]["setuptools"]["package-data"]["gateway"]

    for logo_name in ("logo.png", "logo-single.png"):
        assert (ADMIN / logo_name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert png_dimensions(ADMIN / "logo.png") == (967, 267)
    assert png_dimensions(ADMIN / "logo-single.png") == (318, 318)
    assert "admin/*.png" in package_data


def test_gateway_admin_uses_grapyth_sidebar_and_separate_views() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    css = (ADMIN / "admin.css").read_text(encoding="utf-8")

    for route in ("clients", "providers", "pricing", "gateway", "audit"):
        assert f'data-route="{route}"' in html
        assert f'data-pane="{route}"' in html

    assert 'class="sidebar"' in html
    assert 'class="sidebar-brand"' in html
    assert 'class="brand-picture"' in html
    assert 'media="(max-width:760px)" srcset="/admin/logo-single.png"' in html
    assert 'class="brand-logo"' in html
    assert 'src="/admin/logo.png"' in html
    assert 'alt="Grapyth Gateway"' in html
    assert "brand-mark-placeholder" not in html
    assert 'href="/admin/admin.css"' in html
    assert 'type="module" src="/admin/admin.js"' in html
    expected_palette = {
        "bg": "#16181c",
        "surface": "#1a1b1f",
        "surface2": "#202125",
        "border": "#303136",
        "text": "#e1e4ed",
        "text2": "#8b90a0",
        "accent": "#8eb2ff",
        "accent2": "#ff6c8c",
        "green": "#63a482",
        "yellow": "#f0c456",
        "orange": "#f09c56",
        "red": "#ff6c6c",
        "cyan": "#56d4f0",
        "purple": "#b56cff",
    }
    for name, value in expected_palette.items():
        assert f"--{name}:{value}" in css
    assert "--sidebar-width:286px" in css
    assert ".brand-logo" in css
    assert ".entity-pane" in css
    assert ".detail-tabs" in css


def test_gateway_admin_has_concise_footer_and_no_redundant_list_headers() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    css = (ADMIN / "admin.css").read_text(encoding="utf-8")
    scripts = javascript_source()

    assert "Gateway admin" not in html
    assert "<strong>Admin</strong>" in html
    assert "grid-template-rows:auto minmax(0,1fr);" in css
    assert ".entity-sidebar>header" not in css
    for summary_id in ("client-summary", "provider-summary", "pricing-summary"):
        assert summary_id not in html + scripts


def test_gateway_entity_views_use_owned_modules_and_native_templates() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    css = (ADMIN / "admin.css").read_text(encoding="utf-8")
    entrypoint = (ADMIN / "admin.js").read_text(encoding="utf-8")

    for entity, pane in (
        ("client", "clients"),
        ("provider", "providers"),
        ("pricing", "pricing"),
    ):
        assert f'id="{pane}-pane" class="view-pane entity-pane' in html
        assert f'id="{entity}-list" class="entity-list"' in html
        assert f'id="{entity}-detail" class="entity-detail"' in html
        assert f'from "./{pane}.js"' in entrypoint

    for template_id in (
        "empty-state-template",
        "entity-row-template",
        "client-detail-template",
        "provider-detail-template",
        "pricing-detail-template",
        "audit-row-template",
    ):
        assert f'<template id="{template_id}">' in html

    assert "max-width:1920px" in css
    assert ".detail-content {\n  display:grid;\n  gap:13px;\n  width:100%;" in css
    assert ".page-content {\n  display:grid;\n  align-content:start;\n  gap:12px;\n  width:100%;" in css
    for obsolete_width in (
        "  max-width:760px;",
        "  max-width:980px;",
        "  max-width:1000px;",
        "  max-width:1180px;",
    ):
        assert obsolete_width not in css


def test_gateway_workspace_pins_every_view_to_the_bounded_content_row() -> None:
    css = (ADMIN / "admin.css").read_text(encoding="utf-8")

    assert ".workspace-header {\n  grid-row:1;" in css
    assert ".page-status {\n  grid-row:2;" in css
    assert ".view-pane {\n  grid-row:3;" in css
    assert "grid-template-rows:62px auto minmax(0,1fr);" in css


def test_gateway_admin_has_explicit_client_policy_and_no_defaults_ui() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    scripts = javascript_source()

    assert 'id="client-provider"' in html
    assert 'id="client-plan"' in html
    assert 'id="client-reasoning"' in html
    assert 'id="client-billing"' in html
    assert "providerCredentialId" in scripts
    assert "pricingPlanId" in scripts
    assert "reasoningEffort" in scripts
    assert "billingMode" in scripts
    assert "/admin/api/defaults" not in html + scripts
    assert "Defaults" not in html + scripts


def test_gateway_admin_uses_accessible_dialogs() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    scripts = javascript_source()

    for dialog_id in (
        "client-dialog",
        "provider-dialog",
        "provider-test-dialog",
        "pricing-dialog",
        "credit-dialog",
        "confirm-dialog",
        "token-dialog",
    ):
        assert f'id="{dialog_id}"' in html

    assert "prompt(" not in scripts
    assert "alert(" not in scripts
    assert "confirm(" not in scripts
    assert 'aria-label="Gateway sections"' in html
    assert 'aria-live="polite"' in html


def test_dynamic_admin_values_use_dom_text_boundaries() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    scripts = javascript_source()
    ui = (ADMIN / "ui.js").read_text(encoding="utf-8")

    assert "innerHTML" not in scripts
    assert "insertAdjacentHTML" not in scripts
    assert "escapeHtml" not in scripts
    assert "const esc" not in scripts
    assert "document.querySelector =" not in scripts
    assert "textContent" in scripts
    assert "replaceChildren" in scripts
    assert 'document.getElementById("token-value").textContent = ""' in ui
    assert 'id="call-details-dialog"' in html
    assert 'data-call-request-kind' in html
    assert 'data-call-outcome-kind' in html
    assert 'data-view-call-details' in html
    assert 'data-call-margin-cell' in html
    assert 'id="copy-call-references"' in html
    assert 'data-call-error' not in html
    assert 'data-call-reference-summary' not in html
    assert "formatDiagnosticCode" not in scripts
    assert "call.gateway_error_code || call.error_code" in scripts
    assert "call.failure?.provider" not in scripts
    assert "const provider = failure.provider || {}" in scripts
    assert 'return "Waiting for response"' in scripts
    assert '|| "Unknown"' in scripts
    assert 'call.status === "running"' in scripts
    assert 'loadClientActivity("calls", isCurrent, { showLoading: false })' in scripts
    assert "Call references copied to clipboard" in scripts
    assert "syncOpenCallDetails" in scripts


def test_screen_create_actions_have_one_owner() -> None:
    entrypoint = (ADMIN / "admin.js").read_text(encoding="utf-8")
    screen_modules = {
        "data-create-client": ADMIN / "clients.js",
        "data-create-provider": ADMIN / "providers.js",
        "data-create-plan": ADMIN / "pricing.js",
    }

    for selector, module_path in screen_modules.items():
        assert selector not in entrypoint
        assert selector in module_path.read_text(encoding="utf-8")


def test_handwritten_admin_sources_are_not_over_compressed() -> None:
    source_files = [
        *ADMIN.glob("*.js"),
        ADMIN / "admin.css",
        ADMIN / "index.html",
    ]

    for path in source_files:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            assert len(line) <= 120, (
                f"{path.relative_to(ROOT)}:{line_number} exceeds 120 characters"
            )
