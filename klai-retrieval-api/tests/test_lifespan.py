"""Resource cleanup contract for retrieval-api lifespan."""

from unittest.mock import AsyncMock

import pytest

from retrieval_api import main
from retrieval_api.services import events, graph_search


@pytest.mark.asyncio
async def test_lifespan_closes_graphiti_and_pool_after_request_failure(monkeypatch) -> None:
    init_pool = AsyncMock()
    close_pool = AsyncMock()
    close_graphiti = AsyncMock()
    monkeypatch.setattr(events, "init_pool", init_pool)
    monkeypatch.setattr(events, "close_pool", close_pool)
    monkeypatch.setattr(graph_search, "close", close_graphiti)
    monkeypatch.setattr(main, "_warmup_reranker", AsyncMock())

    with pytest.raises(RuntimeError, match="request failed"):
        async with main.lifespan(main.app):
            raise RuntimeError("request failed")

    init_pool.assert_awaited_once_with()
    close_graphiti.assert_awaited_once_with()
    close_pool.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_lifespan_still_closes_pool_when_graphiti_close_fails(monkeypatch) -> None:
    close_pool = AsyncMock()
    close_graphiti = AsyncMock(side_effect=RuntimeError("graph close failed"))
    monkeypatch.setattr(events, "init_pool", AsyncMock())
    monkeypatch.setattr(events, "close_pool", close_pool)
    monkeypatch.setattr(graph_search, "close", close_graphiti)
    monkeypatch.setattr(main, "_warmup_reranker", AsyncMock())

    with pytest.raises(RuntimeError, match="graph close failed"):
        async with main.lifespan(main.app):
            pass

    close_pool.assert_awaited_once_with()
