from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..errors import GatewayError


RATE_WINDOW_SECONDS = 60.0
TRACKING_IDLE_SECONDS = 300.0
MAX_TRACKED_INSTALLATIONS = 4096


class AdmissionController:
    """Single-process rate and concurrency admission for managed calls."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        global_concurrent_calls: int,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        if global_concurrent_calls < 1:
            raise ValueError("global_concurrent_calls must be at least 1")
        self.requests_per_minute = requests_per_minute
        self._monotonic = monotonic
        self._guard = threading.Lock()
        self._installation_locks: dict[str, threading.Lock] = {}
        self._request_times: dict[str, deque[float]] = {}
        self._last_seen: OrderedDict[str, float] = OrderedDict()
        self._global_slots = threading.BoundedSemaphore(global_concurrent_calls)

    def enforce_rate_limit(self, installation_id: str) -> None:
        now = self._monotonic()
        cutoff = now - RATE_WINDOW_SECONDS
        with self._guard:
            self._touch_locked(installation_id, now)
            self._evict_inactive_locked(now, protected=installation_id)
            request_times = self._request_times.setdefault(installation_id, deque())
            while request_times and request_times[0] <= cutoff:
                request_times.popleft()
            if len(request_times) >= self.requests_per_minute:
                retry_after = max(
                    1,
                    int(RATE_WINDOW_SECONDS - (now - request_times[0]) + 0.999),
                )
                raise GatewayError(
                    429,
                    "installation_rate_limited",
                    "This installation has sent too many AI requests",
                    details={"retryAfterSeconds": retry_after},
                )
            request_times.append(now)

    @contextmanager
    def installation_slot(self, installation_id: str) -> Iterator[None]:
        now = self._monotonic()
        with self._guard:
            self._touch_locked(installation_id, now)
            self._evict_inactive_locked(now, protected=installation_id)
            installation_lock = self._installation_locks.setdefault(
                installation_id, threading.Lock()
            )
            installation_acquired = installation_lock.acquire(blocking=False)
        if not installation_acquired:
            raise GatewayError(
                429,
                "installation_concurrency_limited",
                "Another AI request is already running for this installation",
                details={"retryAfterSeconds": 1},
            )
        global_acquired = self._global_slots.acquire(blocking=False)
        if not global_acquired:
            installation_lock.release()
            self._mark_seen(installation_id)
            raise GatewayError(
                503,
                "gateway_at_capacity",
                "The AI gateway is at capacity; retry shortly",
                details={"retryAfterSeconds": 1},
            )
        try:
            yield
        finally:
            self._global_slots.release()
            installation_lock.release()
            self._mark_seen(installation_id)

    @property
    def tracked_installation_count(self) -> int:
        with self._guard:
            return len(self._last_seen)

    def _mark_seen(self, installation_id: str) -> None:
        with self._guard:
            self._touch_locked(installation_id, self._monotonic())

    def _touch_locked(self, installation_id: str, now: float) -> None:
        self._last_seen[installation_id] = now
        self._last_seen.move_to_end(installation_id)

    def _evict_inactive_locked(self, now: float, *, protected: str) -> None:
        required_capacity_evictions = max(
            0, len(self._last_seen) - MAX_TRACKED_INSTALLATIONS
        )
        removable: list[str] = []
        for installation_id, last_seen in self._last_seen.items():
            if installation_id == protected:
                continue
            idle = now - last_seen >= TRACKING_IDLE_SECONDS
            needed_for_capacity = len(removable) < required_capacity_evictions
            if not idle and not needed_for_capacity:
                break
            installation_lock = self._installation_locks.get(installation_id)
            if installation_lock is not None and installation_lock.locked():
                continue
            removable.append(installation_id)
        for installation_id in removable:
            self._installation_locks.pop(installation_id, None)
            self._request_times.pop(installation_id, None)
            self._last_seen.pop(installation_id, None)
