"""Pure AIMD regelwet for per-domain crawl rate-limit recovery (block B).

``lower_domain_rate_limit`` (2026-08-17) halves a domain's rate limit on
congestion but never raises it back — a domain that had one bad day stays
throttled forever. This module adds the missing half: additive-increase
with hysteresis (a cooldown + a minimum run of clean observations before
any increase), so a site that has genuinely recovered gets its rate limit
back over time, without oscillating on a single good crawl right after a
bad one.

``compute_domain_rate_limit_update`` is a pure function: no database, no
wall clock — every time-dependent value is a parameter. This lets every
edge case (hysteresis, floor, ceiling, table-cleanliness-on-recovery) be
tested without a Postgres connection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_ingest.domain_rate_limit_control import (
    DomainRateLimitState,
    compute_domain_rate_limit_update,
    count_rate_limit_observations,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
STEP_UP = 0.2
RECOVERY_THRESHOLD = 50
COOLDOWN = timedelta(hours=24)
DEFAULT_RATE_LIMIT = 2.0


def _update(
    state: DomainRateLimitState,
    *,
    had_congestion: bool = False,
    clean_observations: int = 0,
    default_rate_limit: float = DEFAULT_RATE_LIMIT,
    step_up: float = STEP_UP,
    recovery_threshold: int = RECOVERY_THRESHOLD,
    cooldown: timedelta = COOLDOWN,
    now: datetime = NOW,
) -> DomainRateLimitState | None:
    return compute_domain_rate_limit_update(
        state,
        had_congestion=had_congestion,
        clean_observations=clean_observations,
        default_rate_limit=default_rate_limit,
        step_up=step_up,
        recovery_threshold=recovery_threshold,
        cooldown=cooldown,
        now=now,
    )


def test_congestion_halves_resets_streak_and_records_the_timestamp() -> None:
    """1. Congestie halveert, zet de teller op nul en legt het tijdstip vast."""
    state = DomainRateLimitState(rate_limit=1.0, clean_streak=30, last_congestion_at=None)

    result = _update(state, had_congestion=True, clean_observations=0)

    assert result == DomainRateLimitState(rate_limit=0.5, clean_streak=0, last_congestion_at=NOW)


def test_congestion_never_halves_below_the_floor() -> None:
    """2. Congestie halveert nooit onder de bodem van 0.2."""
    state = DomainRateLimitState(rate_limit=0.3, clean_streak=0, last_congestion_at=None)

    result = _update(state, had_congestion=True, clean_observations=0)

    assert result is not None
    assert result.rate_limit == pytest.approx(0.2)


def test_congestion_from_default_when_no_override_stored_yet() -> None:
    """Congestion on a domain with no stored override halves the job's
    default rate — the 'effective rate actually used this run'."""
    state = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)

    result = _update(state, had_congestion=True, clean_observations=0, default_rate_limit=2.0)

    assert result is not None
    assert result.rate_limit == pytest.approx(1.0)


def test_clean_job_below_threshold_does_not_raise_and_accumulates_streak() -> None:
    """3. Schone job onder de drempel: geen verhoging, teller telt op."""
    state = DomainRateLimitState(rate_limit=0.5, clean_streak=10, last_congestion_at=None)

    result = _update(state, had_congestion=False, clean_observations=5)

    assert result == DomainRateLimitState(rate_limit=0.5, clean_streak=15, last_congestion_at=None)


def test_clean_job_above_threshold_but_within_cooldown_does_not_raise() -> None:
    """4. Schone job boven de drempel maar binnen de afkoelperiode: geen
    verhoging. This is the hysteresis case — the streak keeps accumulating
    so a later job (once the cooldown elapses) can still trigger the raise
    without needing fresh clean observations from scratch."""
    state = DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=45,
        last_congestion_at=NOW - timedelta(hours=1),
    )

    result = _update(state, had_congestion=False, clean_observations=10)

    assert result == DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=55,
        last_congestion_at=NOW - timedelta(hours=1),
    )


def test_clean_job_above_threshold_and_outside_cooldown_raises_one_step() -> None:
    """5. Schone job boven de drempel én buiten de afkoelperiode: precies
    één stap omhoog, teller terug op nul."""
    state = DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=60,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    result = _update(state, had_congestion=False, clean_observations=0)

    assert result == DomainRateLimitState(
        rate_limit=pytest.approx(0.7),
        clean_streak=0,
        last_congestion_at=NOW - timedelta(hours=25),
    )


def test_raise_never_exceeds_the_job_default() -> None:
    """6. Verhoging plafonneert op de default van de job en gaat er nooit
    overheen."""
    state = DomainRateLimitState(
        rate_limit=1.9,
        clean_streak=60,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    result = _update(state, had_congestion=False, clean_observations=0, default_rate_limit=2.0)

    assert result is not None
    assert result.rate_limit is None  # reached/exceeded default -> override cleared


def test_reaching_default_clears_the_override_instead_of_storing_it() -> None:
    """7. Bereikt de verhoging de default, dan wordt de opgeslagen override
    verwijderd in plaats van de default opgeslagen. Also resets the streak
    and congestion timestamp so the table is genuinely clean again."""
    state = DomainRateLimitState(
        rate_limit=1.8,  # +0.2 step lands exactly on the 2.0 default
        clean_streak=60,
        last_congestion_at=NOW - timedelta(hours=25),
    )

    result = _update(state, had_congestion=False, clean_observations=0, default_rate_limit=2.0)

    assert result == DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)


def test_healthy_domain_with_no_stored_override_is_left_untouched() -> None:
    """8. Een domein zonder opgeslagen verlaging blijft ongemoeid — geen
    rijen aanmaken voor gezonde domeinen. Returning None signals the caller
    to skip persistence entirely."""
    state = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)

    result = _update(state, had_congestion=False, clean_observations=1)

    assert result is None


def test_congestion_after_a_run_of_clean_jobs_resets_the_streak_immediately() -> None:
    """9. Congestie ná een reeks schone jobs zet de teller onmiddellijk
    terug op nul (even though the streak was already large)."""
    state = DomainRateLimitState(
        rate_limit=0.5,
        clean_streak=49,  # one observation away from the threshold
        last_congestion_at=NOW - timedelta(hours=30),
    )

    result = _update(state, had_congestion=True, clean_observations=0)

    assert result == DomainRateLimitState(rate_limit=0.25, clean_streak=0, last_congestion_at=NOW)


def test_count_rate_limit_observations_congestion_and_not_fetched_stop() -> None:
    """10. Een job met 1 waargenomen 429 en 99 NOT_FETCHED_RATE_LIMIT_STOP
    telt als één congestiesignaal en NUL schone waarnemingen — the
    not-fetched URLs were never sent, so they are not an observation of
    anything."""
    outcomes = [
        {"url": "https://example.com/a", "reason_code": "rate_limited"},
        *(
            {"url": f"https://example.com/skip-{i}", "reason_code": "not_fetched_rate_limit_stop"}
            for i in range(99)
        ),
    ]

    result = count_rate_limit_observations(outcomes)

    assert result.had_congestion is True
    assert result.clean_count == 0


def test_count_rate_limit_observations_timeouts_count_as_neither() -> None:
    """A job with only timeouts says nothing about whether the site rate-
    limits us — it counts toward neither congestion nor a clean run."""
    outcomes = [
        {"url": "https://example.com/a", "reason_code": "timeout"},
        {"url": "https://example.com/b", "reason_code": "http_5xx"},
        {"url": "https://example.com/c", "reason_code": "non_content_listing_page"},
    ]

    result = count_rate_limit_observations(outcomes)

    assert result.had_congestion is False
    assert result.clean_count == 0


def test_count_rate_limit_observations_blocked_anti_bot_is_congestion() -> None:
    outcomes = [{"url": "https://example.com/a", "reason_code": "blocked_anti_bot"}]

    result = count_rate_limit_observations(outcomes)

    assert result.had_congestion is True
    assert result.clean_count == 0


def test_count_rate_limit_observations_refused_is_not_congestion() -> None:
    """2026-08-19 (onderdeel 3, intermedia.com / support.ascendcloud.com
    "weigering" incident): a REFUSED outcome (the site explicitly refuses
    automated access — see reason_codes.py) must NOT lower the domain's
    stored rate_limit. Slowing down does not fix a refusal, unlike real
    429 congestion — the support.ascendcloud.com incident's rate got
    halved all the way to the floor for exactly this reason before this
    fix. REFUSED counts toward neither congestion nor a clean run, same
    treatment as a timeout or a 5xx."""
    outcomes = [{"url": "https://example.com/a", "reason_code": "refused"}]

    result = count_rate_limit_observations(outcomes)

    assert result.had_congestion is False
    assert result.clean_count == 0


def test_count_rate_limit_observations_success_is_clean() -> None:
    outcomes = [
        {"url": "https://example.com/a", "reason_code": "success"},
        {"url": "https://example.com/b", "reason_code": "success"},
    ]

    result = count_rate_limit_observations(outcomes)

    assert result.had_congestion is False
    assert result.clean_count == 2
