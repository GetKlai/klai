# pyright: basic
"""fire_retrieval_log behaviour tests.

SPEC-KB-015 + SPEC-MCP-RETRIEVAL-001 REQ-9.

Pyright basic mode for tests: MagicMock + monkey-patched ``httpx.AsyncClient.post``
produces unsolvable strict-mode types (mock fixtures, partial unknown args). The
production code in ``klai_retrieval_telemetry/_emit.py`` stays strict-typed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from klai_retrieval_telemetry import RetrievalTelemetryConfig, fire_retrieval_log


def _cfg() -> RetrievalTelemetryConfig:
    return RetrievalTelemetryConfig(
        portal_api_url="http://portal-api:8000",
        portal_internal_secret="test-secret",
        portal_retrieval_log_url="http://portal-api:8000/internal/v1/retrieval-log",
        portal_gap_events_url="http://portal-api:8000/internal/v1/gap-events",
    )


def test_skip_silently_when_no_event_loop() -> None:
    """Outside a running loop, the helper must NOT raise."""
    fire_retrieval_log(
        org_id="42",
        user_id="user-1",
        chunk_ids=["c1"],
        reranker_scores=[0.9],
        query_resolved="test",
        config=_cfg(),
    )


def test_skip_silently_when_org_id_non_numeric() -> None:
    """Non-numeric org_id is logged + skipped, no scheduling attempt."""
    with patch("asyncio.get_running_loop") as mock_loop:
        fire_retrieval_log(
            org_id="not-a-number",
            user_id="user-1",
            chunk_ids=["c1"],
            reranker_scores=[0.9],
            query_resolved="test",
            config=_cfg(),
        )
    # get_running_loop must NOT be called when org_id is bogus
    mock_loop.assert_not_called()


@pytest.mark.asyncio
async def test_payload_shape_without_caller_client_id() -> None:
    """LibreChat path: caller_client_id absent from payload."""
    captured: dict = {}

    async def _fake_post(self: object, url: str, **kwargs: object) -> MagicMock:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return MagicMock(status_code=201)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        fire_retrieval_log(
            org_id="42",
            user_id="user-1",
            chunk_ids=["c1", "c2"],
            reranker_scores=[0.9, 0.8],
            query_resolved="test query",
            config=_cfg(),
        )
        # Let the create_task fire
        await asyncio.sleep(0)

    assert captured["url"] == "http://portal-api:8000/internal/v1/retrieval-log"
    assert captured["json"]["org_id"] == "42"
    assert captured["json"]["user_id"] == "user-1"
    assert captured["json"]["chunk_ids"] == ["c1", "c2"]
    assert captured["json"]["reranker_scores"] == [0.9, 0.8]
    assert captured["json"]["query_resolved"] == "test query"
    assert captured["json"]["embedding_model_version"] == "bge-m3-v1"
    assert "retrieved_at" in captured["json"]
    # Critical: caller_client_id must be absent on LibreChat path
    assert "caller_client_id" not in captured["json"]
    # Auth header
    assert captured["headers"] == {"Authorization": "Bearer test-secret"}


@pytest.mark.asyncio
async def test_payload_includes_caller_client_id_when_set() -> None:
    """OAuth path: caller_client_id labels the row."""
    captured: dict = {}

    async def _fake_post(self, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return MagicMock(status_code=201)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        fire_retrieval_log(
            org_id="42",
            user_id="user-1",
            chunk_ids=["c1"],
            reranker_scores=[0.9],
            query_resolved="test",
            caller_client_id="claude-desktop",
            config=_cfg(),
        )
        await asyncio.sleep(0)

    assert captured["json"]["caller_client_id"] == "claude-desktop"


@pytest.mark.asyncio
async def test_post_failure_swallowed() -> None:
    """Network error during POST must NOT propagate to caller."""

    async def _failing_post(self, url, **kwargs):
        raise RuntimeError("simulated network error")

    with patch("httpx.AsyncClient.post", new=_failing_post):
        # Must not raise
        fire_retrieval_log(
            org_id="42",
            user_id="user-1",
            chunk_ids=["c1"],
            reranker_scores=[0.9],
            query_resolved="test",
            config=_cfg(),
        )
        await asyncio.sleep(0)
