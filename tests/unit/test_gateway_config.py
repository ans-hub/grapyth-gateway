from __future__ import annotations

from pathlib import Path

import pytest

from gateway import config as gateway_config
from gateway.api.schemas import ProviderCredentialCreateRequest, parse_model
from gateway.config import GatewaySettings


def test_default_data_root_is_inside_the_gateway_repository() -> None:
    settings = GatewaySettings.from_environment({})

    assert settings.data_root == gateway_config.PROJECT_ROOT / "var" / "gateway"
    assert gateway_config.PROJECT_ROOT == Path(gateway_config.__file__).resolve().parent


def test_environment_diagnostics_report_every_invalid_non_secret_value() -> None:
    with pytest.raises(ValueError) as invalid:
        GatewaySettings.from_environment(
            {
                "GRAPYTH_GATEWAY_ADMIN_PASSWORD": "must-not-appear",
                "GRAPYTH_GATEWAY_MASTER_KEY": "must-not-appear-either",
                "GRAPYTH_GATEWAY_REQUESTS_PER_MINUTE": "zero",
                "GRAPYTH_GATEWAY_GLOBAL_CONCURRENT_CALLS": "0",
                "GRAPYTH_GATEWAY_PROVIDER_TIMEOUT_SECONDS": "nan",
                "GRAPYTH_GATEWAY_PROVIDER_MAX_RETRIES": "-1",
            }
        )

    message = str(invalid.value)
    for setting in (
        "GRAPYTH_GATEWAY_REQUESTS_PER_MINUTE",
        "GRAPYTH_GATEWAY_GLOBAL_CONCURRENT_CALLS",
        "GRAPYTH_GATEWAY_PROVIDER_TIMEOUT_SECONDS",
        "GRAPYTH_GATEWAY_PROVIDER_MAX_RETRIES",
    ):
        assert setting in message
    assert "must-not-appear" not in message


def test_configuration_and_secret_boundary_models_hide_secrets(tmp_path: Path) -> None:
    settings = GatewaySettings(tmp_path, "admin-private", master_key="master-private")
    request = parse_model(
        ProviderCredentialCreateRequest,
        {"name": "Provider", "apiKey": "sk-private", "ignored": "value"},
    )

    assert "admin-private" not in repr(settings)
    assert "master-private" not in repr(settings)
    assert "sk-private" not in repr(request)
    assert request.model_dump().keys() == {"name", "api_key"}
