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
  (crawl-wide, within ONE ``_chunked_bulk_fetch`` call), this is now a
  TWO-STEP LADDER instead of a single abort-or-nothing gate (2026-08-19,
  onderdeel 3 — the "20 minutes, zero pages" incident's actual trigger was
  crawl4ai wrapping a 429 in an opaque 500, so the failure REASON was
  unreadable but the failure RATE was known — 100%. The rate should have
  driven a response even though the reason didn't):

  - once the ratio exceeds ``slowdown_ratio_threshold``, verdict is
    SLOWDOWN — not an abort. The caller (``crawl4ai_client._chunked_bulk_
    fetch``) reuses the EXISTING RATE_LIMITED-flavoured stop-and-retry
    path (``crawl_site``'s Deel B halving/cooldown/give-up-after-N-
    halvings ladder) for this verdict too, so a persistently high ratio
    that never recovers still gives up eventually — via the SAME
    ``_MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS`` cap, not a second one.
  - once the ratio exceeds ``failure_ratio_threshold`` (unchanged, the
    original single gate), verdict is ABORT_PERSISTENT_FAILURE — give up
    immediately, same as before. This threshold MUST stay above
    ``slowdown_ratio_threshold`` (checked first) so a genuinely dead site
    is never mistaken for merely a "go slower" case.

  Unlike the consecutive trigger, this one counts real per-URL
  attempts/failures, not chunk-as-one-observation.
- Refusal: ``refusal_threshold`` observed REFUSED outcomes (see
  ``knowledge_ingest.reason_codes.FetchReasonCode.REFUSED``) abort
  immediately. The site is explicitly refusing automated access — slowing
  down does not fix that, unlike real rate-limiting — so this counter is
  never reset by an interleaved success, is checked independently of (and
  before) the other two, and NEVER downgrades to SLOWDOWN regardless of
  the concurrent failure ratio.

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
    """The four outcomes ``evaluate_chunk`` can return.

    SLOWDOWN sits between CONTINUE and the two ABORT verdicts: the failure
    ratio is elevated enough to warrant pacing down, but not (yet) high
    enough to justify giving up. See the module docstring's "Ratio" bullet
    for the full ladder and how the caller is expected to treat each verdict.
    """

    CONTINUE = "continue"
    SLOWDOWN = "slowdown"
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
    slowdown_ratio_threshold: float,
) -> tuple[HostCircuitBreakerState, BreakerVerdict]:
    """Fold ``observation`` into ``state`` and decide continue/slowdown/abort.

    Refusal is checked first — it is never undone by a success in the same
    or an earlier chunk, unlike the consecutive-failure streak, and it NEVER
    downgrades to SLOWDOWN: a refusal is not a pacing problem. Consecutive
    failures abort next — an intermittently-failing site never reaches
    ``min_attempts_for_ratio`` worth of runway before this trips, so it is
    checked before the ratio ladder, not after.

    The ratio ladder itself checks the higher (abort) threshold before the
    lower (slowdown) one, so a ratio that already exceeds
    ``failure_ratio_threshold`` the very first time ``min_attempts_for_ratio``
    is reached goes straight to ABORT_PERSISTENT_FAILURE — it is never
    reported as SLOWDOWN first. Callers MUST pass
    ``slowdown_ratio_threshold < failure_ratio_threshold``; this function
    does not itself validate the ordering.
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

    if new_state.attempted >= min_attempts_for_ratio:
        failure_ratio = new_state.failed / new_state.attempted
        if failure_ratio > failure_ratio_threshold:
            return new_state, BreakerVerdict.ABORT_PERSISTENT_FAILURE
        if failure_ratio > slowdown_ratio_threshold:
            return new_state, BreakerVerdict.SLOWDOWN

    return new_state, BreakerVerdict.CONTINUE
