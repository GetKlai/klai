"""Search survives a restart of the dense embedding service, and says so when it cannot.

The dense service runs on the GPU server, which restarts for updates. On
2026-11-23 11:51Z a search arrived as it came back, the connection dropped
("Server disconnected") and /retrieve answered an unhandled 500. A dropped
connection or a 5xx is now retried for a short, bounded time; a service that
stays down gives a 503 ``embedding_unavailable`` the chat layer presents as an
unreachable knowledge base.
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
    state = SimpleNamespace(down_for=0, failure="refuse", calls=0, slept=[])

    def _answer(request: httpx.Request) -> httpx.Response:
        state.calls += 1
        if state.down_for:
            state.down_for -= 1
            if state.failure == "refuse":
                raise httpx.RemoteProtocolError("Server disconnected", request=request)
            return httpx.Response(state.failure)
        return httpx.Response(200, json={"data": [{"embedding": _VECTOR, "index": 0}]})

    async def _sleep(seconds: float) -> None:
        state.slept.append(seconds)

    transport = httpx.MockTransport(_answer)
    real_client = httpx.AsyncClient
    with (
        patch.object(
            tei.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
        ),
        patch.object(tei.asyncio, "sleep", _sleep),
    ):
        yield state


@pytest.mark.parametrize("failure", ["refuse", 503])
async def test_a_restart_shorter_than_the_budget_is_ridden_out(dense, failure):
    dense.failure, dense.down_for = failure, 3

    assert await tei.embed_single("q") == _VECTOR
    assert dense.calls == 4
    assert sum(dense.slept) <= 3.0


async def test_a_service_that_stays_down_raises_embedding_unavailable(dense):
    dense.down_for = 10**6

    with pytest.raises(tei.EmbeddingUnavailable):
        await tei.embed_single("q")
    assert dense.calls == len(tei._RETRY_DELAYS_S) + 1
    assert sum(dense.slept) <= 3.0


async def test_a_client_error_is_not_retried(dense):
    dense.failure, dense.down_for = 400, 1

    with pytest.raises(httpx.HTTPStatusError):
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
