"""A cap on connection tests per person: each one makes the agent call HighLevel, so pressing Test over and over is
held back after a few a minute. Kept in the admin app's memory: it starts empty when the app restarts,
which is enough for a handful of support people and one admin container."""

import threading
import time
from collections import deque
from collections.abc import Callable

CHECKS_PER_MINUTE = 5


class CheckCap:
    def __init__(self, per_minute: int = CHECKS_PER_MINUTE, clock: Callable[[], float] = time.monotonic):
        self._per_minute = per_minute
        self._clock = clock
        self._checks: dict[int, deque[float]] = {}  # staff id -> times of their checks in the last minute
        self._lock = threading.Lock()  # pages run in a thread pool: two presses can arrive at once

    def allow(self, staff_id: int) -> bool:
        """True, and counted, when this person has had fewer than per_minute checks in the last 60 s. A refusal
        isn't counted, so pressing again while held back doesn't make the wait longer."""
        now = self._clock()
        with self._lock:
            checks = self._checks.setdefault(staff_id, deque())
            while checks and now - checks[0] >= 60:
                checks.popleft()
            if len(checks) >= self._per_minute:
                return False
            checks.append(now)
            return True


_cap = CheckCap()


def get_check_cap() -> CheckCap:
    return _cap
