"""The MCP consent step issues a code for the organisation of the signed-in session.

A person can belong to several organisations; the code (and the token it is
exchanged for) is bound to the membership of the organisation the session was
signed in to, and only while that membership is active. A visitor without a
session is sent through the portal sign-in and returns to the consent step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import mcp_oauth as api
from app.api.auth_bff import _safe_return_to
from app.api.session_deps import get_optional_session
from app.core.session import SessionContext

USER = "person-1"
REDIRECT_URI = "http://localhost:54321/callback"
AUTHORIZE_QUERY = {
    "response_type": "code",
    "client_id": "client-1",
    "redirect_uri": REDIRECT_URI,
    "code_challenge": "c" * 43,
    "code_challenge_method": "S256",
    "state": "client-state",
}
ORG_A = SimpleNamespace(id=1, name="Org A")
ORG_B = SimpleNamespace(id=2, name="Org B")


def _membership(org: SimpleNamespace, status: str) -> tuple[SimpleNamespace, SimpleNamespace]:
    return SimpleNamespace(id=100 + org.id, zitadel_user_id=USER, org_id=org.id, status=status, email=None), org


class _MembershipDb:
    """Answers a select on portal_users with the stored rows that match every bound equality."""

    def __init__(self, rows: list[tuple[SimpleNamespace, SimpleNamespace]]) -> None:
        self.rows = rows

    async def execute(self, stmt: Any) -> MagicMock:
        params = stmt.compile().params
        matched = [
            row for row in self.rows if all(getattr(row[0], key.rsplit("_", 1)[0]) == v for key, v in params.items())
        ]
        result = MagicMock()
        result.all.return_value = matched
        result.one_or_none.return_value = matched[0] if len(matched) == 1 else None
        return result


def _session(org_id: int | None) -> SessionContext:
    return SessionContext(
        sid="sid",
        zitadel_user_id=USER,
        access_token="at",
        csrf_token="csrf",
        access_token_expires_at=0,
        org_id=org_id,
    )


async def _call(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    session: SessionContext | None,
    rows: list[tuple[SimpleNamespace, SimpleNamespace]],
) -> tuple[Any, AsyncMock]:
    db = _MembershipDb(rows)

    class _CrossOrgSession:
        async def __aenter__(self) -> _MembershipDb:
            return db

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    client = SimpleNamespace(
        client_name="MCP client",
        redirect_uris=[REDIRECT_URI],
        application_type="native",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    redis = AsyncMock()
    redis.get.return_value = "csrf-1"
    approve = AsyncMock(return_value="code-1")
    monkeypatch.setattr(api, "cross_org_session", _CrossOrgSession)
    monkeypatch.setattr(api, "get_redis_pool", AsyncMock(return_value=redis))
    monkeypatch.setattr(api.svc, "get_client_by_id", AsyncMock(return_value=client))
    monkeypatch.setattr(api.svc, "create_auth_request", AsyncMock(return_value="request-1"))
    monkeypatch.setattr(
        api.svc,
        "fetch_auth_request",
        AsyncMock(return_value=SimpleNamespace(state="client-state", redirect_uri=REDIRECT_URI)),
    )
    monkeypatch.setattr(api.svc, "approve_auth_request", approve)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_optional_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://my.example.com") as http:
        if method == "GET":
            # Unencoded on purpose: clients may send the redirect_uri's "://" literally.
            raw_query = "&".join(f"{k}={v}" for k, v in AUTHORIZE_QUERY.items())
            response = await http.get(f"/oauth/authorize?{raw_query}")
        else:
            response = await http.post(
                "/oauth/authorize",
                data={"request_id": "request-1", "csrf_token": "csrf-1", "decision": "approve"},
            )
    return response, approve


@pytest.mark.asyncio
async def test_visitor_without_session_signs_in_and_returns_to_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    response, _ = await _call(monkeypatch, "GET", None, [])

    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.path == "/api/auth/oidc/start"
    return_to = parse_qs(location.query)["return_to"][0]
    # The sign-in start accepts the target unchanged, and it leads back to the same consent request.
    assert _safe_return_to(return_to) == return_to
    # The post-sign-in page refuses a target containing a literal "://".
    assert "://" not in return_to
    target = urlsplit(return_to)
    assert target.path == "/oauth/authorize"
    assert {k: v[0] for k, v in parse_qs(target.query).items()} == AUTHORIZE_QUERY


@pytest.mark.asyncio
async def test_consent_page_names_the_session_organisation(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_membership(ORG_B, "active"), _membership(ORG_A, "active")]

    response, _ = await _call(monkeypatch, "GET", _session(ORG_A.id), rows)

    assert response.status_code == 200
    assert "Org A" in response.text
    assert "Org B" not in response.text


@pytest.mark.asyncio
async def test_code_is_issued_for_the_session_organisation(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_membership(ORG_B, "active"), _membership(ORG_A, "active")]

    response, approve = await _call(monkeypatch, "POST", _session(ORG_A.id), rows)

    assert response.status_code == 302
    assert response.headers["location"] == f"{REDIRECT_URI}?code=code-1&state=client-state"
    approve.assert_awaited_once_with(ANY, "request-1", user_id=101, org_id=ORG_A.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("session_membership", ["suspended", "offboarded", None])
async def test_no_code_without_active_membership_in_session_organisation(
    monkeypatch: pytest.MonkeyPatch, method: str, session_membership: str | None
) -> None:
    rows = [_membership(ORG_B, "active")]
    if session_membership is not None:
        rows.append(_membership(ORG_A, session_membership))

    response, approve = await _call(monkeypatch, method, _session(ORG_A.id), rows)

    assert response.status_code == 401
    assert response.json()["error"] == "login_required"
    approve.assert_not_awaited()
