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

Most tests mock ``crawl_site`` to isolate adapter wiring. The Deel B
regression deliberately keeps the real ``crawl_site`` state machine and
mocks only its network/checkpoint boundaries, proving that every in-job
halving reaches persistence. The pure regelwet itself (hysteresis, floor,
ceiling, table-cleanliness edge cases, decay) is covered exhaustively,
without mocking, in tests/test_domain_rate_limit_control.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.adapters.crawler import (
    _apply_domain_rate_limit_effect_once,
    run_crawl_job,
)
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, CrawlResult
from knowledge_ingest.crawl_checkpoint import CrawlExecutionSuperseded
from knowledge_ingest.domain_rate_limit_control import DomainRateLimitState
from knowledge_ingest.domain_selectors import DomainRateLimitWriteKind
from knowledge_ingest.reason_codes import FetchReasonCode
from tests.conftest import connection_factory_for

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
    conn: MagicMock | None = None,
    ingest_mock: AsyncMock | None = None,
) -> MagicMock:
    actual_conn = conn or _mock_conn()
    actual_ingest = ingest_mock or AsyncMock(return_value=None)
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
            new=actual_ingest,
        ),
    ):
        await run_crawl_job(
            connection_factory=connection_factory_for(actual_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="support",
            start_url=START_URL,
            rate_limit=rate_limit,
        )
    return actual_conn


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
async def test_aug_19_replayed_crawl_applies_rate_limit_effect_once() -> None:
    conn = _mock_conn()
    conn.execute = AsyncMock(side_effect=["UPDATE 1", "UPDATE 0"])
    conn.fetchrow = AsyncMock(
        return_value={
            "execution_generation": 123,
            "status": "running",
            "rate_limit_effect_applied": True,
        }
    )
    save_state = AsyncMock(return_value=True)
    outcomes = _outcomes(*([FetchReasonCode.RATE_LIMITED.value] * 10))

    with patch(
        "knowledge_ingest.adapters.crawler.save_domain_rate_limit_state",
        new=save_state,
    ):
        for _attempt in range(2):
            await _apply_domain_rate_limit_effect_once(
                conn,
                job_id="job-1",
                org_id="org-1",
                domain=DOMAIN,
                execution_generation=123,
                fetch_outcomes=outcomes,
                domain_rate_limit_state=_NO_OVERRIDE,
                effective_rate_limit=2.0,
                default_rate_limit=2.0,
            )

    save_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_aug_19_stale_aimd_attempt_stops_on_generation_miss() -> None:
    conn = _mock_conn()
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchrow = AsyncMock(
        return_value={
            "execution_generation": 124,
            "status": "running",
            "rate_limit_effect_applied": False,
        }
    )

    with pytest.raises(CrawlExecutionSuperseded):
        await _apply_domain_rate_limit_effect_once(
            conn,
            job_id="job-1",
            org_id="org-1",
            domain=DOMAIN,
            execution_generation=123,
            fetch_outcomes=_outcomes(FetchReasonCode.SUCCESS.value),
            domain_rate_limit_state=_NO_OVERRIDE,
            effective_rate_limit=2.0,
            default_rate_limit=2.0,
        )


@pytest.mark.asyncio
async def test_aug_19_page_side_effects_hold_advisory_lock_until_progress_commit() -> None:
    conn = _mock_conn()
    advisory_lock_depth = 0

    async def _execute(query: str, *_args) -> str | None:
        nonlocal advisory_lock_depth
        if "pg_advisory_lock(" in query:
            advisory_lock_depth += 1
            return "SELECT 1"
        if "pg_advisory_unlock(" in query:
            advisory_lock_depth -= 1
            return "SELECT 1"
        return None

    conn.execute = AsyncMock(side_effect=_execute)

    async def _fetchval(query: str, *_args) -> bool | None:
        nonlocal advisory_lock_depth
        if "pg_try_advisory_lock" in query:
            advisory_lock_depth += 1
            return True
        return None

    conn.fetchval = AsyncMock(side_effect=_fetchval)

    async def _assert_fenced_ingest(*_args, **_kwargs) -> None:
        assert advisory_lock_depth > 0

    await _run(
        crawl_site_mock=AsyncMock(
            return_value=([_page(START_URL)], _outcomes(FetchReasonCode.SUCCESS.value))
        ),
        get_state_mock=AsyncMock(return_value=_NO_OVERRIDE),
        save_state_mock=AsyncMock(return_value=True),
        conn=conn,
        ingest_mock=AsyncMock(side_effect=_assert_fenced_ingest),
    )

    progress_query = next(
        call.args[0]
        for call in conn.execute.await_args_list
        if call.args and "SET pages_done" in call.args[0]
    )
    assert "execution_generation" in progress_query
    assert "status='running'" in progress_query
    assert advisory_lock_depth == 0


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
async def test_in_job_slowdown_rate_is_persisted_for_the_next_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persisted rate must include every halving performed by Deel B."""
    urls = [f"https://intermedia.com/support/{index}" for index in range(5)]

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=start_url,
            fit_markdown="Seed content",
            raw_markdown="Seed content",
            html="<html></html>",
            word_count=2,
            success=True,
            links={"internal": [{"href": url, "text": ""} for url in urls]},
        )

    async def _no_sitemap(_start_url: str) -> list[str]:
        return []

    observed_rates: list[float | None] = []

    async def _fake_bulk_fetch(
        *, urls: list[str], rate_limit: float | None, **_kwargs: Any
    ) -> ChunkedFetchResult:
        observed_rates.append(rate_limit)
        if len(observed_rates) <= 3:
            return ChunkedFetchResult(
                raw_results=[
                    {
                        "url": urls[0],
                        "success": False,
                        "status_code": 429,
                        "error_message": "Too Many Requests",
                        "markdown": "",
                        "html": "",
                    }
                ],
                not_attempted=urls[1:],
                stopped_early=True,
                stop_trigger_reason_code=FetchReasonCode.RATE_LIMITED.value,
            )
        return ChunkedFetchResult(
            raw_results=[
                {
                    "url": url,
                    "success": True,
                    "status_code": 200,
                    "markdown": "Recovered content",
                    "html": "<html></html>",
                    "links": {"internal": []},
                }
                for url in urls
            ]
        )

    async def _no_sleep(_seconds: float) -> None:
        return None

    class _NoopCheckpoint:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def load(self) -> None:
            return None

        async def save(self, _snapshot: dict[str, Any]) -> None:
            return None

        async def ensure_active(self) -> None:
            return None

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)
    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)
    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_bulk_fetch)
    monkeypatch.setattr(crawl4ai_client, "_slowdown_sleep", _no_sleep)

    conn = _mock_conn()
    get_state = AsyncMock(return_value=_NO_OVERRIDE)
    save_state = AsyncMock(return_value=True)
    with (
        patch(
            "knowledge_ingest.adapters.crawler.PostgresCrawlCheckpoint",
            new=_NoopCheckpoint,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.get_domain_rate_limit_state",
            new=get_state,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.save_domain_rate_limit_state",
            new=save_state,
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
            connection_factory=connection_factory_for(conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="support",
            start_url=START_URL,
            rate_limit=2.0,
        )

    assert observed_rates == [2.0, 1.0, 0.5, 0.25]
    persisted_state = save_state.await_args.kwargs["state"]
    assert persisted_state.rate_limit == pytest.approx(0.25)


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
