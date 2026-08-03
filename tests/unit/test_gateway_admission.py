from __future__ import annotations

import pytest

from gateway.errors import GatewayError
from gateway.services.admission import (
    MAX_TRACKED_INSTALLATIONS,
    TRACKING_IDLE_SECONDS,
    AdmissionController,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def controller(clock: Clock, *, rate: int = 2) -> AdmissionController:
    return AdmissionController(
        requests_per_minute=rate,
        global_concurrent_calls=1,
        monotonic=clock,
    )


def test_rate_window_remains_enforced() -> None:
    clock = Clock()
    admission = controller(clock)

    admission.enforce_rate_limit("installation-1")
    admission.enforce_rate_limit("installation-1")

    with pytest.raises(GatewayError) as error:
        admission.enforce_rate_limit("installation-1")
    assert error.value.code == "installation_rate_limited"

    clock.now = 60.0
    admission.enforce_rate_limit("installation-1")


def test_inactive_tracking_is_evicted_and_total_tracking_is_bounded() -> None:
    clock = Clock()
    admission = controller(clock, rate=1)

    admission.enforce_rate_limit("inactive")
    clock.now = TRACKING_IDLE_SECONDS
    admission.enforce_rate_limit("current")
    assert "inactive" not in admission._last_seen

    for number in range(MAX_TRACKED_INSTALLATIONS + 100):
        admission.enforce_rate_limit(f"installation-{number}")

    assert admission.tracked_installation_count <= MAX_TRACKED_INSTALLATIONS
    assert len(admission._request_times) <= MAX_TRACKED_INSTALLATIONS


def test_active_installation_lock_is_never_evicted() -> None:
    clock = Clock()
    admission = controller(clock, rate=1)

    with admission.installation_slot("active"):
        active_lock = admission._installation_locks["active"]
        clock.now = TRACKING_IDLE_SECONDS
        for number in range(MAX_TRACKED_INSTALLATIONS + 1):
            admission.enforce_rate_limit(f"installation-{number}")
        assert admission._installation_locks["active"] is active_lock
        assert active_lock.locked()

    with admission.installation_slot("active"):
        assert admission._installation_locks["active"] is active_lock
