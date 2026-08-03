from __future__ import annotations

import ast
from pathlib import Path

import gateway.server as gateway_server
from gateway.api import admin, health, public
from gateway.config import GatewaySettings


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE_DIRECTORIES = ("api", "persistence", "providers", "services", "stores")


def imported_roots(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def imported_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(("." * node.level) + (node.module or ""))
    return modules


def runtime_python_sources(gateway_root: Path) -> list[Path]:
    sources = list(gateway_root.glob("*.py"))
    for directory in RUNTIME_SOURCE_DIRECTORIES:
        sources.extend((gateway_root / directory).rglob("*.py"))
    return sorted(source for source in sources if source.is_file())


def route_contract(router) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in router.routes
        for method in route.methods or set()
    }


def test_server_delegates_to_the_gateway_composition_root(monkeypatch) -> None:
    settings = GatewaySettings(ROOT / "var" / "gateway-test", "admin")
    provider = object()
    application = object()
    received = {}

    def create_gateway_app(settings_value, provider_value, *, provider_factory):
        received.update(
            {
                "settings": settings_value,
                "provider": provider_value,
                "providerFactory": provider_factory,
            }
        )
        return application

    monkeypatch.setattr(gateway_server, "create_gateway_app", create_gateway_app)

    assert gateway_server.create_app(settings, provider) is application
    assert received == {
        "settings": settings,
        "provider": provider,
        "providerFactory": gateway_server.OpenAIProvider,
    }


def test_boundary_routers_own_the_public_http_contract() -> None:
    assert route_contract(public.router) == {
        ("POST", "/v1/responses"),
        ("GET", "/v1/account"),
        ("GET", "/v1/account/users"),
        ("PUT", "/v1/account/users/{user_id}/limit"),
    }
    assert route_contract(health.router) == {
        ("GET", "/health"),
        ("GET", "/ready"),
    }
    assert route_contract(admin.router) == {
        ("GET", "/admin"),
        ("GET", "/admin/{asset_name}"),
        ("GET", "/admin/api/installations"),
        ("POST", "/admin/api/installations"),
        ("PATCH", "/admin/api/installations/{installation_id}"),
        ("POST", "/admin/api/installations/{installation_id}/credit"),
        ("POST", "/admin/api/installations/{installation_id}/rotate-token"),
        ("GET", "/admin/api/installations/{installation_id}/calls"),
        ("GET", "/admin/api/installations/{installation_id}/ledger"),
        ("GET", "/admin/api/provider-credentials"),
        ("POST", "/admin/api/provider-credentials"),
        ("PATCH", "/admin/api/provider-credentials/{credential_id}"),
        ("POST", "/admin/api/provider-credentials/{credential_id}/test"),
        ("GET", "/admin/api/pricing-plans"),
        ("POST", "/admin/api/pricing-plans"),
        ("PATCH", "/admin/api/pricing-plans/{plan_id}"),
        ("GET", "/admin/api/settings"),
        ("PATCH", "/admin/api/settings"),
        ("GET", "/admin/api/audit"),
    }


def test_transport_and_managed_call_core_do_not_import_infrastructure_adapters() -> None:
    gateway_root = ROOT
    http_sources = (
        gateway_root / "api" / "public.py",
        gateway_root / "api" / "admin.py",
        gateway_root / "api" / "health.py",
    )
    assert all("sqlite3" not in imported_roots(source) for source in http_sources)
    calls_source = gateway_root / "services" / "calls.py"
    assert imported_roots(calls_source).isdisjoint(
        {"fastapi", "openai", "sqlite3"}
    )
    assert "..database" not in imported_modules(calls_source)
    assert imported_roots(gateway_root / "providers" / "base.py").isdisjoint(
        {"fastapi", "openai"}
    )


def test_openai_sdk_is_confined_to_the_provider_adapter() -> None:
    gateway_root = ROOT
    openai_importers = [
        source.relative_to(gateway_root).as_posix()
        for source in runtime_python_sources(gateway_root)
        if "openai" in imported_roots(source)
    ]

    assert openai_importers == ["providers/openai.py"]


def test_cryptography_is_confined_to_the_fernet_secret_adapter() -> None:
    gateway_root = ROOT
    cryptography_importers = {
        source.relative_to(gateway_root).as_posix(): {
            module for module in imported_modules(source) if module.startswith("cryptography")
        }
        for source in runtime_python_sources(gateway_root)
        if "cryptography" in imported_roots(source)
    }

    assert cryptography_importers == {"crypto.py": {"cryptography.fernet"}}


def test_runtime_source_scan_excludes_local_environments_and_build_outputs(tmp_path: Path) -> None:
    (tmp_path / "providers").mkdir()
    (tmp_path / ".venv" / "openai").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "gateway").mkdir(parents=True)
    (tmp_path / "server.py").write_text("", encoding="utf-8")
    (tmp_path / "providers" / "openai.py").write_text("", encoding="utf-8")
    (tmp_path / ".venv" / "openai" / "client.py").write_text("", encoding="utf-8")
    (tmp_path / "build" / "lib" / "gateway" / "copy.py").write_text("", encoding="utf-8")

    assert [
        source.relative_to(tmp_path).as_posix()
        for source in runtime_python_sources(tmp_path)
    ] == ["providers/openai.py", "server.py"]


def test_standalone_quickstart_documents_the_supported_operational_boundary() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())

    for required_text in (
        "Run exactly one Uvicorn worker in one Gateway container or replica",
        "TLS-terminating reverse proxy",
        "Store the authoritative master key in protected off-host storage",
        "python -m gateway.manage backup",
        "python -m gateway.manage verify",
        "python -m gateway.manage restore",
        "restoring the matching pre-upgrade backup",
        "service_tier=default",
        "CVE-2026-69247",
    ):
        assert required_text in readme
