"""
Tests for knowledge_ingest.eval.retrieval_client.

RED phase: tests fail until retrieval_client.py exists.

Coverage:
  - Success path: 200 response returns RetrievalResult with chunks and retrieval_ms > 0.
  - 5xx response returns RetrievalFailure (not raises) with a reason string.
  - Timeout returns RetrievalFailure with reason "timeout".
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    """Return a minimal settings object for retrieval_client tests."""
    from unittest.mock import MagicMock

    s = MagicMock()
    s.retrieval_api_url = overrides.get("retrieval_api_url", "http://klai-retrieval-api:8000")
    s.rag_eval_retrieval_timeout = overrides.get("rag_eval_retrieval_timeout", 10)
    s.retrieval_internal_secret = overrides.get("retrieval_internal_secret", "test-secret")
    return s


class _MockTransport(httpx.AsyncBaseTransport):
    """Minimal async transport that returns a canned response."""

    def __init__(self, status_code: int, json_body: dict | None = None, raise_exc=None):
        self._status_code = status_code
        self._json_body = json_body or {}
        self._raise_exc = raise_exc

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._raise_exc is not None:
            raise self._raise_exc
        import json

        content = json.dumps(self._json_body).encode()
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=content,
            request=request,
        )


# ---------------------------------------------------------------------------
# Test 1 — success returns chunks + retrieval_ms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_success_returns_chunks() -> None:
    """200 response: RetrievalResult with chunks list and retrieval_ms > 0."""
    transport = _MockTransport(
        status_code=200,
        json_body={
            "chunks": [
                {"id": "c1", "text": "Some context about Bubble troubleshooting"},
                {"id": "c2", "text": "Browser plugin restart procedure"},
            ]
        },
    )

    from knowledge_ingest.eval.retrieval_client import RetrievalResult, retrieve_chunks

    settings = _make_settings()
    with patch("knowledge_ingest.eval.retrieval_client.settings", settings):
        result = await retrieve_chunks(
            query="Hoe troubleshoot ik Bubble?",
            org_zitadel_id="111",
            user_zitadel_id=None,
            _transport=transport,
        )

    assert isinstance(result, RetrievalResult)
    assert len(result.chunks) == 2
    assert result.chunks[0]["id"] == "c1"
    assert result.retrieval_ms >= 0


# ---------------------------------------------------------------------------
# Test 2 — 5xx response returns RetrievalFailure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_5xx_returns_failure() -> None:
    """HTTP 500: returns RetrievalFailure (not raises) with a reason string."""
    transport = _MockTransport(
        status_code=500,
        json_body={"detail": "Internal server error"},
    )

    from knowledge_ingest.eval.retrieval_client import RetrievalFailure, retrieve_chunks

    settings = _make_settings()
    with patch("knowledge_ingest.eval.retrieval_client.settings", settings):
        result = await retrieve_chunks(
            query="Test query",
            org_zitadel_id="111",
            user_zitadel_id=None,
            _transport=transport,
        )

    assert isinstance(result, RetrievalFailure)
    assert result.reason  # non-empty reason string
    assert "500" in result.reason or "error" in result.reason.lower() or result.reason


# ---------------------------------------------------------------------------
# Test 3 — timeout returns RetrievalFailure with reason "timeout"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_timeout_returns_failure() -> None:
    """ReadTimeout: returns RetrievalFailure with reason containing 'timeout'."""
    transport = _MockTransport(
        status_code=200,
        raise_exc=httpx.ReadTimeout("timed out", request=None),
    )

    from knowledge_ingest.eval.retrieval_client import RetrievalFailure, retrieve_chunks

    settings = _make_settings()
    with patch("knowledge_ingest.eval.retrieval_client.settings", settings):
        result = await retrieve_chunks(
            query="Test query",
            org_zitadel_id="111",
            user_zitadel_id=None,
            _transport=transport,
        )

    assert isinstance(result, RetrievalFailure)
    assert "timeout" in result.reason.lower()
