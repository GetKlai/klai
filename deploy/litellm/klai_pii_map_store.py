"""Process-local placeholder->original-value map store (REQ-11).

SPEC-PRIVACY-MISTRAL-PII-001 REQ-11: the map from placeholder to original
value lives only for the lifetime of the request that created it, is keyed
such that it cannot be reached by another request, and is never written to
Redis, Postgres, disk, or any log.

This class is that store. It is deliberately the ONLY place in the Phase 3
stack that holds a placeholder->original-value mapping in memory, and it has
no serialization method at all — not a missing feature, the absence is the
control. Adding a ``to_json`` / ``to_dict`` here would be the one change that
could turn this into the "shared map... reachable across requests" REQ-11
calls the failure mode worse than masking itself.

Keying: callers key entries by ``litellm_call_id`` (REQ-11's own words) — a
per-request UUID LiteLLM puts on ``data``/``request_data`` before any hook
runs (verified against ``litellm/proxy/common_request_processing.py`` and
``litellm/proxy/utils.py`` in the installed v1.96.2 package: both set
``data["litellm_call_id"]`` before the pre-call hook chain executes), and
which the ``aim`` and ``cato_networks`` guardrails already read via
``data.get("litellm_call_id")`` / ``request_data.get("litellm_call_id")`` in
their own streaming iterator hooks — this store follows the same contract.

Three failure modes REQ-11 names explicitly, all covered here:

1. A client disconnects mid-stream, reaching neither the success nor the
   error path -> entry would leak forever. ``_sweep_expired()`` (TTL,
   REQ-11's own words: "a TTL sweep SHALL additionally drop entries older
   than a bounded age") runs on every ``put``/``get`` call, so a leaked
   entry is bounded in lifetime even if nobody ever calls ``discard()``.
2. Unbounded growth under sustained traffic -> ``put()`` evicts the oldest
   entry (REQ-11: "bounded... drop oldest-first when full") before
   inserting once the store is at capacity.
3. Cross-request reachability -> there is no lookup by anything other than
   the exact ``litellm_call_id`` string a caller supplies; there is no
   iteration, no "get most recent", no wildcard. A caller with the wrong
   call_id gets ``None``, never another request's map.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

# Conservative defaults for a per-request placeholder map: entries are tiny
# (a handful of short strings) and short-lived (one request's worth of
# in-flight chat/stream), so neither bound needs to be generous to be safe.
DEFAULT_MAX_ENTRIES = 512
DEFAULT_TTL_SECONDS = 300.0  # 5 minutes: comfortably longer than any Klai
# chat request_timeout (120s, config.yaml litellm_settings), short enough
# that a leaked entry from a disconnected client does not linger.


@dataclass
class _Entry:
    mapping: dict[str, str]
    created_at: float


class PiiMapStore:
    """Bounded, TTL-swept, process-local dict keyed by ``litellm_call_id``.

    Not thread-safe by design — LiteLLM's proxy hooks run on a single
    asyncio event loop per worker process, and this store is process-local
    (REQ-11: never shared, never persisted), so there is exactly one
    coroutine touching any given entry's lifecycle at a time in practice.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def _sweep_expired(self) -> None:
        if not self._entries:
            return
        now = self._clock()
        expired = [
            call_id
            for call_id, entry in self._entries.items()
            if now - entry.created_at > self._ttl_seconds
        ]
        for call_id in expired:
            del self._entries[call_id]

    def put(self, call_id: str, mapping: dict[str, str]) -> None:
        """Store (or replace) the restore map for one request.

        Sweeps expired entries first (so a burst of expired garbage never
        counts against the size bound), then evicts the single oldest
        surviving entry, repeatedly, until there is room — "drop
        oldest-first when full rather than grow without limit" (REQ-11).
        """
        if not call_id:
            raise ValueError("call_id must be non-empty")
        self._sweep_expired()
        if call_id in self._entries:
            # Replacing an existing call's map should not itself count as
            # "growth" for the eviction check below — drop it first.
            del self._entries[call_id]
        while len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)  # oldest-first (insertion order)
        self._entries[call_id] = _Entry(mapping=dict(mapping), created_at=self._clock())

    def get(self, call_id: str) -> dict[str, str] | None:
        """Non-destructive lookup. Used mid-stream; entry survives until
        an explicit ``discard()`` (REQ-11 requires deletion only at stream
        end / success / error, not on every intermediate read).
        """
        if not call_id:
            return None
        self._sweep_expired()
        entry = self._entries.get(call_id)
        return dict(entry.mapping) if entry is not None else None

    def discard(self, call_id: str) -> None:
        """Remove the entry unconditionally. Safe to call when absent."""
        if call_id:
            self._entries.pop(call_id, None)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, call_id: str) -> bool:
        return call_id in self._entries

    def sweep(self) -> int:
        """Force a TTL sweep now; return the number of entries removed.

        ``put``/``get`` already sweep internally on every call — this is
        for tests and any future explicit maintenance hook that wants to
        observe sweep effects without also mutating the store via
        ``put``/``get``.
        """
        before = len(self._entries)
        self._sweep_expired()
        return before - len(self._entries)
