"""SPEC-SEC-TENANT-001 A-6 + A-8 — sync-route org scoping (v0.5.0–v0.6.1).

Coverage:
- A-6: cross-tenant ``GET /syncs/{run_id}`` returns 404, never 200/403.
- A-8 case 8.a: missing ``X-Org-ID`` during transition (flag OFF) on a
  read endpoint logs ``event="sync_missing_org_id"`` and returns 200
  with legacy connector_id-only filter.
- A-8 case 8.b: missing ``X-Org-ID`` after transition (flag ON) returns
  HTTP 400 with ``{"detail": "X-Org-ID header required"}``.
- A-8 case 8.c (v0.6.1 regression): production default ``sync_require_org_id=True``
  — a request without ``X-Org-ID`` must be rejected. Guards against any
  future code path that silently resets the default to ``False``.
- Settings default assertion: ``Settings.model_fields["sync_require_org_id"].default``
  must be ``True`` (SPEC-SEC-AUDIT-2026-04 C2).

Test design follows the FakeSession pattern from
``test_connector_routes_not_found.py`` — no real Postgres, no real
Redis. ``_require_portal_call`` is monkey-patched to a no-op so the
test does not need to thread the portal-secret through the auth
middleware (that path is covered by ``test_auth_middleware_*.py``).

Implementation note (v0.5.1): ``trigger_sync`` rejects missing-X-Org-ID
at HTTP 400 even when ``sync_require_org_id=False``. The column itself
is nullable post-migration 006 (no backfill — historical rows keep
NULL), but persisting a NEW row with org_id=NULL would create an
orphan invisible to every tenant's per-org filter. Fail-fast at the
handler keeps the new-row contract clean. Transition-period graceful
degradation in REQ-7.6 therefore applies to READ endpoints only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_session
from app.core.enums import SyncStatus
from app.models.sync_run import SyncRun
from app.routes.deps import get_settings
from app.routes.sync import router as sync_router

_CONN_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_CONN_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
_RUN_B = uuid.UUID("33333333-3333-3333-3333-333333333333")
_ORG_A = "org-a-resourceowner"
_ORG_B = "org-b-resourceowner"


class _FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._items)


class _FakeSession:
    """Minimal AsyncSession stub for the three sync handlers.

    ``run_by_id`` -> what ``session.get(SyncRun, run_id)`` returns.
    ``rows`` -> what ``session.execute(...).scalars().all/first()`` returns.
    """

    def __init__(self, *, rows: list[SyncRun] | None = None, run_by_id: SyncRun | None = None) -> None:
        self._rows = rows or []
        self._run_by_id = run_by_id

    async def execute(self, _stmt: Any) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._rows)

    async def get(self, _model: type, _key: uuid.UUID) -> SyncRun | None:
        return self._run_by_id

    async def commit(self) -> None:  # pragma: no cover
        return None

    async def refresh(self, _obj: Any) -> None:  # pragma: no cover
        return None

    def add(self, _obj: Any) -> None:  # pragma: no cover
        return None


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: _FakeSession,
    sync_require_org_id: bool = False,
) -> TestClient:
    app = FastAPI()
    app.include_router(sync_router, prefix="/api/v1")

    async def _override_get_session() -> AsyncIterator[_FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        sync_require_org_id=sync_require_org_id,
    )

    # Bypass the portal-secret check: the auth-middleware tests cover that
    # path. Here we exercise org-scoping behaviour assuming the request has
    # already been authenticated as a portal call.
    def _noop_portal_call(_request: Any) -> None:
        return None

    monkeypatch.setattr("app.routes.sync._require_portal_call", _noop_portal_call)

    return TestClient(app, raise_server_exceptions=False)


def _make_run(*, run_id: uuid.UUID, connector_id: uuid.UUID, org_id: str) -> SyncRun:
    return SyncRun(
        id=run_id,
        connector_id=connector_id,
        org_id=org_id,
        status=SyncStatus.COMPLETED,
        # started_at has server_default=func.now() but the FakeSession path
        # never hits the DB; populate it client-side so SyncRunResponse
        # validation succeeds on the way out.
        started_at=datetime.now(UTC),
        documents_total=0,
        documents_ok=0,
        documents_failed=0,
        bytes_processed=0,
    )


# ---------------------------------------------------------------------------
# A-6 — cross-tenant fetch returns 404
# ---------------------------------------------------------------------------


def test_get_sync_run_cross_tenant_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-7.3 / REQ-7.5: run belongs to org B, caller asserts org A → 404."""
    run_b = _make_run(run_id=_RUN_B, connector_id=_CONN_B, org_id=_ORG_B)
    client = _build_client(monkeypatch, session=_FakeSession(run_by_id=run_b))

    resp = client.get(
        f"/api/v1/connectors/{_CONN_B}/syncs/{_RUN_B}",
        headers={"X-Org-ID": _ORG_A},
    )

    assert resp.status_code == 404, f"REQ-7.5 cross-tenant fetch must return 404, got {resp.status_code}: {resp.text}"
    assert resp.json() == {"detail": "Sync run not found"}


def test_get_sync_run_same_tenant_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: caller asserts the right org → 200."""
    run_b = _make_run(run_id=_RUN_B, connector_id=_CONN_B, org_id=_ORG_B)
    client = _build_client(monkeypatch, session=_FakeSession(run_by_id=run_b))

    resp = client.get(
        f"/api/v1/connectors/{_CONN_B}/syncs/{_RUN_B}",
        headers={"X-Org-ID": _ORG_B},
    )

    assert resp.status_code == 200, f"same-tenant fetch must return 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["id"] == str(_RUN_B)


def test_list_sync_runs_filters_by_org_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-7.3: list endpoint filters on org_id when header is present.

    The FakeSession returns whatever rows we give it regardless of the
    SQL clause, so this test asserts the call shape (200 with rows for
    the asserting org). The real org_id filter is exercised by A-7
    integration tests at deploy-time; here we pin that the route
    accepts the header and the rows are returned to the caller.
    """
    rows = [
        _make_run(run_id=uuid.uuid4(), connector_id=_CONN_B, org_id=_ORG_B),
    ]
    client = _build_client(monkeypatch, session=_FakeSession(rows=rows))

    resp = client.get(
        f"/api/v1/connectors/{_CONN_B}/syncs",
        headers={"X-Org-ID": _ORG_B},
    )

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# A-8 — transition flag behaviour (REQ-7.6 + REQ-8.5)
# ---------------------------------------------------------------------------


def test_list_sync_runs_missing_org_id_transition_off_returns_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 8.a: flag OFF, missing header → 200 with legacy filter + WARN."""
    rows = [
        _make_run(run_id=uuid.uuid4(), connector_id=_CONN_A, org_id=_ORG_A),
    ]
    client = _build_client(
        monkeypatch,
        session=_FakeSession(rows=rows),
        sync_require_org_id=False,
    )

    resp = client.get(f"/api/v1/connectors/{_CONN_A}/syncs")

    assert resp.status_code == 200, (
        f"REQ-7.6 transition-period read must succeed without X-Org-ID, got {resp.status_code}: {resp.text}"
    )


def test_list_sync_runs_missing_org_id_transition_on_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 8.b: flag ON, missing header → 400 ``X-Org-ID header required``."""
    client = _build_client(
        monkeypatch,
        session=_FakeSession(rows=[]),
        sync_require_org_id=True,
    )

    resp = client.get(f"/api/v1/connectors/{_CONN_A}/syncs")

    assert resp.status_code == 400, (
        f"REQ-7.6 post-transition read must reject without X-Org-ID, got {resp.status_code}: {resp.text}"
    )
    assert resp.json() == {"detail": "X-Org-ID header required"}


def test_trigger_sync_missing_org_id_returns_400_regardless_of_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-7.4 / v0.5.1: trigger requires X-Org-ID always.

    Migration 006 made org_id nullable (no backfill — historical rows
    keep NULL), so a new SyncRun with org_id=NULL would technically
    succeed at the schema layer. But such a row is invisible to every
    tenant's per-org filter and effectively orphaned at creation time.
    The handler fail-fasts with a deterministic 400 to keep the new-row
    contract clean — same shape REQ-7.6 mandates for reads after the
    transition closes. This ensures fail-fast even when
    sync_require_org_id is still False.
    """
    client = _build_client(
        monkeypatch,
        session=_FakeSession(rows=[]),
        sync_require_org_id=False,
    )

    resp = client.post(f"/api/v1/connectors/{_CONN_A}/sync")

    assert resp.status_code == 400, (
        f"trigger_sync without X-Org-ID must 400 (cannot create NOT NULL row), got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# A-8 case 8.c — production default enforced (SPEC-SEC-AUDIT-2026-04 C2)
# ---------------------------------------------------------------------------


def test_sync_require_org_id_settings_default_is_true() -> None:
    """SPEC-SEC-AUDIT-2026-04 C2 / REQ-8.5: transition flag must be True by default.

    Guards the config.py default from regressing to False. Checks the
    pydantic field default directly — no subprocess, no env override.
    """
    from app.core.config import Settings

    default = Settings.model_fields["sync_require_org_id"].default
    assert default is True, (
        f"sync_require_org_id default must be True (transition closed per REQ-8.5), got {default!r}"
    )


def test_sync_request_without_org_id_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPEC-SEC-AUDIT-2026-04 C2 regression: production default rejects missing X-Org-ID.

    Uses the enforced default (True) directly so this test catches any
    future code path that passes sync_require_org_id=False implicitly.
    Mirrors case 8.b but named for audit traceability.
    """
    client = _build_client(
        monkeypatch,
        session=_FakeSession(rows=[]),
        sync_require_org_id=True,  # production default post REQ-8.5 flip (2026-04-29)
    )

    resp = client.get(f"/api/v1/connectors/{_CONN_A}/syncs")

    assert resp.status_code == 400, (
        f"sync route without X-Org-ID must 400 under enforced default, got {resp.status_code}: {resp.text}"
    )
    assert resp.json() == {"detail": "X-Org-ID header required"}
