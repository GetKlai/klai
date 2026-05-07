"""fire_gap_event behaviour tests.

SPEC-KB-014 + SPEC-MCP-RETRIEVAL-001 REQ-9.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from klai_retrieval_telemetry import RetrievalTelemetryConfig, fire_gap_event


def _cfg() -> RetrievalTelemetryConfig:
    return RetrievalTelemetryConfig(
        portal_api_url="http://portal-api:8000",
        portal_internal_secret="test-secret",
        portal_retrieval_log_url="http://portal-api:8000/internal/v1/retrieval-log",
        portal_gap_events_url="http://portal-api:8000/internal/v1/gap-events",
    )


def test_skip_silently_when_no_event_loop() -> None:
    fire_gap_event(
        org_id="42",
        user_id="user-1",
        query_text="test",
        gap_type="hard",
        chunks=[],
        retrieval_ms=42,
        config=_cfg(),
    )


def test_skip_silently_when_org_id_non_numeric() -> None:
    with patch("asyncio.get_running_loop") as mock_loop:
        fire_gap_event(
            org_id="bogus",
            user_id="user-1",
            query_text="test",
            gap_type="hard",
            chunks=[],
            retrieval_ms=42,
            config=_cfg(),
        )
    mock_loop.assert_not_called()


@pytest.mark.asyncio
async def test_payload_shape_hard_gap_no_chunks() -> None:
    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return MagicMock(status_code=201)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        fire_gap_event(
            org_id="42",
            user_id="user-1",
            query_text="impossible question",
            gap_type="hard",
            chunks=[],
            retrieval_ms=123,
            config=_cfg(),
        )
        await asyncio.sleep(0)

    assert captured["url"] == "http://portal-api:8000/internal/v1/gap-events"
    assert captured["json"]["org_id"] == 42  # int, not str
    assert captured["json"]["gap_type"] == "hard"
    assert captured["json"]["chunks_retrieved"] == 0
    assert captured["json"]["retrieval_ms"] == 123
    assert captured["json"]["top_score"] is None
    assert captured["json"]["nearest_kb_slug"] is None
    assert "caller_client_id" not in captured["json"]


@pytest.mark.asyncio
async def test_payload_shape_soft_gap_with_chunks() -> None:
    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return MagicMock(status_code=201)

    chunks = [
        {"reranker_score": 0.1, "metadata": {"kb_slug": "support"}},
        {"reranker_score": 0.2, "metadata": {"kb_slug": "support"}},
    ]
    with patch("httpx.AsyncClient.post", new=_fake_post):
        fire_gap_event(
            org_id="42",
            user_id="user-1",
            query_text="weak match",
            gap_type="soft",
            chunks=chunks,
            retrieval_ms=200,
            config=_cfg(),
        )
        await asyncio.sleep(0)

    # Top-chunk highest reranker = 0.2
    assert captured["json"]["top_score"] == 0.2
    assert captured["json"]["nearest_kb_slug"] == "support"
    assert captured["json"]["chunks_retrieved"] == 2


@pytest.mark.asyncio
async def test_caller_client_id_propagates_to_payload() -> None:
    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return MagicMock(status_code=201)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        fire_gap_event(
            org_id="42",
            user_id="user-1",
            query_text="q",
            gap_type="hard",
            chunks=[],
            retrieval_ms=42,
            caller_client_id="cursor",
            config=_cfg(),
        )
        await asyncio.sleep(0)

    assert captured["json"]["caller_client_id"] == "cursor"


@pytest.mark.asyncio
async def test_taxonomy_node_ids_propagate_when_present() -> None:
    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return MagicMock(status_code=201)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        fire_gap_event(
            org_id="42",
            user_id="user-1",
            query_text="q",
            gap_type="hard",
            chunks=[],
            retrieval_ms=42,
            taxonomy_node_ids=[7, 11],
            config=_cfg(),
        )
        await asyncio.sleep(0)

    assert captured["json"]["taxonomy_node_ids"] == [7, 11]


@pytest.mark.asyncio
async def test_post_failure_swallowed() -> None:
    async def _failing_post(self, url, **kwargs):
        raise RuntimeError("simulated network error")

    with patch("httpx.AsyncClient.post", new=_failing_post):
        fire_gap_event(
            org_id="42",
            user_id="user-1",
            query_text="q",
            gap_type="hard",
            chunks=[],
            retrieval_ms=42,
            config=_cfg(),
        )
        await asyncio.sleep(0)
