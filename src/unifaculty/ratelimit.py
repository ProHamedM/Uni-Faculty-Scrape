"""Per-host politeness delays and a requests-per-minute limiter for LLM APIs."""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from typing import Callable

from unifaculty.logs import TRACE, get_logger, log_event

log = get_logger("ratelimit")

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class HostRateLimiter:
    """One request at a time per host, at least ``delay`` seconds apart (+ small jitter)."""

    def __init__(self, clock: Clock = time.monotonic, sleep: Sleep = time.sleep, jitter: float = 0.4,
                 rng: random.Random | None = None):
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._last: dict[str, float] = {}
        self._rng = rng or random.Random()
        self.total_waited = 0.0

    def wait(self, host: str, delay: float) -> float:
        now = self._clock()
        last = self._last.get(host)
        waited = 0.0
        if last is not None:
            target = last + delay + self._rng.uniform(0, self._jitter)
            waited = max(0.0, target - now)
            if waited > 0:
                log_event(log, TRACE, "ratelimit.wait", host=host, seconds=round(waited, 2), delay=delay)
                self._sleep(waited)
        self._last[host] = self._clock()
        self.total_waited += waited
        return waited

    def pause(self, host: str, seconds: float, reason: str) -> None:
        log_event(log, logging.INFO, "ratelimit.pause", host=host, seconds=round(seconds, 1), reason=reason)
        self._sleep(seconds)
        self.total_waited += seconds
        self._last[host] = self._clock()


class RequestsPerMinute:
    def __init__(self, rpm: float, clock: Clock = time.monotonic, sleep: Sleep = time.sleep):
        self.rpm = max(0.1, float(rpm))
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> float:
        window = 60.0
        now = self._clock()
        while self._calls and now - self._calls[0] >= window:
            self._calls.popleft()
        waited = 0.0
        if len(self._calls) >= self.rpm:
            waited = window - (now - self._calls[0]) + 0.05
            log_event(log, logging.DEBUG, "llm.rpm_wait", seconds=round(waited, 2), rpm=self.rpm)
            self._sleep(waited)
            now = self._clock()
            while self._calls and now - self._calls[0] >= window:
                self._calls.popleft()
        self._calls.append(self._clock())
        return waited
