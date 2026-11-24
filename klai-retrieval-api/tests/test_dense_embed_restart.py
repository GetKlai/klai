"""Search survives a restart of the dense embedding service, and says so when it cannot.

The dense service runs on the GPU server, which restarts for updates. On
2026-11-23 11:51Z a search arrived as it came back, the connection dropped
("Server disconnected") and /retrieve answered an unhandled 500. A dropped
connection or a 5xx is now retried within one 3 s deadline, connect timeouts
included; a service that stays down gives a 503 ``embedding_unavailable`` the
chat layer presents as an unreachable knowledge base.

Time is a fake clock: waits and hanging connects advance it instead of passing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from retrieval_api.services import tei

_VECTOR = [0.1, 0.2, 0.3]


@pytest.fixture
def dense():
    """The dense service behind tei's HTTP client; it fails the next ``down_for`` calls."""
    state = SimpleNamespace(down_for=0, failure="refuse", calls=0, now=1000.0)

    def _answer(request: httpx.Request) -> httpx.Response:
        state.calls += 1
        if state.down_for:
            state.down_for -= 1
            if state.failure == "refuse":
                raise httpx.RemoteProtocolError("Server disconnected", request=request)
            if state.failure == "hang":
                state.now += request.extensions["timeout"]["connect"]
                raise httpx.ConnectTimeout("connect timed out", request=request)
            if state.failure == "read_timeout":
                raise httpx.ReadTimeout("read timed out", request=request)
            return httpx.Response(state.failure)
        return httpx.Response(200, json={"data": [{"embedding": _VECTOR, "index": 0}]})

    async def _sleep(seconds: float) -> None:
        state.now += seconds

    transport = httpx.MockTransport(_answer)
    real_client = httpx.AsyncClient
    with (
        patch.object(
            tei.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
        ),
        patch.object(tei.asyncio, "sleep", _sleep),
        patch.object(tei.time, "monotonic", lambda: state.now),
    ):
        yield state


@pytest.mark.parametrize("failure", ["refuse", 503])
async def test_a_restart_shorter_than_the_budget_is_ridden_out(dense, failure):
    dense.failure, dense.down_for = failure, 3
    start = dense.now

    assert await tei.embed_single("q") == _VECTOR
    assert dense.calls == 4
    assert dense.now - start <= tei._RETRY_BUDGET_S


@pytest.mark.parametrize("failure", ["refuse", "hang"])
async def test_a_service_that_stays_down_raises_within_the_deadline(dense, failure):
    """Hanging connects count against the same deadline as the waits."""
    dense.failure, dense.down_for = failure, 10**6
    start = dense.now

    with pytest.raises(tei.EmbeddingUnavailable):
        await tei.embed_single("q")
    assert dense.calls > 1
    assert dense.now - start <= tei._RETRY_BUDGET_S + 0.05


@pytest.mark.parametrize(
    ("failure", "raised"),
    [(400, httpx.HTTPStatusError), ("read_timeout", httpx.ReadTimeout)],
)
async def test_a_client_error_or_a_read_timeout_is_not_retried(dense, failure, raised):
    dense.failure, dense.down_for = failure, 1

    with pytest.raises(raised):
        await tei.embed_single("q")
    assert dense.calls == 1


def test_retrieve_answers_503_embedding_unavailable_while_the_service_is_down(client, dense):
    dense.down_for = 10**6
    with (
        patch(
            "retrieval_api.api.retrieve.coreference.resolve",
            new_callable=AsyncMock,
            return_value="q",
        ),
        patch("retrieval_api.api.retrieve.embed_sparse", new_callable=AsyncMock, return_value=None),
        patch(
            "retrieval_api.services.search.embed_sparse", new_callable=AsyncMock, return_value=None
        ),
        patch("retrieval_api.api.retrieve.search.hybrid_search", new_callable=AsyncMock) as search,
    ):
        resp = client.post("/retrieve", json={"query": "q", "org_id": "org-1", "scope": "org"})

    assert resp.status_code == 503
    assert resp.json() == {"detail": "embedding_unavailable"}
    search.assert_not_awaited()
