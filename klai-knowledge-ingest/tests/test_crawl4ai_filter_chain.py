"""Test that crawl_site builds a JSON-deserialisable filter_chain payload.

Crawl4AI v0.8.6's `from_serializable_dict` only instantiates nested strategy
objects when they are wrapped in `{"type": "<ClassName>", "params": {...}}`.
A bare list of filter dicts stays a list, and BFSDeepCrawlStrategy then crashes
with `AttributeError: 'list' object has no attribute 'apply'` the moment
`self.filter_chain.apply(url)` is called for depth > 0.

This test pins the payload structure so any regression surfaces in CI instead
of a live sync failure (as happened on wiki.redcactus.cloud /nl/).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest import crawl4ai_client


@pytest.mark.asyncio
async def test_crawl_site_wraps_filter_chain_when_include_patterns_set() -> None:
    captured: dict[str, Any] = {}

    async def _fake_post(self, url: str, json: dict[str, Any], headers: dict[str, str]):
        captured["payload"] = json
        # Surface-level status ok so _DEEP_POLL flow can start; we raise afterwards
        # so the test does not need to simulate the poll loop.
        raise RuntimeError("short-circuit after capturing payload")

    with patch("httpx.AsyncClient.post", new=_fake_post):
        with pytest.raises(RuntimeError, match="short-circuit"):
            await crawl4ai_client.crawl_site(
                start_url="https://wiki.redcactus.cloud",
                selector="main",
                max_depth=2,
                max_pages=50,
                include_patterns=["/nl/"],
            )

    payload = captured["payload"]
    deep = payload["crawler_config"]["params"]["deep_crawl_strategy"]

    assert deep["type"] == "BFSDeepCrawlStrategy"
    filter_chain = deep["params"].get("filter_chain")

    # RED assertion: filter_chain MUST be wrapped in {type:FilterChain, params:{filters:[...]}}
    # so crawl4ai's from_serializable_dict builds a real FilterChain object.
    assert isinstance(filter_chain, dict), (
        f"filter_chain must be a typed object wrapper, got {type(filter_chain).__name__}"
    )
    assert filter_chain.get("type") == "FilterChain"
    filters = filter_chain["params"]["filters"]
    assert isinstance(filters, list) and len(filters) == 1
    assert filters[0]["type"] == "URLPatternFilter"
    assert filters[0]["params"]["patterns"] == ["/nl/"]


@pytest.mark.asyncio
async def test_crawl_site_omits_filter_chain_when_no_include_patterns() -> None:
    captured: dict[str, Any] = {}

    async def _fake_post(self, url: str, json: dict[str, Any], headers: dict[str, str]):
        captured["payload"] = json
        raise RuntimeError("short-circuit")

    with patch("httpx.AsyncClient.post", new=_fake_post):
        with pytest.raises(RuntimeError):
            await crawl4ai_client.crawl_site(
                start_url="https://example.com",
                max_depth=1,
                max_pages=10,
                include_patterns=None,
            )

    deep = captured["payload"]["crawler_config"]["params"]["deep_crawl_strategy"]
    # When no include_patterns, no filter_chain should be emitted at all.
    assert "filter_chain" not in deep["params"]
