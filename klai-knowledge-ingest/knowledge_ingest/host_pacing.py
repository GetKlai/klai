"""Process-wide weighted pacing for concurrent crawls of one external host.

The registry is intentionally in memory.  ``knowledge-ingest`` currently runs
its FastAPI app and all Procrastinate workers in one Python process.  Multiple
Uvicorn workers or service replicas require replacing this registry with a
distributed limiter before rollout.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from knowledge_ingest.domain_selectors import extract_domain

_BURST_WINDOW_SECONDS = 10.0
# crawl4ai 0.8 rejects POST /crawl payloads containing more than 100 URLs.
# Keep that transport ceiling next to the burst calculation that enforces it;
# raise it only in lock-step with a verified crawl4ai schema relaxation.
_MAX_BURST_SIZE = 100


def ensure_single_process_host_pacing(
    *,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> None:
    """Refuse startup when an in-memory host gate would be process-local.

    Resolve worker count with Uvicorn's precedence: the last explicit
    ``--workers`` flag, then ``UVICORN_WORKERS``, then ``WEB_CONCURRENCY``.
    Replicas cannot be detected from inside one container; deployment
    configuration must keep replicas at one until this registry is replaced
    by a distributed limiter.
    """
    active_environ = os.environ if environ is None else environ
    active_argv = sys.argv if argv is None else argv
    configured_worker: tuple[str, str] | None = None

    for index, argument in enumerate(active_argv):
        if argument.startswith("--workers="):
            configured_worker = ("--workers", argument.split("=", 1)[1])
        elif argument == "--workers":
            value = active_argv[index + 1] if index + 1 < len(active_argv) else ""
            configured_worker = ("--workers", value)

    if configured_worker is None:
        for name in ("UVICORN_WORKERS", "WEB_CONCURRENCY"):
            value = active_environ.get(name)
            if value:
                configured_worker = (name, value)
                break

    if configured_worker is None:
        return

    source, raw_value = configured_worker
    try:
        worker_count = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "Host pacing requires single-process knowledge-ingest; "
            f"cannot verify {source}={raw_value!r} as a worker count."
        ) from exc
    if worker_count > 1:
        rendered = f"{source}={raw_value}" if source != "--workers" else f"--workers={raw_value}"
        raise RuntimeError(
            "Host pacing requires single-process knowledge-ingest; "
            f"detected {rendered}. Use exactly one web worker until "
            "HostGateRegistry has a distributed backend."
        )


def crawl_gate_key(url: str) -> str:
    """Return the live pacing key, collapsing only the leading ``www.`` alias.

    ``extract_domain`` remains the exact persisted selector/rate-state key.
    Reusing its normalization here keeps case, IDNA, trailing-dot, and port
    behavior aligned without changing that storage contract.
    """
    domain = extract_domain(url)
    return domain[4:] if domain.startswith("www.") else domain


@dataclass
class _HostGate:
    rates: dict[object, float | None] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    previous_start: float | None = None
    previous_weight: int = 0

    @property
    def effective_rate(self) -> float | None:
        limited_rates = [rate for rate in self.rates.values() if rate is not None]
        return min(limited_rates) if limited_rates else None


class HostPacingSession:
    """One active crawl's handle into a shared host pacing schedule."""

    def __init__(
        self, registry: HostGateRegistry, key: str, gate: _HostGate, token: object
    ) -> None:
        self._registry = registry
        self.key = key
        self._gate = gate
        self._token = token
        self._closed = False

    async def acquire(self, max_weight: int) -> int:
        """Wait for and reserve the next burst, returning its allowed URL count."""
        if self._closed:
            raise RuntimeError("host pacing session is closed")
        if max_weight < 1:
            raise ValueError("max_weight must be at least 1")
        return await self._registry._acquire(self._gate, max_weight)

    def update_rate(self, rate_limit: float | None) -> None:
        """Change this crawl's advertised rate for every future shared grant."""
        if self._closed:
            raise RuntimeError("host pacing session is closed")
        self._gate.rates[self._token] = _validated_rate(rate_limit)

    def _close(self) -> None:
        self._closed = True


class HostGateRegistry:
    """Own one fair weighted schedule per normalized crawl host."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._gates: dict[str, _HostGate] = {}

    @property
    def active_gate_count(self) -> int:
        return len(self._gates)

    @asynccontextmanager
    async def session(self, url: str, rate_limit: float | None) -> AsyncIterator[HostPacingSession]:
        """Register an active crawl and remove its gate deterministically on exit."""
        key = crawl_gate_key(url)
        token = object()
        gate = self._gates.setdefault(key, _HostGate())
        gate.rates[token] = _validated_rate(rate_limit)
        session = HostPacingSession(self, key, gate, token)
        try:
            yield session
        finally:
            session._close()
            gate.rates.pop(token, None)
            if not gate.rates:
                self._gates.pop(key, None)

    async def _acquire(self, gate: _HostGate, max_weight: int) -> int:
        # asyncio.Lock acquisition is FIFO-fair. Holding it across the pacing
        # sleep prevents a later crawl from jumping ahead, while rate updates
        # remain synchronous and can still change ``gate.rates`` during sleep.
        async with gate.lock:
            while True:
                rate_limit = gate.effective_rate
                if rate_limit is None:
                    weight = min(max_weight, _MAX_BURST_SIZE)
                    break

                weight = min(max_weight, _burst_size(rate_limit))
                if gate.previous_start is None:
                    break

                elapsed = self._monotonic() - gate.previous_start
                remaining = (gate.previous_weight / rate_limit) - elapsed
                if remaining <= 0:
                    break
                await self._sleep(remaining)
                # Re-evaluate the effective rate and burst after sleeping: an
                # overlapping crawl may have joined or slowed down meanwhile.
                # When the rate did not change, the awaited sleep itself is
                # the pacing guarantee. This also keeps the clock/sleep seam
                # usable in tests where a no-op sleep intentionally leaves a
                # frozen monotonic clock.
                if gate.effective_rate == rate_limit:
                    weight = min(max_weight, _burst_size(rate_limit))
                    break

            gate.previous_start = self._monotonic()
            gate.previous_weight = weight
            return weight


def _validated_rate(rate_limit: float | None) -> float | None:
    if rate_limit is not None and rate_limit <= 0:
        raise ValueError("rate_limit must be positive")
    return rate_limit


def _burst_size(rate_limit: float) -> int:
    return max(
        1,
        min(_MAX_BURST_SIZE, int(rate_limit * _BURST_WINDOW_SECONDS + 0.5)),
    )
