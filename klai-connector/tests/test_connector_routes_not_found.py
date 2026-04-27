"""SPEC-SEC-HYGIENE-001 HY-30 — `HTTPException` NameError regression tests.

Pre-fix state: `app/routes/connectors.py` imports
``APIRouter, Depends, Request`` from FastAPI but NOT ``HTTPException``.
Lines 75/90/121 then ``raise HTTPException(...)`` in the not-found
branches of ``get_connector``, ``update_connector``, ``delete_connector``.
At runtime the lookup fails with ``NameError`` and FastAPI converts the
uncaught exception to a generic 500. Symptoms:

- Every "not found" returns 500 instead of 404.
- The 500-vs-200 difference is a UUID-existence oracle (an attacker can
  enumerate connector UUIDs per-tenant by poking URLs).

Post-fix: the import is added; not-found paths return 404 with
``{"detail": "Connector not found"}``.

Covers REQ-30.1 + REQ-30.2 + AC-30 (cross-tenant case).

Note on test design: the connector currently has no DB-backed route
tests. Rather than spin up Postgres just to drive a NameError, we
override the SQLAlchemy session dependency with a stub whose ``.get()``
returns whatever the test injects (``None`` for missing, or a row with a
mismatched ``org_id`` for cross-tenant). ``get_org_id`` is called inline
(not via ``Depends``), so we monkey-patch the symbol in
``app.routes.connectors``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_session
from app.models.connector import Connector
from app.routes.connectors import router as connectors_router

_FAKE_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_SELF_ORG = "org-self"
_OTHER_ORG = "org-other"


class _FakeSession:
    """Minimal AsyncSession stub.

    Only ``.get()`` is exercised on the not-found path. The other coroutines
    exist to keep the route's reference graph happy if a future change starts
    calling them before the not-found check.
    """

    def __init__(self, lookup_result: Any = None) -> None:
        self._lookup_result = lookup_result

    async def get(self, _model: type, _key: uuid.UUID) -> Any:
        return self._lookup_result

    async def commit(self) -> None:  # pragma: no cover — not reached on the not-found path
        return None

    async def refresh(self, _obj: Any) -> None:  # pragma: no cover
        return None

    async def delete(self, _obj: Any) -> None:  # pragma: no cover
        return None

    def add(self, _obj: Any) -> None:  # pragma: no cover
        return None


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_result: Any,
    org_id: str = _SELF_ORG,
) -> TestClient:
    """Build a FastAPI test client with the connectors router mounted.

    raise_server_exceptions=False so the pre-fix NameError surfaces as a
    real 500 response we can assert on, instead of being re-raised inside
    the test runner and aborting it.
    """
    app = FastAPI()
    app.include_router(connectors_router, prefix="/api/v1")

    async def _override_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession(session_result)

    app.dependency_overrides[get_session] = _override_get_session
    monkeypatch.setattr(
        "app.routes.connectors.get_org_id", lambda _request: org_id
    )

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# REQ-30.1 / REQ-30.2 — not-found paths return 404
# ---------------------------------------------------------------------------


def test_get_missing_connector_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(monkeypatch, session_result=None)

    response = client.get(f"/api/v1/connectors/{_FAKE_UUID}")

    assert response.status_code == 404, (
        f"expected 404, got {response.status_code}: {response.text}"
    )
    assert response.json() == {"detail": "Connector not found"}


def test_put_missing_connector_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(monkeypatch, session_result=None)

    response = client.put(
        f"/api/v1/connectors/{_FAKE_UUID}",
        json={"name": "renamed"},
    )

    assert response.status_code == 404, (
        f"expected 404, got {response.status_code}: {response.text}"
    )
    assert response.json() == {"detail": "Connector not found"}


def test_delete_missing_connector_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(monkeypatch, session_result=None)

    response = client.delete(f"/api/v1/connectors/{_FAKE_UUID}")

    assert response.status_code == 404, (
        f"expected 404, got {response.status_code}: {response.text}"
    )
    assert response.json() == {"detail": "Connector not found"}


# ---------------------------------------------------------------------------
# AC-30 — cross-tenant access returns 404 (not 403/500)
# ---------------------------------------------------------------------------


def _make_connector(*, owner_org: str) -> Connector:
    """Build a Connector instance owned by a different org.

    The route compares ``connector.org_id != org_id`` after a successful
    lookup. Returning this from the fake session exercises the same
    not-found branch that triggered the original bug.
    """
    return Connector(
        id=_FAKE_UUID,
        org_id=owner_org,
        name="theirs",
        connector_type="github",
        config={},
    )


def test_cross_tenant_get_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(
        monkeypatch,
        session_result=_make_connector(owner_org=_OTHER_ORG),
        org_id=_SELF_ORG,
    )

    response = client.get(f"/api/v1/connectors/{_FAKE_UUID}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Connector not found"}


def test_cross_tenant_put_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(
        monkeypatch,
        session_result=_make_connector(owner_org=_OTHER_ORG),
        org_id=_SELF_ORG,
    )

    response = client.put(
        f"/api/v1/connectors/{_FAKE_UUID}",
        json={"name": "renamed"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Connector not found"}


def test_cross_tenant_delete_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(
        monkeypatch,
        session_result=_make_connector(owner_org=_OTHER_ORG),
        org_id=_SELF_ORG,
    )

    response = client.delete(f"/api/v1/connectors/{_FAKE_UUID}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Connector not found"}
