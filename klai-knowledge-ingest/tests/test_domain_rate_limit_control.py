"""Pure AIMD regelwet for per-domain crawl rate-limit recovery.

``lower_domain_rate_limit`` (2026-08-17) halves a domain's rate limit on
congestion but never raises it back — a domain that had one bad day stays
throttled forever. Block B (2026-08-18) added the missing half:
additive-increase with hysteresis.

2026-08-19 replaced two more problems in that design (see
``domain_rate_limit_control``'s module docstring for the full incident
context): congestion is now a RATIO verdict instead of a single-signal
event (``classify_crawl_congestion``), the recovery step scales to the
domain's own default rate limit instead of a fixed absolute step, and a
stored override with no recent congestion evidence decays back to the
default at read time (``apply_domain_rate_limit_decay``).

``compute_domain_rate_limit_update`` stays a pure function: no database,
no wall clock — every time-dependent value is a parameter. This lets every
edge case (hysteresis, floor, ceiling, table-cleanliness-on-recovery,
decay) be tested without a Postgres connection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_ingest.domain_rate_limit_control import (
    MIN_DOMAIN_RATE_LIMIT,
    DomainRateLimitState,
    RateLimitObservation,
    apply_domain_rate_limit_decay,
    classify_crawl_congestion,
    compute_domain_rate_limit_update,
    count_rate_limit_observations,
)

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
STEP_FRACTION = 0.25
COOLDOWN = timedelta(hours=24)
DEFAULT_RATE_LIMIT = 2.0
RATIO_THRESHOLD = 0.25
MIN_ATTEMPTS = 10


def _update(
    state: DomainRateLimitState,
    *,
    had_congestion: bool | None,
    default_rate_limit: float = DEFAULT_RATE_LIMIT,
    step_fraction: float = STEP_FRACTION,
    cooldown: timedelta = COOLDOWN,
    now: datetime = NOW,
) -> DomainRateLimitState | None:
    return compute_domain_rate_limit_update(
        state,
        had_congestion=had_congestion,
        default_rate_limit=default_rate_limit,
        step_fraction=step_fraction,
        cooldown=cooldown,
        now=now,
    )


def _classify(
    *,
    congestion_count: int,
    clean_count: int,
    attempted_count: int,
    ratio_threshold: float = RATIO_THRESHOLD,
    min_attempts: int = MIN_ATTEMPTS,
) -> bool | None:
    observation = RateLimitObservation(
        congestion_count=congestion_count,
        clean_count=clean_count,
        attempted_count=attempted_count,
    )
    return classify_crawl_congestion(
        observation, ratio_threshold=ratio_threshold, min_attempts=min_attempts
    )


def _outcomes(*reason_codes: str) -> list[dict]:
    return [
        {"url": f"https://example.com/{i}", "reason_code": rc} for i, rc in enumerate(reason_codes)
    ]


# ---------------------------------------------------------------------------
# THE regression test — support.ascendcloud.com, 2026-08-19
# ---------------------------------------------------------------------------


def test_ascend_incident_one_signal_in_475_attempts_does_not_trigger_congestion() -> None:
    """support.ascendcloud.com: 474 SUCCESS + 1 BLOCKED_ANTI_BOT out of 475
    real attempts (0.21% ratio) must NOT be classified as congestion, and
    must NOT reset a domain sitting at the floor — it must instead let that
    domain raise, because the old event-based design treated this single
    signal as full congestion and reset all recovery progress."""
    outcomes = _outcomes(*(["success"] * 474 + ["blocked_anti_bot"]))
    observation = count_rate_limit_observations(outcomes)
    assert observation.congestion_count == 1
    assert observation.clean_count == 474
    assert observation.attempted_count == 475

    verdict = classify_crawl_congestion(
        observation, ratio_threshold=RATIO_THRESHOLD, min_attempts=MIN_ATTEMPTS
    )
    assert verdict is False

    state = DomainRateLimitState(
        rate_limit=MIN_DOMAIN_RATE_LIMIT,
        clean_streak=0,
        last_congestion_at=NOW - timedelta(hours=25),
    )
    result = _update(state, had_congestion=verdict)

    assert result is not None
    assert result.rate_limit == pytest.approx(0.7)  # raised, not reset to floor/congestion
    assert result.clean_streak == 1


# ---------------------------------------------------------------------------
# classify_crawl_congestion
# ---------------------------------------------------------------------------


def test_a_quarter_of_attempts_rate_limited_counts_as_congestion() -> None:
    """Exactly on the threshold (>=, not >) still counts as congestion."""
    verdict = _classify(congestion_count=5, clean_count=15, attempted_count=20)
    assert verdict is True


def test_too_few_attempts_returns_no_verdict_and_state_is_untouched() -> None:
    """A LOW ratio on too little data (5 attempts, 1 congestion signal, 20%
    — below the 25% threshold) is not trustworthy evidence of health
    either way. See test_high_ratio_congestion_is_recognized_even_under_
    the_min_attempts_floor for why a HIGH ratio is treated differently."""
    verdict = _classify(congestion_count=1, clean_count=4, attempted_count=5)
    assert verdict is None

    # A None verdict is a true no-op — even a domain WITH a stored override
    # is left completely untouched, proving this isn't just "no verdict on
    # an empty state".
    state = DomainRateLimitState(rate_limit=0.5, clean_streak=10, last_congestion_at=NOW)
    result = _update(state, had_congestion=verdict)
    assert result is None


def test_high_ratio_congestion_is_recognized_even_under_the_min_attempts_floor() -> None:
    """2026-08-19 (Sol review): a domain already at the rate-limit floor
    that is STILL being congested produces a naturally SMALL job — crawl4ai_
    client's own consecutive-slowdown give-up ladder aborts after roughly 4
    chunks of 2 URLs plus the seed page (9 attempts) at the 0.2 req/s floor,
    under the 10-attempt min_attempts. If min_attempts gated True as well as
    False, this domain would get NO verdict, last_congestion_at would never
    refresh, and apply_domain_rate_limit_decay would eventually un-throttle
    a domain that never stopped being congested. A high ratio is trusted
    regardless of sample size; only the "healthy" conclusion needs a real
    sample."""
    verdict = _classify(congestion_count=8, clean_count=1, attempted_count=9)
    assert verdict is True


def test_a_crawl_with_no_success_evidence_at_all_is_inconclusive_not_clean() -> None:
    """2026-08-19 (Sol review): a crawl that fails every one of >= min_
    attempts requests for reasons UNRELATED to rate-limiting (DNS errors,
    5xx, timeouts, refusals) has a congestion ratio of 0 — but zero SUCCESS
    observations is not evidence the domain tolerates a faster pace either.
    Without this check, a domain that is simply down would still get its
    rate limit raised, having proven nothing about whether it can sustain
    that speed."""
    verdict = _classify(congestion_count=0, clean_count=0, attempted_count=10)
    assert verdict is None


# ---------------------------------------------------------------------------
# compute_domain_rate_limit_update
# ---------------------------------------------------------------------------


def test_congestion_halves_resets_streak_and_records_the_timestamp() -> None:
    state = DomainRateLimitState(rate_limit=1.0, clean_streak=30, last_congestion_at=None)

    result = _update(state, had_congestion=True)

    assert result == DomainRateLimitState(rate_limit=0.5, clean_streak=0, last_congestion_at=NOW)


def test_congestion_never_halves_below_the_floor() -> None:
    state = DomainRateLimitState(rate_limit=0.3, clean_streak=0, last_congestion_at=None)

    result = _update(state, had_congestion=True)

    assert result is not None
    assert result.rate_limit == pytest.approx(0.2)


def test_congestion_from_default_when_no_override_stored_yet() -> None:
    """Congestion on a domain with no stored override halves the job's
    default rate — the 'effective rate actually used this run'."""
    state = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)

    result = _update(state, had_congestion=True, default_rate_limit=2.0)

    assert result is not None
    assert result.rate_limit == pytest.approx(1.0)


def test_clean_job_within_cooldown_does_not_raise_but_still_accumulates_streak() -> None:
    """Hysteresis case — the streak keeps accumulating even though the
    cooldown blocks a raise, so a later job (once the cooldown elapses)
    can raise without needing fresh evidence from scratch."""
    state = DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=10,
        last_congestion_at=NOW - timedelta(hours=1),
    )

    result = _update(state, had_congestion=False)

    assert result == DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=11,
        last_congestion_at=NOW - timedelta(hours=1),
    )


def test_a_single_clean_crawl_past_cooldown_raises_one_step() -> None:
    """A single non-congested crawl (no accumulated-observation threshold
    any more) is itself sufficient evidence to raise, once the cooldown has
    elapsed."""
    state = DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=0,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    result = _update(state, had_congestion=False)

    assert result == DomainRateLimitState(
        rate_limit=pytest.approx(1.0),  # 0.5 + (2.0 * 0.25)
        clean_streak=1,
        last_congestion_at=NOW - timedelta(hours=25),
    )


def test_a_domain_at_the_floor_reaches_the_default_in_exactly_four_clean_crawls() -> None:
    """0.2 -> 0.7 -> 1.2 -> 1.7 -> cleared, matching the arithmetic in
    config.py's crawl_rate_limit_recovery_step_fraction comment: step =
    default_rate_limit * step_fraction = 2.0 * 0.25 = 0.5 req/s.

    ``last_congestion_at`` never advances on a clean raise (only congestion
    or a full clear touch it), so a single fixed cooldown-elapsed timestamp
    stays valid across every chained call below."""
    state: DomainRateLimitState | None = DomainRateLimitState(
        rate_limit=MIN_DOMAIN_RATE_LIMIT,
        clean_streak=0,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    expected_rate_limits = [0.7, 1.2, 1.7]
    for i, expected in enumerate(expected_rate_limits, start=1):
        state = _update(state, had_congestion=False)
        assert state is not None
        assert state.rate_limit == pytest.approx(expected)
        assert state.clean_streak == i

    final = _update(state, had_congestion=False)
    assert final == DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)


def test_raise_never_exceeds_the_job_default() -> None:
    state = DomainRateLimitState(
        rate_limit=1.9,
        clean_streak=0,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    result = _update(state, had_congestion=False, default_rate_limit=2.0)

    assert result is not None
    assert result.rate_limit is None  # reached/exceeded default -> override cleared


def test_reaching_default_clears_the_override_instead_of_storing_it() -> None:
    """Also resets the streak and congestion timestamp so the table is
    genuinely clean again."""
    state = DomainRateLimitState(
        rate_limit=1.5,  # +0.5 step lands exactly on the 2.0 default
        clean_streak=3,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    result = _update(state, had_congestion=False, default_rate_limit=2.0)

    assert result == DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)


def test_healthy_domain_with_no_stored_override_is_left_untouched() -> None:
    """A domain without a stored override stays untouched — no rows get
    created for healthy domains. Returning None signals the caller to skip
    persistence entirely."""
    state = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)

    result = _update(state, had_congestion=False)

    assert result is None


def test_congestion_after_a_run_of_clean_jobs_resets_the_streak_immediately() -> None:
    state = DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=49,
        last_congestion_at=NOW - timedelta(hours=30),
    )

    result = _update(state, had_congestion=True)

    assert result == DomainRateLimitState(rate_limit=0.25, clean_streak=0, last_congestion_at=NOW)


# ---------------------------------------------------------------------------
# apply_domain_rate_limit_decay
# ---------------------------------------------------------------------------


def test_decay_no_congestion_in_eight_days_reverts_to_default() -> None:
    state = DomainRateLimitState(
        rate_limit=0.5, clean_streak=2, last_congestion_at=NOW - timedelta(days=8)
    )

    result = apply_domain_rate_limit_decay(state, decay_after=timedelta(days=7), now=NOW)

    assert result == DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)


def test_decay_congestion_yesterday_does_not_decay() -> None:
    state = DomainRateLimitState(
        rate_limit=0.5, clean_streak=0, last_congestion_at=NOW - timedelta(days=1)
    )

    result = apply_domain_rate_limit_decay(state, decay_after=timedelta(days=7), now=NOW)

    assert result == state


def test_decay_treats_missing_congestion_timestamp_as_already_expired_evidence() -> None:
    """The www.intermedia.com production case: rate_limit stored, but
    last_congestion_at is NULL (lowered before the column existed). This
    decays immediately regardless of decay_after — proven here with a long
    30-day window so it is clearly not just "the null case coincidentally
    exceeds a short window"."""
    state = DomainRateLimitState(rate_limit=0.5, clean_streak=0, last_congestion_at=None)

    result = apply_domain_rate_limit_decay(state, decay_after=timedelta(days=30), now=NOW)

    assert result == DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)


def test_decay_leaves_a_healthy_domain_completely_untouched() -> None:
    state = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)

    result = apply_domain_rate_limit_decay(state, decay_after=timedelta(days=7), now=NOW)

    assert result == state


# ---------------------------------------------------------------------------
# count_rate_limit_observations
# ---------------------------------------------------------------------------


def test_count_rate_limit_observations_not_fetched_excluded_from_attempted_too() -> None:
    """1 rate_limited + 99 not_fetched_rate_limit_stop: the not-fetched URLs
    were never sent, so they are excluded from attempted_count too, not
    just from congestion/clean."""
    outcomes = _outcomes(*(["rate_limited"] + ["not_fetched_rate_limit_stop"] * 99))

    result = count_rate_limit_observations(outcomes)

    assert result.congestion_count == 1
    assert result.clean_count == 0
    assert result.attempted_count == 1


def test_count_rate_limit_observations_timeouts_count_as_neither() -> None:
    """A job with only timeouts says nothing about whether the site rate-
    limits us, but it WAS a real attempt."""
    outcomes = _outcomes("timeout", "http_5xx", "non_content_listing_page")

    result = count_rate_limit_observations(outcomes)

    assert result.congestion_count == 0
    assert result.clean_count == 0
    assert result.attempted_count == 3


def test_count_rate_limit_observations_blocked_anti_bot_is_congestion() -> None:
    outcomes = _outcomes("blocked_anti_bot")

    result = count_rate_limit_observations(outcomes)

    assert result.congestion_count == 1
    assert result.clean_count == 0
    assert result.attempted_count == 1


def test_count_rate_limit_observations_refused_is_not_congestion() -> None:
    """2026-08-19 (onderdeel 3, intermedia.com / support.ascendcloud.com
    "weigering" incident): a REFUSED outcome (the site explicitly refuses
    automated access — see reason_codes.py) must NOT lower the domain's
    stored rate_limit. Slowing down does not fix a refusal, unlike real
    429 congestion."""
    outcomes = _outcomes("refused")

    result = count_rate_limit_observations(outcomes)

    assert result.congestion_count == 0
    assert result.clean_count == 0


def test_count_rate_limit_observations_refused_counts_toward_attempted() -> None:
    """REFUSED dilutes the congestion ratio (it counts as an attempt) but
    never inflates it (it never counts as congestion)."""
    outcomes = _outcomes("refused", "refused", "success")

    result = count_rate_limit_observations(outcomes)

    assert result.attempted_count == 3
    assert result.congestion_count == 0
    assert result.clean_count == 1


def test_count_rate_limit_observations_success_is_clean() -> None:
    outcomes = _outcomes("success", "success")

    result = count_rate_limit_observations(outcomes)

    assert result.congestion_count == 0
    assert result.clean_count == 2
    assert result.attempted_count == 2
