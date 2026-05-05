"""SPEC-INFRA-TENANT-DELETE-002 G6 -- wipe-state endpoint tests.

Coverage:
- Test 1: 5 sync_runs for org tenant-a + 3 for tenant-b + 2 NULL-org rows
  -> POST wipe-state for tenant-a returns 200, rows_deleted=5;
  tenant-b and NULL-org rows are preserved.
- Test 2: Re-call (idempotency) returns rows_deleted=0.
- Test 3: Non-portal caller (from_portal=False) returns 403.
- Test 4: NULL-org legacy rows are intentionally preserved -- documented
  behaviour, not a bug.

Test design mirrors test_sync_routes_org_scoping.py (FakeSession pattern).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.database import get_session
from app.routes.internal import router as internal_router

_ORG_A = "tenant-a"
_ORG_B = "tenant-b"


class _FakeDeleteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows: list[dict[str, Any]] = list(rows or [])

    async def execute(self, stmt: Any) -> _FakeDeleteResult:
        import re

        try:
            from sqlalchemy.dialects import postgresql

            compiled = stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
            sql_str = str(compiled)
            match = re.search(
                r"WHERE\s+.*?org_id\s*=\s*'([^']*)'",
                sql_str,
                re.IGNORECASE,
            )
            target_org_id: str | None = match.group(1) if match else None
        except Exception:
            target_org_id = None

        before = len(self._rows)
        if target_org_id is not None:
            self._rows = [r for r in self._rows if r.get("org_id") != target_org_id]
        deleted = before - len(self._rows)
        return _FakeDeleteResult(rowcount=deleted)

    async def commit(self) -> None:
        return None

    def remaining_rows(self) -> list[dict[str, Any]]:
        return list(self._rows)


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: _FakeSession,
    bypass_portal_check: bool = True,
) -> TestClient:
    app = FastAPI()
    app.include_router(internal_router)

    async def _override_get_session() -> AsyncIterator[_FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _override_get_session

    if bypass_portal_check:

        def _noop(_request: Any) -> None:
            return None

        monkeypatch.setattr("app.routes.internal._require_portal_call", _noop)

    return TestClient(app, raise_server_exceptions=False)


def test_wipe_state_deletes_only_target_org_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, Any]] = (
        [{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(5)]
        + [{"id": str(uuid.uuid4()), "org_id": _ORG_B} for _ in range(3)]
        + [{"id": str(uuid.uuid4()), "org_id": None} for _ in range(2)]
    )
    session = _FakeSession(rows=rows)
    client = _build_client(monkeypatch, session=session)

    resp = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["rows_deleted"] == 5, f"expected 5 rows deleted, got {body[chr(39) + 'rows_deleted' + chr(39)]}"
    assert body["status"] == "ok"

    remaining = session.remaining_rows()
    assert len(remaining) == 5, f"expected 5 remaining rows, got {len(remaining)}"
    assert all(r["org_id"] != _ORG_A for r in remaining)
    assert len([r for r in remaining if r["org_id"] == _ORG_B]) == 3
    assert len([r for r in remaining if r["org_id"] is None]) == 2


def test_wipe_state_idempotent_second_call_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, Any]] = [{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(3)]
    session = _FakeSession(rows=rows)
    client = _build_client(monkeypatch, session=session)

    resp1 = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")
    assert resp1.status_code == 200
    assert resp1.json()["rows_deleted"] == 3

    resp2 = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")
    assert resp2.status_code == 200
    assert resp2.json()["rows_deleted"] == 0
    assert resp2.json()["status"] == "ok"


class _FakeAuthMiddlewareNonPortal(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.from_portal = False
        request.state.org_id = "some-org"
        return await call_next(request)


def test_wipe_state_non_portal_caller_returns_403() -> None:
    app = FastAPI()
    app.add_middleware(_FakeAuthMiddlewareNonPortal)
    app.include_router(internal_router)

    async def _override_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    app.dependency_overrides[get_session] = _override_get_session
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        f"/internal/v1/orgs/{_ORG_A}/wipe-state",
        headers={"Authorization": "Bearer wrong-secret"},
    )

    assert resp.status_code == 403
    assert resp.json() == {"detail": "Portal service token required"}


def test_wipe_state_preserves_null_org_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    null_rows: list[dict[str, Any]] = [{"id": str(uuid.uuid4()), "org_id": None} for _ in range(4)]
    session = _FakeSession(rows=null_rows)
    client = _build_client(monkeypatch, session=session)

    resp = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")

    assert resp.status_code == 200
    assert resp.json()["rows_deleted"] == 0
    assert len(session.remaining_rows()) == 4
