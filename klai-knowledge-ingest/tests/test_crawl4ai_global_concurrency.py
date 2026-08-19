"""SPEC-CRAWL-001 AC-13 — global Crawl4AI request capacity."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.config import settings


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"results": []}


class _BlockingClient:
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release
        self.active = 0
        self.max_active = 0

    async def post(self, *_args: Any, **_kwargs: Any) -> _Response:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.release.wait()
            return _Response()
        finally:
            self.active -= 1


def test_global_crawl4ai_request_cap_defaults_to_one() -> None:
    assert settings.crawl4ai_max_concurrent_requests == 1


@pytest.mark.asyncio
async def test_global_cap_bounds_concurrent_posts_across_different_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 2
    release = asyncio.Event()
    client = _BlockingClient(release)
    monkeypatch.setattr(
        crawl4ai_client,
        "_crawl4ai_request_semaphore",
        asyncio.BoundedSemaphore(cap),
    )

    tasks = [
        asyncio.create_task(
            crawl4ai_client._crawl_sync(client, {"urls": [f"https://host-{i}.example/"]})
        )
        for i in range(5)
    ]

    for _ in range(20):
        if client.active == cap:
            break
        await asyncio.sleep(0)

    assert client.active == cap
    assert client.max_active == cap
    release.set()
    await asyncio.gather(*tasks)
    assert client.max_active == cap
