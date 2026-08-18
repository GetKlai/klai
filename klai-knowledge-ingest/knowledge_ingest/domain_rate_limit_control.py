"""Pure AIMD regelwet for per-domain crawl rate-limit recovery.

2026-08-17 (intermedia.com + support.ascendcloud.com incident) added
``lower_domain_rate_limit``: a domain that hits RATE_LIMITED or
BLOCKED_ANTI_BOT gets its rate limit halved (floor 0.2 req/s) so the NEXT
crawl of that domain starts already paced down. That is half of an AIMD
(Additive-Increase/Multiplicative-Decrease) controller — multiplicative
decrease with no additive increase. A domain that had one bad crawl stays
throttled forever, because nothing ever raises the limit back up.

This module adds the missing half: additive increase, with hysteresis so
a single good crawl right after a bad one does not immediately erase the
backoff (that would just repeat the incident). The regelwet
(``compute_domain_rate_limit_update``) is a pure function — no database,
no wall clock, every time-dependent value is a parameter — so every edge
case is testable without a Postgres connection. Persistence is a separate,
thin concern in ``knowledge_ingest.domain_selectors``.

Counting rule (the part most likely to be "simplified" away by a future
change — see ``count_rate_limit_observations``): only ``SUCCESS`` is a
clean observation, and only ``RATE_LIMITED`` / ``BLOCKED_ANTI_BOT`` is a
congestion observation. Everything else (timeouts, 5xx, the
``NOT_FETCHED_*`` family, ``non_content_listing_page``) is neither. A
timeout says nothing about whether the site is rate-limiting us, and a URL
we chose not to send (``NOT_FETCHED_RATE_LIMIT_STOP``) says nothing about
anything — counting it either way would inflate or deflate the very signal
this controller reacts to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from knowledge_ingest.reason_codes import FetchReasonCode

# Same floor as the original halving-only behaviour — a very touchy site
# still makes forward progress (1 request every 5s) instead of the halving
# (and now also the increase step) asymptoting toward zero.
MIN_DOMAIN_RATE_LIMIT = 0.2

_CONGESTION_REASON_CODES = frozenset(
    {FetchReasonCode.RATE_LIMITED.value, FetchReasonCode.BLOCKED_ANTI_BOT.value}
)


@dataclass(frozen=True)
class DomainRateLimitState:
    """The full AIMD state persisted per ``(domain, org_id)``.

    ``rate_limit`` is ``None`` when no override is stored — the domain is
    healthy and the caller's own default applies. ``clean_streak`` and
    ``last_congestion_at`` only matter while an override exists; a healthy
    domain (``rate_limit is None``) has nothing to recover from and is
    never written (see ``compute_domain_rate_limit_update``).
    """

    rate_limit: float | None
    clean_streak: int
    last_congestion_at: datetime | None


@dataclass(frozen=True)
class RateLimitObservation:
    """What this crawl job actually observed, reduced to the two facts the
    regelwet needs. See the module docstring for the counting rule."""

    had_congestion: bool
    clean_count: int


def count_rate_limit_observations(fetch_outcomes: list[dict]) -> RateLimitObservation:
    """Reduce a job's ``fetch_outcomes`` to (had_congestion, clean_count).

    See the module docstring's "Counting rule" section — this is
    deliberately narrow. Do not broaden it to count timeouts, 5xx, or
    ``NOT_FETCHED_*`` as either signal; none of them observe whether the
    site is rate-limiting us.
    """
    had_congestion = False
    clean_count = 0
    for outcome in fetch_outcomes:
        reason = outcome.get("reason_code")
        if reason in _CONGESTION_REASON_CODES:
            had_congestion = True
        elif reason == FetchReasonCode.SUCCESS.value:
            clean_count += 1
    return RateLimitObservation(had_congestion=had_congestion, clean_count=clean_count)


def compute_domain_rate_limit_update(
    state: DomainRateLimitState,
    *,
    had_congestion: bool,
    clean_observations: int,
    default_rate_limit: float,
    step_up: float,
    recovery_threshold: int,
    cooldown: timedelta,
    now: datetime,
) -> DomainRateLimitState | None:
    """The regelwet: additive up, multiplicative down, with hysteresis.

    Returns the new state to persist, or ``None`` when nothing should be
    written at all — a healthy domain with no stored override and no
    congestion this job stays untouched, so ``knowledge.crawl_domains``
    never gains a row for a site that was never a problem.

    Congestion (``had_congestion``) always wins over any clean observations
    in the SAME job — halve from whatever rate was actually in effect this
    run (the stored override if any, else the job's own default), reset
    the clean streak to zero, and record ``now`` as the last congestion.

    Otherwise, accumulate the clean streak. Only raise when ALL of:
      - an override is actually stored (nothing to recover otherwise),
      - the accumulated streak has reached ``recovery_threshold``, and
      - the cooldown since the last congestion has elapsed (or there was
        never a recorded congestion for this stored override).
    The raise is always exactly one step (``step_up``), never scaled by how
    far past the threshold the streak has grown — a single fixed step per
    eligible job, capped at ``default_rate_limit``. Once the raise would
    reach or exceed the default, the override is cleared entirely
    (``rate_limit=None``) rather than storing the default value, and the
    streak/congestion-timestamp reset with it — the domain falls back to
    the normal (no-override) path and the table stays clean.
    """
    if had_congestion:
        effective_rate_limit = (
            state.rate_limit if state.rate_limit is not None else default_rate_limit
        )
        lowered = max(MIN_DOMAIN_RATE_LIMIT, effective_rate_limit / 2)
        return DomainRateLimitState(rate_limit=lowered, clean_streak=0, last_congestion_at=now)

    if state.rate_limit is None:
        # Nothing to recover — do not start tracking a healthy domain.
        return None

    new_streak = state.clean_streak + clean_observations
    cooldown_elapsed = (
        state.last_congestion_at is None or now - state.last_congestion_at >= cooldown
    )

    if new_streak >= recovery_threshold and cooldown_elapsed:
        raised = min(default_rate_limit, state.rate_limit + step_up)
        if raised >= default_rate_limit:
            return DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)
        return DomainRateLimitState(
            rate_limit=raised, clean_streak=0, last_congestion_at=state.last_congestion_at
        )

    return DomainRateLimitState(
        rate_limit=state.rate_limit,
        clean_streak=new_streak,
        last_congestion_at=state.last_congestion_at,
    )
