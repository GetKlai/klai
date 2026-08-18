"""Shared async token-bucket rate limiter for Klai LLM callers.

See the package README for the incident history this class fixes (twice,
in two independent services, before being extracted here as the single
shared implementation).
"""

from __future__ import annotations

import asyncio

__all__ = ["TokenBucketLimiter"]


class TokenBucketLimiter:
    """Async token bucket: ``rate`` sustained calls/sec with ``capacity`` burst.

    ``acquire()`` returns immediately while burst tokens remain and sleeps
    just long enough for the next token otherwise. The sleep happens while
    holding the internal lock, so concurrent acquirers are served strictly
    in arrival order.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate}")
        self._rate = rate
        self._capacity = max(1.0, capacity if capacity is not None else rate)
        self._tokens = self._capacity
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            if self._last is not None:
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0
            await asyncio.sleep(wait)
            self._last = loop.time()
