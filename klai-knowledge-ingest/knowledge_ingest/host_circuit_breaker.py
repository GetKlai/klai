"""Pure per-host circuit breaker for the crawl bulk-fetch loop.

2026-08-19 (intermedia.com incident #3 — "20 minutes, zero pages"): a
RATE_LIMITED signal already stops ``_chunked_bulk_fetch`` immediately (see
``crawl4ai_client._STOP_CHUNKING_REASON_CODES``), and a BLOCKED_ANTI_BOT
signal stops it once a crawl-wide ratio+floor gate trips
(``settings.crawl_antibot_stop_ratio`` / ``crawl_antibot_stop_min_count``).
Neither mechanism reacts to "everything is failing" in general — a site
that 500s, times out, or DNS-fails on every request keeps getting hammered
for the crawl's full page budget, because none of those individual reason
codes is RATE_LIMITED or BLOCKED_ANTI_BOT. That gap is what let the morning
crawl of intermedia.com burn ~20 minutes and ingest zero pages.

This module is the missing general-purpose breaker: three independent
triggers, evaluated once per bulk-fetch CHUNK (never per URL — see
``crawl4ai_client._chunked_bulk_fetch``'s docstring for why per-chunk
granularity is what lets this intervene within tens of seconds instead of
minutes):

- Consecutive: N chunk-level failures in a row abort. A chunk counts as
  exactly ONE observation for this counter regardless of how many URLs it
  covered (a 20-URL chunk that fails wholesale is one failed *request*,
  not twenty) — see ``ChunkObservation``. A single success anywhere resets
  the counter to zero.
- Ratio: once at least ``min_attempts_for_ratio`` URLs have been attempted
  (crawl-wide, within ONE ``_chunked_bulk_fetch`` call), abort once MORE
  than ``failure_ratio_threshold`` of them failed. Unlike the consecutive
  trigger, this one counts real per-URL attempts/failures, not
  chunk-as-one-observation.
- Refusal: ``refusal_threshold`` observed REFUSED outcomes (see
  ``knowledge_ingest.reason_codes.FetchReasonCode.REFUSED``) abort
  immediately. The site is explicitly refusing automated access — slowing
  down does not fix that, unlike real rate-limiting — so this counter is
  never reset by an interleaved success and is checked independently of
  the other two.

Pure function, no I/O, no wall clock — every caller-observable value is a
parameter, matching ``domain_rate_limit_control.compute_domain_rate_limit_
update``'s shape. State is scoped to ONE ``_chunked_bulk_fetch`` call
(never persisted across calls) — a fresh ``HostCircuitBreakerState()``
starts at the top of every call, since crawl4ai_client itself has no
memory across invocations today.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BreakerVerdict(StrEnum):
    """The three outcomes ``evaluate_chunk`` can return."""

    CONTINUE = "continue"
    ABORT_PERSISTENT_FAILURE = "abort_persistent_failure"
    ABORT_REFUSAL = "abort_refusal"


@dataclass(frozen=True)
class HostCircuitBreakerState:
    """Counters accumulated across chunk boundaries within one
    ``_chunked_bulk_fetch`` call."""

    consecutive_failures: int = 0
    attempted: int = 0
    failed: int = 0
    refused: int = 0


@dataclass(frozen=True)
class ChunkObservation:
    """What ONE chunk contributed, reduced to the four facts the breaker
    needs.

    ``attempted``/``failed`` are real per-URL counts — a whole-chunk
    transport failure counts every URL it covered (the request really was
    made, and every one of those URLs really did fail), matching how
    ``crawl4ai_client._chunked_bulk_fetch`` already accounts chunk-level
    transport exceptions against ``failed``/``not_attempted``.

    ``any_success`` and ``refused`` drive the consecutive-streak and
    refusal-count respectively — see the module docstring for why a
    wholly-failed chunk is still only ONE observation for the streak
    regardless of ``attempted``.
    """

    attempted: int
    failed: int
    any_success: bool
    refused: int


def evaluate_chunk(
    state: HostCircuitBreakerState,
    observation: ChunkObservation,
    *,
    consecutive_failure_threshold: int,
    min_attempts_for_ratio: int,
    failure_ratio_threshold: float,
    refusal_threshold: int,
) -> tuple[HostCircuitBreakerState, BreakerVerdict]:
    """Fold ``observation`` into ``state`` and decide continue vs. abort.

    Refusal is checked first — it is never undone by a success in the same
    or an earlier chunk, unlike the consecutive-failure streak. Either the
    consecutive-failure trigger or the ratio trigger is independently
    sufficient to abort once refusal does not already apply.
    """
    new_state = HostCircuitBreakerState(
        consecutive_failures=(0 if observation.any_success else state.consecutive_failures + 1),
        attempted=state.attempted + observation.attempted,
        failed=state.failed + observation.failed,
        refused=state.refused + observation.refused,
    )

    if new_state.refused >= refusal_threshold:
        return new_state, BreakerVerdict.ABORT_REFUSAL

    if new_state.consecutive_failures >= consecutive_failure_threshold:
        return new_state, BreakerVerdict.ABORT_PERSISTENT_FAILURE

    if (
        new_state.attempted >= min_attempts_for_ratio
        and new_state.failed / new_state.attempted > failure_ratio_threshold
    ):
        return new_state, BreakerVerdict.ABORT_PERSISTENT_FAILURE

    return new_state, BreakerVerdict.CONTINUE
