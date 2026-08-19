"""SPEC-CRAWL-001 amendment — process-wide weighted host pacing."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.domain_selectors import extract_domain
from knowledge_ingest.host_pacing import HostGateRegistry, crawl_gate_key


class _VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


def test_apex_and_www_share_live_gate_but_keep_distinct_storage_keys() -> None:
    assert extract_domain("https://www.example.com/page") == "www.example.com"
    assert extract_domain("https://example.com/page") == "example.com"
    assert crawl_gate_key("https://www.example.com/page") == "example.com"
    assert crawl_gate_key("https://example.com/page") == "example.com"


@pytest.mark.asyncio
async def test_aug_18_0927_same_host_crawls_share_aggregate_cadence() -> None:
    clock = _VirtualClock()
    registry = HostGateRegistry(monotonic=clock.monotonic, sleep=clock.sleep)

    async with (
        registry.session("https://www.example.com/start", 0.2) as first,
        registry.session("https://example.com/other", 0.2) as second,
    ):
        starts: list[float] = []

        async def reserve(session: object) -> None:
            weight = await session.acquire(2)  # type: ignore[attr-defined]
            assert weight == 2
            starts.append(clock.now)

        await asyncio.gather(reserve(first), reserve(second))

    assert sorted(starts) == [0.0, 10.0]
    assert registry.active_gate_count == 0


@pytest.mark.asyncio
async def test_different_hosts_do_not_share_a_pacing_schedule() -> None:
    clock = _VirtualClock()
    registry = HostGateRegistry(monotonic=clock.monotonic, sleep=clock.sleep)

    async with (
        registry.session("https://alpha.example/start", 0.2) as alpha,
        registry.session("https://beta.example/start", 0.2) as beta,
    ):
        starts: list[float] = []

        async def reserve(session: object) -> None:
            assert await session.acquire(2) == 2  # type: ignore[attr-defined]
            starts.append(clock.now)

        await asyncio.gather(reserve(alpha), reserve(beta))

    assert starts == [0.0, 0.0]


@pytest.mark.asyncio
async def test_lower_active_rate_controls_future_burst_and_repays_previous_burst() -> None:
    clock = _VirtualClock()
    registry = HostGateRegistry(monotonic=clock.monotonic, sleep=clock.sleep)

    async with registry.session("https://example.com", 2.0) as fast:
        assert await fast.acquire(100) == 20
        async with registry.session("https://www.example.com", 0.2) as slow:
            assert await slow.acquire(100) == 2

    # The previous 20-URL burst is charged against the newly effective
    # 0.2 URL/s rate before another request may start.
    assert clock.now == 100.0


@pytest.mark.asyncio
async def test_concurrent_bulk_fetches_share_the_host_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _VirtualClock()
    registry = HostGateRegistry(monotonic=clock.monotonic, sleep=clock.sleep)
    starts: list[tuple[float, int]] = []

    async def crawl_sync(_client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
        starts.append((clock.now, len(payload["urls"])))
        await asyncio.sleep(0)
        return {
            "results": [
                {"url": url, "success": True, "markdown": "content", "links": {}}
                for url in payload["urls"]
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_host_gate_registry", registry)
    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", crawl_sync)

    await asyncio.gather(
        crawl4ai_client._chunked_bulk_fetch(
            urls=["https://www.example.com/a", "https://www.example.com/b"],
            crawler_config={},
            cookies=None,
            rate_limit=0.2,
        ),
        crawl4ai_client._chunked_bulk_fetch(
            urls=["https://example.com/c", "https://example.com/d"],
            crawler_config={},
            cookies=None,
            rate_limit=0.2,
        ),
    )

    assert sorted(starts) == [(0.0, 2), (10.0, 2)]


@pytest.mark.asyncio
async def test_concurrent_bulk_fetches_for_different_hosts_start_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _VirtualClock()
    registry = HostGateRegistry(monotonic=clock.monotonic, sleep=clock.sleep)
    starts: list[float] = []

    async def crawl_sync(_client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
        starts.append(clock.now)
        await asyncio.sleep(0)
        return {
            "results": [
                {"url": url, "success": True, "markdown": "content", "links": {}}
                for url in payload["urls"]
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_host_gate_registry", registry)
    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", crawl_sync)

    await asyncio.gather(
        crawl4ai_client._chunked_bulk_fetch(
            urls=["https://alpha.example/a", "https://alpha.example/b"],
            crawler_config={},
            cookies=None,
            rate_limit=0.2,
        ),
        crawl4ai_client._chunked_bulk_fetch(
            urls=["https://beta.example/c", "https://beta.example/d"],
            crawler_config={},
            cookies=None,
            rate_limit=0.2,
        ),
    )

    assert starts == [0.0, 0.0]


@pytest.mark.asyncio
async def test_single_page_requests_also_share_the_host_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _VirtualClock()
    registry = HostGateRegistry(monotonic=clock.monotonic, sleep=clock.sleep)
    starts: list[float] = []

    class Response:
        def __init__(self, url: str) -> None:
            self.url = url

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "results": [
                    {
                        "url": self.url,
                        "success": True,
                        "markdown": "content",
                        "links": {},
                    }
                ]
            }

    async def post(
        _client: httpx.AsyncClient, _url: str, *, json: dict[str, Any], **_kwargs: Any
    ) -> Response:
        starts.append(clock.now)
        await asyncio.sleep(0)
        return Response(json["urls"][0])

    monkeypatch.setattr(crawl4ai_client, "_host_gate_registry", registry)
    monkeypatch.setattr(crawl4ai_client, "_crawl4ai_request_semaphore", asyncio.Semaphore(2))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    await asyncio.gather(
        crawl4ai_client.crawl_page("https://www.example.com/a", rate_limit=0.2),
        crawl4ai_client.crawl_page("https://example.com/b", rate_limit=0.2),
    )

    assert sorted(starts) == [0.0, 5.0]
