"""Per-domain adaptive rate-limit control, wired through ``run_crawl_job``
(2026-08-17 halving incident, 2026-08-18 block B additive recovery,
2026-08-19 ratio-based congestion + evidence-scaled recovery + decay).

A domain that returns RATE_LIMITED or BLOCKED_ANTI_BOT outcomes at a
congestion RATIO above threshold should not get hammered again at the same
pace on the next crawl — and a domain that has since behaved, or whose
punishment has gone stale, should not stay throttled forever.
``run_crawl_job``:

- reads the full AIMD state for the domain
  (``domain_selectors.get_domain_rate_limit_state``), decays it if the
  congestion evidence is stale
  (``domain_rate_limit_control.apply_domain_rate_limit_decay``), and
  applies its ``rate_limit`` INSTEAD OF the caller's default when one is
  still stored after decay,
- counts this job's fetch_outcomes into congestion/clean/attempted signals
  (``domain_rate_limit_control.count_rate_limit_observations``) and
  classifies them as a congestion ratio verdict
  (``domain_rate_limit_control.classify_crawl_congestion``),
- runs the pure regelwet
  (``domain_rate_limit_control.compute_domain_rate_limit_update``), and
- persists the result in one write
  (``domain_selectors.save_domain_rate_limit_state``) — unless the
  regelwet says nothing needs persisting.

These tests mock ``crawl_site`` entirely — the crawl4ai wiring itself is
covered by tests/test_build_crawl_config.py and
tests/test_crawl_site_reconcile.py. The pure regelwet itself (hysteresis,
floor, ceiling, table-cleanliness edge cases, decay) is covered
exhaustively, without any mocking, in
tests/test_domain_rate_limit_control.py. Here the concern is purely the
wiring: does run_crawl_job read the stored state, decay it correctly, feed
the right observations into the regelwet, and persist exactly what the
regelwet returned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import run_crawl_job
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.domain_rate_limit_control import DomainRateLimitState
from knowledge_ingest.domain_selectors import DomainRateLimitWriteKind
from knowledge_ingest.reason_codes import FetchReasonCode

START_URL = "https://intermedia.com/support"
DOMAIN = "intermedia.com"

_NO_OVERRIDE = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _page(url: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown="Real content here.",
        raw_markdown="Real content here.",
        html="<html></html>",
        word_count=10,
        success=True,
    )


async def _run(
    *,
    crawl_site_mock: AsyncMock,
    get_state_mock: AsyncMock,
    save_state_mock: AsyncMock,
    rate_limit: float = 2.0,
) -> None:
    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new=crawl_site_mock),
        patch(
            "knowledge_ingest.adapters.crawler.get_domain_rate_limit_state",
            new=get_state_mock,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.save_domain_rate_limit_state",
            new=save_state_mock,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._build_link_graph",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new=AsyncMock(return_value=None),
        ),
    ):
        await run_crawl_job(
            _mock_conn(),
            job_id="job-1",
            org_id="org-1",
            kb_slug="support",
            start_url=START_URL,
            rate_limit=rate_limit,
        )


def _outcomes(*reason_codes: str) -> list[dict]:
    return [
        {
            "url": f"https://intermedia.com/support/{i}",
            "reason_code": reason_code,
            "status_code": 200 if reason_code == FetchReasonCode.SUCCESS.value else None,
            "content_length": 100 if reason_code == FetchReasonCode.SUCCESS.value else 0,
        }
        for i, reason_code in enumerate(reason_codes)
    ]


@pytest.mark.asyncio
async def test_rate_limited_outcome_lowers_the_domain_rate_limit() -> None:
    """A crawl whose outcomes cross the congestion ratio threshold (30%
    RATE_LIMITED here, above the 25% default) must halve and persist the
    rate for this domain (floor 0.2), starting from the rate_limit
    actually used for this run. 7 SUCCESS + 3 RATE_LIMITED = 10 attempts,
    at/above crawl_circuit_breaker_min_attempts so the ratio verdict is
    definitive."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            _outcomes(
                *([FetchReasonCode.SUCCESS.value] * 7 + [FetchReasonCode.RATE_LIMITED.value] * 3)
            ),
        )
    )
    get_state = AsyncMock(return_value=_NO_OVERRIDE)  # no prior override
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
        rate_limit=2.0,
    )

    save_state.assert_awaited_once()
    args = save_state.await_args
    assert args.args[1] == DOMAIN
    assert args.args[2] == "org-1"
    assert args.kwargs["expected_state"] == _NO_OVERRIDE
    assert args.kwargs["kind"] is DomainRateLimitWriteKind.CONGESTION
    new_state = args.kwargs["state"]
    assert new_state.rate_limit == pytest.approx(1.0)  # 2.0 / 2
    assert new_state.clean_streak == 0
    assert new_state.last_congestion_at is not None


@pytest.mark.asyncio
async def test_blocked_anti_bot_outcome_also_lowers_the_domain_rate_limit() -> None:
    """BLOCKED_ANTI_BOT is the sibling trigger to RATE_LIMITED — a site
    that anti-bot-blocked us at a congesting ratio should also be paced
    down next time. 7 SUCCESS + 3 BLOCKED_ANTI_BOT = 10 attempts, 30%
    ratio, above the 25% default threshold."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            _outcomes(
                *(
                    [FetchReasonCode.SUCCESS.value] * 7
                    + [FetchReasonCode.BLOCKED_ANTI_BOT.value] * 3
                )
            ),
        )
    )
    get_state = AsyncMock(return_value=_NO_OVERRIDE)
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
    )

    save_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthy_crawl_with_no_override_does_not_touch_the_domain_rate_limit() -> None:
    """No RATE_LIMITED / BLOCKED_ANTI_BOT signal, no stored override →
    nothing written. A healthy site is never touched by the adaptive
    throttle, and no row is created for it. 10x SUCCESS so this is
    unambiguously a genuinely-healthy, definitively-judged crawl — not
    merely too small to judge."""
    crawl_site = AsyncMock(
        return_value=([_page(START_URL)], _outcomes(*([FetchReasonCode.SUCCESS.value] * 10)))
    )
    get_state = AsyncMock(return_value=_NO_OVERRIDE)
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
    )

    save_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_previously_lowered_rate_is_applied_on_the_next_crawl() -> None:
    """A stored override for this domain must be used INSTEAD OF the
    caller's default rate_limit, on both crawl_site call sites."""
    crawl_site = AsyncMock(
        return_value=([_page(START_URL)], _outcomes(FetchReasonCode.SUCCESS.value))
    )
    get_state = AsyncMock(
        return_value=DomainRateLimitState(
            rate_limit=0.5,
            clean_streak=0,
            # A real, recent timestamp — NOT None. A null last_congestion_at
            # decays the override immediately at read time (see
            # apply_domain_rate_limit_decay and its dedicated test), which
            # would defeat this test's whole premise. That decay behavior
            # has its own coverage below
            # (test_a_stored_override_with_no_congestion_timestamp_decays_before_the_crawl_starts).
            last_congestion_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
        rate_limit=2.0,  # caller's default — must be overridden
    )

    crawl_site.assert_awaited_once()
    assert crawl_site.await_args.kwargs["rate_limit"] == 0.5


@pytest.mark.asyncio
async def test_lowering_never_goes_below_the_floor() -> None:
    """Repeated lowering on an already-low stored rate must not asymptote
    toward zero — floor is 0.2 req/s."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            _outcomes(
                *([FetchReasonCode.SUCCESS.value] * 7 + [FetchReasonCode.RATE_LIMITED.value] * 3)
            ),
        )
    )
    get_state = AsyncMock(
        return_value=DomainRateLimitState(
            rate_limit=0.3,
            clean_streak=0,
            # Real, recent timestamp — a None here would decay the override
            # away before this test's congestion signal even gets a chance
            # to fire. See the comment on the previous test.
            last_congestion_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
        rate_limit=2.0,
    )

    save_state.assert_awaited_once()
    assert save_state.await_args.kwargs["state"].rate_limit == pytest.approx(0.2)
    assert save_state.await_args.kwargs["kind"] is DomainRateLimitWriteKind.CONGESTION


@pytest.mark.asyncio
async def test_a_clean_crawl_past_cooldown_raises_one_step_and_persists() -> None:
    """Integration-level: a domain with a stored override and a last
    congestion outside the cooldown window gets raised by exactly one step
    on a single definitive non-congested crawl — there is no accumulated-
    streak threshold gating the raise any more (see
    domain_rate_limit_control) — and the raised state is actually written
    back, not just computed."""
    crawl_site = AsyncMock(
        return_value=([_page(START_URL)], _outcomes(*([FetchReasonCode.SUCCESS.value] * 10)))
    )
    get_state = AsyncMock(
        return_value=DomainRateLimitState(
            rate_limit=0.5,
            clean_streak=60,
            last_congestion_at=datetime.now(UTC) - timedelta(hours=48),
        )
    )
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
        rate_limit=2.0,
    )

    save_state.assert_awaited_once()
    new_state = save_state.await_args.kwargs["state"]
    assert save_state.await_args.kwargs["kind"] is DomainRateLimitWriteKind.RECOVERY
    assert new_state.rate_limit == pytest.approx(1.0)  # 0.5 + (2.0 * 0.25) step
    # Incremented, not reset — only congestion or a full override-clear
    # reset clean_streak; a partial raise carries it forward.
    assert new_state.clean_streak == 61


@pytest.mark.asyncio
async def test_a_clean_job_within_cooldown_does_not_raise_despite_a_large_streak() -> None:
    """Hysteresis wired end-to-end: even with a large accumulated streak and
    a definitive clean verdict this job, a congestion inside the cooldown
    window blocks the raise — the persisted state keeps the same (lowered)
    rate_limit."""
    crawl_site = AsyncMock(
        return_value=([_page(START_URL)], _outcomes(*([FetchReasonCode.SUCCESS.value] * 10)))
    )
    get_state = AsyncMock(
        return_value=DomainRateLimitState(
            rate_limit=0.5,
            clean_streak=60,
            last_congestion_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
        rate_limit=2.0,
    )

    save_state.assert_awaited_once()
    new_state = save_state.await_args.kwargs["state"]
    assert new_state.rate_limit == pytest.approx(0.5)  # unchanged — still throttled
    assert new_state.clean_streak == 61  # 60 stored + 1 SUCCESS this job


@pytest.mark.asyncio
async def test_a_stored_override_with_no_congestion_timestamp_decays_before_the_crawl_starts() -> (
    None
):
    """Decay wiring proven end-to-end, not just unit-tested in isolation
    (see apply_domain_rate_limit_decay's own tests in
    tests/test_domain_rate_limit_control.py): a stored override with
    ``last_congestion_at IS NULL`` (the www.intermedia.com production
    case) must be cleared BEFORE crawl_site is invoked — so the job's own
    default rate_limit is used, not the stale override — and that clear
    must be PERSISTED, not just applied in-memory for this one job."""
    crawl_site = AsyncMock(
        return_value=([_page(START_URL)], _outcomes(FetchReasonCode.SUCCESS.value))
    )
    get_state = AsyncMock(
        return_value=DomainRateLimitState(rate_limit=0.5, clean_streak=0, last_congestion_at=None)
    )
    save_state = AsyncMock(return_value=True)

    await _run(
        crawl_site_mock=crawl_site,
        get_state_mock=get_state,
        save_state_mock=save_state,
        rate_limit=2.0,
    )

    crawl_site.assert_awaited_once()
    assert crawl_site.await_args.kwargs["rate_limit"] == 2.0  # decay cleared the stale override

    decay_persisted = any(
        call.kwargs["state"].rate_limit is None
        and call.kwargs["kind"] is DomainRateLimitWriteKind.DECAY
        for call in save_state.await_args_list
    )
    assert decay_persisted
