from __future__ import annotations

import asyncio
import time
from functools import wraps


class ResettableLimits:
    """At most `calls` calls per `period` seconds, for coroutines (asyncio).

    The semantics of the ratelimit-based limiter of the threaded versions (0.3.x up to 0.3.28):
    a fixed window opens with the first call after the previous one has passed; a call beyond
    `calls` in the window waits for its end. `add_period` lengthens the window (backoff after a
    429 / 5xx, up to `max`), `set_period` restores it, `reset` clears the window and wakes the
    calls waiting in it. Instances are shared by all config entries (one source IP).
    """

    # How often a waiting call checks for a reset (it wakes within this many seconds).
    _POLL = 0.5

    def __init__(self, calls: int = 15, period: float = 900, max: float | None = None) -> None:  # noqa: A002
        self.calls = calls
        self.period = self._init_period = period
        self._max = max if max is not None else period
        self.num_calls = 0
        self.last_reset = self.clock()
        self._generation = 0

    @staticmethod
    def clock() -> float:
        return time.monotonic()

    def _remaining(self) -> float:
        return self.period - (self.clock() - self.last_reset)

    async def acquire(self) -> None:
        while True:
            remaining = self._remaining()
            if remaining <= 0:
                self.num_calls = 0
                self.last_reset = self.clock()
            if self.num_calls < self.calls:
                self.num_calls += 1
                return
            generation = self._generation
            deadline = self.clock() + remaining
            while self._generation == generation and (left := deadline - self.clock()) > 0:
                await asyncio.sleep(min(left, self._POLL))

    def __call__(self, func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            await self.acquire()
            return await func(*args, **kwargs)
        return wrapper

    def set_period(self, period: float | None = None) -> None:
        self.period = period or self._init_period

    def add_period(self, period: float) -> float:
        self.period = min(self.period + period, self._max)
        return self.period

    def reset(self) -> None:
        self.period = self._init_period
        self.num_calls = 0
        self.last_reset = self.clock()
        self._generation += 1
