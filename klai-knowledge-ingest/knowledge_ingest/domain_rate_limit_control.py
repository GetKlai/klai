"""Pure AIMD regelwet for per-domain crawl rate-limit recovery.

2026-08-17 (intermedia.com + support.ascendcloud.com incident) added
``lower_domain_rate_limit``: a domain that hits RATE_LIMITED or
BLOCKED_ANTI_BOT gets its rate limit halved (floor 0.2 req/s) so the NEXT
crawl of that domain starts already paced down. That is half of an AIMD
(Additive-Increase/Multiplicative-Decrease) controller — multiplicative
decrease with no additive increase. A domain that had one bad crawl stays
throttled forever, because nothing ever raises the limit back up.

2026-08-18 (block B) added the missing half: additive increase, with
hysteresis so a single good crawl right after a bad one does not
immediately erase the backoff (that would just repeat the incident).

2026-08-19 (support.ascendcloud.com / www.intermedia.com follow-up) fixed
three problems in that first cut:

1. Congestion used to be detected as "any single RATE_LIMITED/
   BLOCKED_ANTI_BOT signal in a job" — an event, not a ratio. A crawl of
   support.ascendcloud.com with 457 SUCCESS and exactly one
   ``blocked_anti_bot`` signal out of ~475 real attempts got treated as
   full congestion and reset all recovery progress, even though the crawl
   was overwhelmingly clean. Congestion is now a ratio decision
   (``classify_crawl_congestion``), reusing the SAME threshold/minimum
   ``knowledge_ingest.host_circuit_breaker`` already uses for its
   per-chunk SLOWDOWN verdict — both mechanisms answer "is this site
   actually struggling, or is this noise?" at different granularities.
2. Recovery required accumulating 50 clean *page-level* observations
   before a single fixed 0.2 req/s step applied. A domain rarely sees 50
   clean pages in one job, so recovery essentially never fired, and even
   when it did, the fixed floor-to-default distance needed 9 steps ~= 9
   days. Recovery is now evidence-scaled per job, not observation-counted:
   one job with a definitive non-congested verdict (see
   ``classify_crawl_congestion``) is itself sufficient to raise one step,
   and the step is a fraction of the domain's own default rate limit
   (``compute_domain_rate_limit_update``'s ``step_fraction``) so it clears
   the floor-to-default distance in a handful of clean crawls regardless
   of what the default happens to be.
3. A punishment had no expiry. A domain with a stored low ``rate_limit``
   and no recent congestion evidence — including the production edge
   case, ``last_congestion_at IS NULL`` while ``rate_limit`` is lowered
   (www.intermedia.com is in exactly this state today, from the
   2026-08-17 halving-only fix that predates the ``last_congestion_at``
   column) — stayed throttled forever unless it happened to get crawled
   again and accumulate a fresh streak. ``apply_domain_rate_limit_decay``
   is the fix: applied at READ time, before a crawl even starts, a stored
   override with no congestion evidence in the configured window reverts
   fully to the default.

The regelwet (``compute_domain_rate_limit_update``) stays a pure function —
no database, no wall clock, every time-dependent value is a parameter — so
every edge case is testable without a Postgres connection. Persistence is a
separate, thin concern in ``knowledge_ingest.domain_selectors``.

Counting rule (the part most likely to be "simplified" away by a future
change — see ``count_rate_limit_observations``): only ``SUCCESS`` is a
clean observation, and only ``RATE_LIMITED`` / ``BLOCKED_ANTI_BOT`` is a
congestion observation. ``REFUSED`` counts toward ``attempted_count`` (the
site really did respond) but never toward ``congestion_count`` — a refusal
is not fixed by going slower, so it dilutes the ratio rather than
inflating it. Everything else (timeouts, 5xx, the ``NOT_FETCHED_*``
family, ``non_content_listing_page``) is excluded even from
``attempted_count`` — a timeout says nothing about whether the site is
rate-limiting us, and a URL we chose not to send
(``NOT_FETCHED_RATE_LIMIT_STOP`` and siblings) says nothing about anything
— counting it either way would inflate or deflate the very signal this
controller reacts to.
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

# URLs that were never actually sent — not an attempt, not a signal either
# way. See the module docstring's counting-rule paragraph.
_NOT_FETCHED_REASON_CODES = frozenset(
    code.value for code in FetchReasonCode if code.value.startswith("not_fetched_")
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
    """Raw counts reduced from one job's fetch_outcomes.

    ``congestion_count`` and ``clean_count`` are diagnostic; ``attempted_
    count`` is the ratio denominator ``classify_crawl_congestion`` needs.
    See ``count_rate_limit_observations`` for exactly which reason codes
    count toward which field.
    """

    congestion_count: int
    clean_count: int
    attempted_count: int


def count_rate_limit_observations(fetch_outcomes: list[dict]) -> RateLimitObservation:
    """Reduce a job's ``fetch_outcomes`` to raw congestion/clean/attempted counts.

    See the module docstring's "Counting rule" section — this is
    deliberately narrow. Do not broaden it to count timeouts, 5xx, or
    ``NOT_FETCHED_*`` toward any field; none of them observe whether the
    site is rate-limiting us.
    """
    congestion_count = 0
    clean_count = 0
    attempted_count = 0
    for outcome in fetch_outcomes:
        reason = outcome.get("reason_code")
        if reason in _NOT_FETCHED_REASON_CODES:
            continue  # never sent — not an attempt, not a signal either way
        attempted_count += 1
        if reason in _CONGESTION_REASON_CODES:
            congestion_count += 1
        elif reason == FetchReasonCode.SUCCESS.value:
            clean_count += 1
    return RateLimitObservation(
        congestion_count=congestion_count,
        clean_count=clean_count,
        attempted_count=attempted_count,
    )


def classify_crawl_congestion(
    observation: RateLimitObservation,
    *,
    ratio_threshold: float,
    min_attempts: int,
) -> bool | None:
    """Percentage-based congestion verdict, not an event-based one.

    Returns ``None`` ("no verdict — not enough data") when ``attempted_
    count`` is below ``min_attempts``: a 3-page crawl with one rate-limit
    signal says nothing trustworthy about whether the site is genuinely
    congested. Returns ``True`` when ``congestion_count / attempted_count
    >= ratio_threshold``, ``False`` otherwise. ``>=`` (not ``>``) so a
    crawl landing EXACTLY on the threshold counts as congestion, matching
    the "a quarter of attempts" framing this threshold is calibrated
    against.

    Callers pass ``settings.crawl_circuit_breaker_slowdown_ratio`` and
    ``settings.crawl_circuit_breaker_min_attempts`` — the SAME threshold
    and minimum ``knowledge_ingest.host_circuit_breaker`` already uses for
    its per-chunk SLOWDOWN verdict (see that module), not a second
    independently tunable pair. Reusing them is deliberate: both
    mechanisms answer the same underlying question ("is this site actually
    struggling, or is this noise?") at different granularities (per-chunk
    vs. per-completed-job).
    """
    if observation.attempted_count < min_attempts:
        return None
    return (observation.congestion_count / observation.attempted_count) >= ratio_threshold


def compute_domain_rate_limit_update(
    state: DomainRateLimitState,
    *,
    had_congestion: bool | None,
    default_rate_limit: float,
    step_fraction: float,
    cooldown: timedelta,
    now: datetime,
) -> DomainRateLimitState | None:
    """The regelwet: additive up, multiplicative down, no observation-count
    threshold — one non-congested crawl (see ``classify_crawl_congestion``)
    is itself sufficient evidence to raise, gated only by the cooldown
    since the last congestion.

    ``had_congestion=None`` means the calling job did not produce enough
    attempts to judge either way (see ``classify_crawl_congestion``) — this
    function is then a strict no-op regardless of what is currently
    stored: returns ``None``, meaning "nothing to persist, leave the row
    exactly as is". This is why a 3-page crawl can no longer wipe out a
    domain's recovery progress the way the old event-based design could.

    ``had_congestion=True`` always wins — halve from whatever rate was
    actually in effect this run (the stored override if any, else the
    job's own default), floor at ``MIN_DOMAIN_RATE_LIMIT``, reset
    ``clean_streak`` to 0, and record ``now`` as the last congestion.

    ``had_congestion=False``: if nothing is stored (``state.rate_limit is
    None``), there is nothing to recover — return ``None`` (do not create a
    row for a healthy domain). Otherwise this clean crawl always
    increments ``clean_streak`` by exactly 1 (a running "consecutive clean
    crawls since last congestion" counter, purely informational — it no
    longer gates anything). If the cooldown since ``last_congestion_at``
    has NOT elapsed, return the unchanged ``rate_limit`` with the
    incremented streak (hysteresis — a clean crawl right after a bad one
    does not undo the backoff). If the cooldown HAS elapsed (or there was
    never a recorded congestion), raise by exactly one step:
    ``step = default_rate_limit * step_fraction``. If the raised value
    would reach or exceed ``default_rate_limit``, clear the override
    entirely (``rate_limit=None``, ``clean_streak=0``,
    ``last_congestion_at=None``) instead of storing a value at/above the
    default — the domain falls back to the normal no-override path and the
    table stays clean.
    """
    if had_congestion is None:
        return None

    if had_congestion:
        effective_rate_limit = (
            state.rate_limit if state.rate_limit is not None else default_rate_limit
        )
        lowered = max(MIN_DOMAIN_RATE_LIMIT, effective_rate_limit / 2)
        return DomainRateLimitState(rate_limit=lowered, clean_streak=0, last_congestion_at=now)

    if state.rate_limit is None:
        # Nothing to recover — do not start tracking a healthy domain.
        return None

    new_streak = state.clean_streak + 1
    cooldown_elapsed = (
        state.last_congestion_at is None or now - state.last_congestion_at >= cooldown
    )

    if not cooldown_elapsed:
        return DomainRateLimitState(
            rate_limit=state.rate_limit,
            clean_streak=new_streak,
            last_congestion_at=state.last_congestion_at,
        )

    step = default_rate_limit * step_fraction
    raised = min(default_rate_limit, state.rate_limit + step)
    if raised >= default_rate_limit:
        return DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)
    return DomainRateLimitState(
        rate_limit=raised, clean_streak=new_streak, last_congestion_at=state.last_congestion_at
    )


def apply_domain_rate_limit_decay(
    state: DomainRateLimitState,
    *,
    decay_after: timedelta,
    now: datetime,
) -> DomainRateLimitState:
    """Time-based decay, applied at READ time before a crawl even starts
    (see ``adapters/crawler.py``) — a stored override with no congestion
    evidence in the last ``decay_after`` window is no more trustworthy than
    an unknown domain, so it reverts FULLY to the default (override
    cleared, not stepped down gradually) rather than waiting for a fresh
    clean crawl to earn its way back.

    A domain with ``rate_limit`` stored but ``last_congestion_at IS NULL``
    is treated the SAME as evidence that has already expired, not as
    evidence that never needs to expire: a punishment with no timestamp is
    a punishment with no evidence for it, and clearing it immediately (on
    the very next read, regardless of ``decay_after``) is the only
    defensible reading — this is exactly the state www.intermedia.com is
    in on production today (rate_limit lowered from the original
    2026-08-17 halving-only fix, before ``last_congestion_at`` existed as
    a column).

    Pure function — no DB, no wall clock, ``now`` is a parameter like
    everywhere else in this module.
    """
    if state.rate_limit is None:
        return state
    if state.last_congestion_at is None or now - state.last_congestion_at >= decay_after:
        return DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)
    return state
