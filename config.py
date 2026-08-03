from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .domain import PricingRates

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_PRICING_VERSION = "grapyth-managed-2026-08-03"
DEFAULT_RATES = PricingRates(
    input=Decimal("2.00"),
    cached=Decimal("0.20"),
    cache_write=Decimal("2.50"),
    output=Decimal("12"),
)


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    data_root: Path
    admin_password: str = field(repr=False)
    master_key: str = field(default="", repr=False)
    requests_per_minute: int = 20
    global_concurrent_calls: int = 4
    provider_timeout_seconds: float = 180.0
    provider_max_retries: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_root", Path(self.data_root))
        if self.requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        if self.global_concurrent_calls < 1:
            raise ValueError("global_concurrent_calls must be at least 1")
        if not math.isfinite(self.provider_timeout_seconds) or self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be a positive finite number")
        if self.provider_max_retries < 0:
            raise ValueError("provider_max_retries cannot be negative")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "GatewaySettings":
        source = environment if environment is not None else os.environ
        errors: list[str] = []

        requests_per_minute = _parse_int(
            source,
            "GRAPYTH_GATEWAY_REQUESTS_PER_MINUTE",
            default=20,
            minimum=1,
            errors=errors,
        )
        global_concurrent_calls = _parse_int(
            source,
            "GRAPYTH_GATEWAY_GLOBAL_CONCURRENT_CALLS",
            default=4,
            minimum=1,
            errors=errors,
        )
        provider_max_retries = _parse_int(
            source,
            "GRAPYTH_GATEWAY_PROVIDER_MAX_RETRIES",
            default=1,
            minimum=0,
            errors=errors,
        )
        provider_timeout_seconds = _parse_float(
            source,
            "GRAPYTH_GATEWAY_PROVIDER_TIMEOUT_SECONDS",
            default=180.0,
            minimum_exclusive=0,
            errors=errors,
        )
        if errors:
            raise ValueError("Invalid Gateway configuration: " + "; ".join(errors))

        return cls(
            data_root=Path(
                source.get(
                    "GRAPYTH_GATEWAY_DATA_ROOT",
                    str(PROJECT_ROOT / "var" / "gateway"),
                )
            ),
            admin_password=source.get("GRAPYTH_GATEWAY_ADMIN_PASSWORD", ""),
            master_key=source.get("GRAPYTH_GATEWAY_MASTER_KEY", ""),
            requests_per_minute=requests_per_minute,
            global_concurrent_calls=global_concurrent_calls,
            provider_timeout_seconds=provider_timeout_seconds,
            provider_max_retries=provider_max_retries,
        )


def _parse_int(
    source: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    errors: list[str],
) -> int:
    raw = source.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        errors.append(f"{name} must be an integer")
        return default
    if value < minimum:
        errors.append(f"{name} must be at least {minimum}")
        return default
    return value


def _parse_float(
    source: Mapping[str, str],
    name: str,
    *,
    default: float,
    minimum_exclusive: float,
    errors: list[str],
) -> float:
    raw = source.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a number")
        return default
    if not math.isfinite(value) or value <= minimum_exclusive:
        errors.append(f"{name} must be greater than {minimum_exclusive:g}")
        return default
    return value
