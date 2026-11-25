"""A write on a connection the server already closed is sent again, once.

The vector store closes an idle kept-alive connection earlier than the HTTP
client drops it, so a call landing in that gap dies with "server disconnected"
before any response. The retry sits in the transport of the shared client and
must leave exactly one set of points after a replayed document write.
"""

import json
import re

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import PointStruct, SparseVector

import knowledge_ingest.qdrant_store as qs


class _FakeVectorStore:
    """Just enough of the REST API for scroll, upsert and delete by id."""

    def __init__(self, existing: list[str], fail_first: list[Exception | int]) -> None:
        self.points: dict[str, dict] = {pid: {"id": pid} for pid in existing}
        self.fail_first = fail_first
        self.requests: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if self.fail_first and request.method == "PUT":
            failure = self.fail_first.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(failure, json={"status": {"error": "rejected"}})
        body = json.loads(request.content or b"{}")
        path = request.url.path
        if path.endswith("/points/scroll"):
            result = {"points": list(self.points.values()), "next_page_offset": None}
        elif path.endswith("/points") and request.method == "PUT":
            for point in body["points"]:
                self.points[point["id"]] = {"id": point["id"]}
            result = {"operation_id": 1, "status": "completed"}
        elif path.endswith("/points/delete"):
            for pid in body["points"]:
                self.points.pop(pid, None)
            result = {"operation_id": 2, "status": "completed"}
        else:
            raise AssertionError(f"unexpected request {request.method} {path}")
        return httpx.Response(200, json={"result": result, "status": "ok", "time": 0.0})


def _client(store: _FakeVectorStore) -> AsyncQdrantClient:
    return AsyncQdrantClient(
        url="http://qdrant.example.com:6333",
        check_compatibility=False,
        transport=qs._RetryDroppedConnectionTransport(httpx.MockTransport(store)),
    )


def _point(point_id: str) -> PointStruct:
    return PointStruct(
        id=point_id,
        vector={"vector_sparse": SparseVector(indices=[0], values=[1.0])},
        payload={},
    )


@pytest.mark.asyncio
async def test_dropped_connection_on_upsert_succeeds_on_second_try_with_one_set_of_points():
    old_id = "00000000-0000-4000-8000-000000000001"
    new_id = "00000000-0000-4000-8000-000000000002"
    store = _FakeVectorStore(
        existing=[old_id], fail_first=[httpx.RemoteProtocolError("Server disconnected")]
    )

    await qs._replace_points(_client(store), [], [_point(new_id)])

    assert set(store.points) == {new_id}
    assert sum(1 for r in store.requests if r == "PUT /collections/klai_knowledge/points") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500])
async def test_response_sent_by_the_server_is_not_retried(status):
    store = _FakeVectorStore(existing=[], fail_first=[status])

    with pytest.raises(UnexpectedResponse):
        await qs._replace_points(
            _client(store), [], [_point("00000000-0000-4000-8000-000000000003")]
        )

    assert len([r for r in store.requests if r.startswith("PUT")]) == 1


@pytest.mark.asyncio
async def test_a_second_dropped_connection_is_raised_not_swallowed():
    store = _FakeVectorStore(
        existing=[],
        fail_first=[httpx.ReadError("reset"), httpx.ReadError("reset")],
    )

    with pytest.raises(Exception, match=re.compile("reset|ResponseHandling", re.I)):
        await qs._replace_points(
            _client(store), [], [_point("00000000-0000-4000-8000-000000000004")]
        )
