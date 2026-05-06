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
    """Two-table-aware fake session.

    SPEC-INFRA-TENANT-DELETE-002 G6 expansion: the wipe-state endpoint
    deletes from both `connector.sync_runs` AND `connector.connectors`.
    The fake session keeps separate row-lists per table name and routes
    DELETE statements based on the compiled SQL's table identifier.
    """

    def __init__(
        self,
        sync_run_rows: list[dict[str, Any]] | None = None,
        connector_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._sync_runs: list[dict[str, Any]] = list(sync_run_rows or [])
        self._connectors: list[dict[str, Any]] = list(connector_rows or [])

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _FakeDeleteResult:
        """Inspect the SQLAlchemy DELETE statement structurally.

        Audit 2026-05-05 finding MED 8: previous version compiled the
        statement with ``literal_binds=True`` and regex'd the resulting
        SQL string for `WHERE ... org_id = 'value'`. That approach is
        dialect-sensitive (postgresql vs sqlite render differently),
        format-sensitive (whitespace, quoting), and silently returns
        rowcount=0 if the regex misses — a test could then "pass"
        idempotency while the first call deleted nothing.

        Replaced with structural inspection via SQLAlchemy's expression
        API:
          - `isinstance(stmt, Delete)` — only DELETE statements have a
            `.table` attribute that we can map to one of our tracked
            tables; SELECT/UPDATE/INSERT no-op (return rowcount=0).
          - `stmt.table.name` — the target table identifier from the
            DELETE; no string parsing required.
          - `visitors.iterate(stmt.whereclause)` — yields every element
            in the WHERE expression tree (columns, BindParameter,
            BinaryExpression, BooleanClauseList). We search for the
            first BindParameter whose key starts with `org_id`
            (SQLAlchemy auto-generates names like `org_id_1`).
        """
        from sqlalchemy.sql import visitors
        from sqlalchemy.sql.elements import BinaryExpression, BindParameter, ColumnElement
        from sqlalchemy.sql.expression import Delete

        if not isinstance(stmt, Delete):
            # SELECT / UPDATE / INSERT — fake session does not model these.
            return _FakeDeleteResult(rowcount=0)

        table = stmt.table.name.lower()

        # Walk the WHERE clause looking for a BinaryExpression of the shape
        # `<col 'org_id'> == <BindParameter>`. SQLAlchemy 2.x auto-generates
        # bind-keys that look like ``%(<id> org_id)s`` (anonymous) — the
        # column-side identification is more reliable than matching the
        # bind-key directly.
        target_org_id: str | None = None
        if stmt.whereclause is not None:
            for elt in visitors.iterate(stmt.whereclause):
                if not isinstance(elt, BinaryExpression):
                    continue
                left = elt.left
                right = elt.right
                left_name = getattr(left, "name", None) or getattr(left, "key", None)
                if left_name == "org_id" and isinstance(right, BindParameter):
                    if right.effective_value is not None:
                        target_org_id = str(right.effective_value)
                    break
                # Also handle the inverse `<BindParameter> == <col>` shape
                # in case a future caller writes WHERE `'foo' == col` for
                # whatever reason. Defensive only — current code uses col-LHS.
                right_name = getattr(right, "name", None) or getattr(right, "key", None)
                if right_name == "org_id" and isinstance(left, BindParameter):
                    if left.effective_value is not None:
                        target_org_id = str(left.effective_value)
                    break
                # Suppress unused-import lint when the import isn't strictly
                # needed at runtime (ColumnElement is for type hints / future use).
                _ = ColumnElement

        if target_org_id is None:
            return _FakeDeleteResult(rowcount=0)

        if table == "sync_runs":
            before = len(self._sync_runs)
            self._sync_runs = [r for r in self._sync_runs if r.get("org_id") != target_org_id]
            return _FakeDeleteResult(rowcount=before - len(self._sync_runs))
        if table == "connectors":
            before = len(self._connectors)
            self._connectors = [r for r in self._connectors if r.get("org_id") != target_org_id]
            return _FakeDeleteResult(rowcount=before - len(self._connectors))
        return _FakeDeleteResult(rowcount=0)

    async def commit(self) -> None:
        return None

    def remaining_sync_runs(self) -> list[dict[str, Any]]:
        return list(self._sync_runs)

    def remaining_connectors(self) -> list[dict[str, Any]]:
        return list(self._connectors)

    # Backwards-compat shim so existing tests calling .remaining_rows() still work
    # — combines both tables into one list.
    def remaining_rows(self) -> list[dict[str, Any]]:
        return [*self._sync_runs, *self._connectors]


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
    sync_runs = (
        [{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(5)]
        + [{"id": str(uuid.uuid4()), "org_id": _ORG_B} for _ in range(3)]
        + [{"id": str(uuid.uuid4()), "org_id": None} for _ in range(2)]
    )
    connectors = [{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(2)] + [
        {"id": str(uuid.uuid4()), "org_id": _ORG_B} for _ in range(1)
    ]
    session = _FakeSession(sync_run_rows=sync_runs, connector_rows=connectors)
    client = _build_client(monkeypatch, session=session)

    resp = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # 5 sync_runs + 2 connectors for tenant-a = 7 total
    assert body["rows_deleted"] == 7, f"expected 7 total rows deleted, got {body['rows_deleted']}"
    assert body["per_table"] == {"sync_runs": 5, "connectors": 2}, f"per_table breakdown wrong: {body['per_table']}"
    assert body["status"] == "ok"

    # tenant-a wiped from BOTH tables
    remaining_sync = session.remaining_sync_runs()
    remaining_conn = session.remaining_connectors()
    assert all(r["org_id"] != _ORG_A for r in remaining_sync)
    assert all(r["org_id"] != _ORG_A for r in remaining_conn)
    # tenant-b + NULL-org rows preserved on both tables
    assert len([r for r in remaining_sync if r["org_id"] == _ORG_B]) == 3
    assert len([r for r in remaining_sync if r["org_id"] is None]) == 2
    assert len([r for r in remaining_conn if r["org_id"] == _ORG_B]) == 1


def test_wipe_state_purges_connectors_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression-guard for the SPEC G6 connector.connectors expansion.

    Pre-fix the endpoint only deleted from sync_runs; connector config rows
    (with at-rest encrypted credentials in portal_secret_id) survived
    deprovisioning. This test asserts both tables are touched: a tenant
    with ZERO sync_runs but >0 connectors is still purged correctly.
    """
    sync_runs: list[dict[str, Any]] = []
    connectors = [{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(3)]
    session = _FakeSession(sync_run_rows=sync_runs, connector_rows=connectors)
    client = _build_client(monkeypatch, session=session)

    resp = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows_deleted"] == 3
    assert body["per_table"] == {"sync_runs": 0, "connectors": 3}
    assert len(session.remaining_connectors()) == 0


def test_wipe_state_idempotent_second_call_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(
        sync_run_rows=[{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(3)],
        connector_rows=[{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(2)],
    )
    client = _build_client(monkeypatch, session=session)

    resp1 = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")
    assert resp1.status_code == 200
    assert resp1.json()["rows_deleted"] == 5  # 3 sync_runs + 2 connectors

    resp2 = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")
    assert resp2.status_code == 200
    assert resp2.json()["rows_deleted"] == 0
    assert resp2.json()["per_table"] == {"sync_runs": 0, "connectors": 0}
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
    """NULL-org rows on BOTH tables are intentionally preserved."""
    session = _FakeSession(
        sync_run_rows=[{"id": str(uuid.uuid4()), "org_id": None} for _ in range(4)],
        connector_rows=[{"id": str(uuid.uuid4()), "org_id": None} for _ in range(2)],
    )
    client = _build_client(monkeypatch, session=session)

    resp = client.post(f"/internal/v1/orgs/{_ORG_A}/wipe-state")

    assert resp.status_code == 200
    assert resp.json()["rows_deleted"] == 0
    assert len(session.remaining_sync_runs()) == 4
    assert len(session.remaining_connectors()) == 2


@pytest.mark.asyncio
async def test_fake_session_handles_named_bindparam_shape() -> None:
    """Audit MED 8 regression-guard: structural inspection must work for
    explicitly-named `bindparam("oid")` shapes too, not just the auto-bound
    `Model.col == value` form. The endpoint code uses the auto-bound form
    today, but a future caller may use an explicit bindparam (e.g. when
    re-using a compiled statement). The inspector should still find the
    org_id column on the LHS.
    """
    from sqlalchemy import bindparam, delete

    from app.models.sync_run import SyncRun

    session = _FakeSession(sync_run_rows=[{"id": str(uuid.uuid4()), "org_id": _ORG_A} for _ in range(2)])
    stmt = delete(SyncRun).where(SyncRun.org_id == bindparam("oid", value=_ORG_A))
    result = await session.execute(stmt)
    assert result.rowcount == 2
    assert len(session.remaining_sync_runs()) == 0
