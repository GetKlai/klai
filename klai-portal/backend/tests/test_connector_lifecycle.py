"""Tests for the connector lifecycle DELETE flow + internal finalize endpoint.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-09. These tests cover the user-facing
side of the orchestrator-saga: the DELETE endpoint flips state and enqueues,
and the internal finalize endpoint hard-deletes the row only when state is
'deleting'.

Lower-level orchestrator tests (cancel-jobs, store ordering, idempotency)
live in ``klai-knowledge-ingest/tests/test_connector_cleanup.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.api.connectors import delete_connector
from app.api.internal_connectors import finalize_connector_delete


class _FakeConnector:
    """Minimal stand-in for ``PortalConnector`` in unit tests."""

    def __init__(self, *, id: str, kb_id: int, state: str = "active") -> None:
        self.id = id
        self.kb_id = kb_id
        self.state = state


class _FakeKB:
    def __init__(self, id: int, slug: str) -> None:
        self.id = id
        self.slug = slug


class _FakeOrg:
    def __init__(self, id: int = 8, zitadel_org_id: str = "368884765035593759") -> None:
        self.id = id
        self.zitadel_org_id = zitadel_org_id


class _FakeResult:
    """Stand-in for the SQLAlchemy ``Result`` returned by ``await db.execute(...)``.

    The DELETE endpoint calls ``result.scalar_one_or_none()``; the internal
    finalize endpoint calls ``result.scalar_one_or_none()`` too. We only need
    that single method.
    """

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeDB:
    """Stand-in for ``AsyncSession`` capturing commit + execute calls."""

    def __init__(self, fetch_value: Any = None) -> None:
        self._fetch_value = fetch_value
        self.commits: int = 0
        self.executes: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executes.append(stmt)
        return _FakeResult(self._fetch_value)

    async def commit(self) -> None:
        self.commits += 1


# -- DELETE endpoint -----------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_connector_flips_state_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-03.1: state flips to 'deleting', enqueue called, 204 returned."""
    connector = _FakeConnector(id="conn-uuid", kb_id=42, state="active")
    db = _FakeDB(fetch_value=connector)

    org = _FakeOrg()
    kb = _FakeKB(id=42, slug="support")
    monkeypatch.setattr(
        "app.api.connectors._get_caller_org",
        AsyncMock(return_value=("user-id", org, None)),
    )
    monkeypatch.setattr(
        "app.api.connectors._get_kb_with_owner_check",
        AsyncMock(return_value=kb),
    )
    enqueue_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.api.connectors.knowledge_ingest_client.enqueue_connector_purge",
        enqueue_mock,
    )

    result = await delete_connector(
        kb_slug="support",
        connector_id="conn-uuid",
        credentials=AsyncMock(),  # bearer
        db=db,  # type: ignore[arg-type]
    )

    # Returns None for 204 No Content
    assert result is None
    # State flipped to 'deleting'
    assert connector.state == "deleting"
    # State commit happened
    assert db.commits == 1
    # Enqueue called with the right tenant + connector args
    enqueue_mock.assert_awaited_once()
    call = enqueue_mock.await_args
    assert call.kwargs == {
        "org_id": "368884765035593759",
        "kb_slug": "support",
        "connector_id": "conn-uuid",
    }


@pytest.mark.asyncio
async def test_delete_connector_rolls_back_on_enqueue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-03.1.3: enqueue failure reverts state and surfaces 502."""
    from fastapi import HTTPException

    connector = _FakeConnector(id="conn-uuid", kb_id=42, state="active")
    db = _FakeDB(fetch_value=connector)

    org = _FakeOrg()
    kb = _FakeKB(id=42, slug="support")
    monkeypatch.setattr(
        "app.api.connectors._get_caller_org",
        AsyncMock(return_value=("user-id", org, None)),
    )
    monkeypatch.setattr(
        "app.api.connectors._get_kb_with_owner_check",
        AsyncMock(return_value=kb),
    )
    monkeypatch.setattr(
        "app.api.connectors.knowledge_ingest_client.enqueue_connector_purge",
        AsyncMock(side_effect=RuntimeError("knowledge-ingest unavailable")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await delete_connector(
            kb_slug="support",
            connector_id="conn-uuid",
            credentials=AsyncMock(),
            db=db,  # type: ignore[arg-type]
        )

    # 502 surfaced to caller
    assert exc_info.value.status_code == 502
    # State reverted: flipped to 'deleting' then back to 'active'
    assert connector.state == "active"
    # Two commits: the flip + the rollback
    assert db.commits == 2


@pytest.mark.asyncio
async def test_delete_connector_404_when_already_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-02 + REQ-03.4: a connector already in 'deleting' is invisible."""
    from fastapi import HTTPException

    db = _FakeDB(fetch_value=None)  # filter (state='active') yields no row

    org = _FakeOrg()
    kb = _FakeKB(id=42, slug="support")
    monkeypatch.setattr(
        "app.api.connectors._get_caller_org",
        AsyncMock(return_value=("user-id", org, None)),
    )
    monkeypatch.setattr(
        "app.api.connectors._get_kb_with_owner_check",
        AsyncMock(return_value=kb),
    )

    with pytest.raises(HTTPException) as exc_info:
        await delete_connector(
            kb_slug="support",
            connector_id="conn-uuid",
            credentials=AsyncMock(),
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


# -- Internal finalize-delete --------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_delete_idempotent_when_row_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-04.4: row already gone => 204 (worker retry after partial success)."""
    monkeypatch.setattr(
        "app.api.internal_connectors.settings.internal_secret",
        "test-secret",
    )
    db = _FakeDB(fetch_value=None)
    result = await finalize_connector_delete(
        connector_id="conn-uuid",
        authorization="Bearer test-secret",
        db=db,  # type: ignore[arg-type]
    )
    assert result is None
    # No DELETE issued, no commit — only the SELECT.
    assert len(db.executes) == 1
    assert db.commits == 0


@pytest.mark.asyncio
async def test_finalize_delete_drops_row_when_state_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.api.internal_connectors.settings.internal_secret",
        "test-secret",
    )
    connector = _FakeConnector(id="conn-uuid", kb_id=42, state="deleting")
    db = _FakeDB(fetch_value=connector)

    result = await finalize_connector_delete(
        connector_id="conn-uuid",
        authorization="Bearer test-secret",
        db=db,  # type: ignore[arg-type]
    )

    assert result is None  # 204 No Content
    # Two executes: SELECT then DELETE
    assert len(db.executes) == 2
    assert db.commits == 1


@pytest.mark.asyncio
async def test_finalize_delete_409_when_state_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-04.4 invariant: never silently hard-delete an 'active' row."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        "app.api.internal_connectors.settings.internal_secret",
        "test-secret",
    )
    connector = _FakeConnector(id="conn-uuid", kb_id=42, state="active")
    db = _FakeDB(fetch_value=connector)

    with pytest.raises(HTTPException) as exc_info:
        await finalize_connector_delete(
            connector_id="conn-uuid",
            authorization="Bearer test-secret",
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_finalize_delete_401_on_bad_bearer() -> None:
    from fastapi import HTTPException

    db = _FakeDB(fetch_value=None)
    with pytest.raises(HTTPException) as exc_info:
        await finalize_connector_delete(
            connector_id="conn-uuid",
            authorization="Bearer wrong-secret",
            db=db,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_finalize_delete_401_on_missing_bearer() -> None:
    from fastapi import HTTPException

    db = _FakeDB(fetch_value=None)
    with pytest.raises(HTTPException) as exc_info:
        await finalize_connector_delete(
            connector_id="conn-uuid",
            authorization=None,
            db=db,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 401
