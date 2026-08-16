"""Staying inside what chat platforms allow.

Both Discord and Telegram will tell you to slow down, and both remember that they had to.
Discord in particular counts rejected requests against an allowance that, once spent, gets
your address blocked at the edge rather than by the API — so the goal is not to react well
to being throttled, it is to not get throttled.

The clock is injectable so the tests can prove the spacing without waiting for it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    """Lets through `rate` requests a second, with a little burst allowance."""

    def __init__(
        self,
        rate_per_s: float,
        capacity: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        self._rate = rate_per_s
        self._capacity = capacity if capacity is not None else max(1.0, rate_per_s)
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> float:
        """Wait until `tokens` are available. Returns how long it waited."""
        async with self._lock:
            waited = 0.0
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                shortfall = tokens - self._tokens
                delay = shortfall / self._rate
                waited += delay
                await self._sleep(delay)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)


class Gate:
    """A hold that can be closed for a while.

    Used when a platform says "stop, globally": one destination hitting a global limit has
    to pause every other destination sharing that connection, not just itself.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._open_at = 0.0

    def close_for(self, seconds: float) -> None:
        self._open_at = max(self._open_at, self._clock() + seconds)

    @property
    def wait_s(self) -> float:
        return max(0.0, self._open_at - self._clock())

    @property
    def is_closed(self) -> bool:
        return self.wait_s > 0
