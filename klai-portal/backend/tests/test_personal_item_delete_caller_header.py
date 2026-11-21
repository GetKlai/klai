"""The personal-item delete forwards the caller service knowledge-ingest asserts on.

knowledge-ingest checks that the user belongs to the organisation before it
removes a personal item, and that check needs ``X-Caller-Service``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


@pytest.mark.asyncio
async def test_personal_item_delete_sends_caller_service_and_user(monkeypatch) -> None:
    from app.api import knowledge

    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        knowledge.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(_handler), **kwargs),
    )
    monkeypatch.setattr(knowledge, "_load_org_or_500", AsyncMock(return_value=SimpleNamespace(zitadel_org_id="z-org")))

    result = await knowledge.delete_personal_item(
        "artifact-1", perms=SimpleNamespace(org_id=1, user_id="user-1"), db=AsyncMock()
    )

    assert result == {"status": "ok"}
    (request,) = seen
    assert request.method == "DELETE"
    assert request.headers["X-Caller-Service"] == "portal-api"
    assert dict(request.url.params) == {"org_id": "z-org", "user_id": "user-1"}
