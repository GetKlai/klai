"""Refresh-rotation and revoke unit tests for SPEC-MCP-AUTH-001.

Pure-unit: no DB, no Redis. We mock AsyncSession (matching the pattern in
``test_audit.py``) and use fakeredis for the cache invalidation calls.

Coverage targets (per evaluator-active review 2026-05-07):

- ``refresh_access_token`` rotation + replay-detection + grace window
  (REQ-25, REQ-26)
- ``_revoke_chain`` mass-revoke trip-wire on real replay
- ``revoke_token`` idempotent semantics (calling twice returns True both times)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.services import mcp_oauth as svc
from app.services.mcp_oauth import (
    REFRESH_TOKEN_PREFIX,
    RefreshOutcome,
    refresh_access_token,
    revoke_token,
)

# ─── Helpers ─────────────────────────────────────────────────────────────


def _make_token_row(
    *,
    token_id: int = 42,
    org_id: int = 8,
    user_id: int = 16,
    client_id: int = 7,
    revoked_at: datetime | None = None,
    replaced_by_token_id: int | None = None,
    refresh_expires_at: datetime | None = None,
    resource_uri: str = "https://mcp.getklai.com",
    refresh_hash: bytes = b"\x00" * 32,
    access_hash: bytes = b"\x11" * 32,
) -> MagicMock:
    """Build a MagicMock PortalMcpToken row with the columns the service reads."""
    row = MagicMock()
    row.id = token_id
    row.org_id = org_id
    row.user_id = user_id
    row.client_id = client_id
    row.revoked_at = revoked_at
    row.replaced_by_token_id = replaced_by_token_id
    row.refresh_expires_at = refresh_expires_at or (datetime.now(UTC) + timedelta(days=30))
    row.resource_uri = resource_uri
    row.refresh_token_hash = refresh_hash
    row.access_token_hash = access_hash
    row.scopes = ["mcp:knowledge"]
    return row


def _mock_session(*, scalar_results: list[Any] | None = None) -> AsyncMock:
    """Mock AsyncSession whose execute() returns a result with the given rows.

    ``scalar_results`` is consumed in order: each call to db.execute() pops
    the next entry. That entry can be:
    - a single value → returned by .scalar_one_or_none()
    - a list → returned by .scalars().all()
    - None → both .scalar_one_or_none() and .scalars().all() return None / []
    """
    queue = list(scalar_results or [])

    async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
        result = MagicMock()
        if not queue:
            result.scalar_one_or_none = MagicMock(return_value=None)
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            return result
        next_value = queue.pop(0)
        if isinstance(next_value, list):
            result.scalar_one_or_none = MagicMock(return_value=next_value[0] if next_value else None)
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=next_value)))
        else:
            result.scalar_one_or_none = MagicMock(return_value=next_value)
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[next_value] if next_value else [])))
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.refresh = AsyncMock()
    return session


@pytest_asyncio.fixture
async def fake_redis() -> Any:
    """Per-test fakeredis instance — no cross-test contamination."""
    return await fakeredis.aioredis.FakeRedis()


# ─── refresh_access_token: format + lookup ───────────────────────────────


@pytest.mark.asyncio
async def test_refresh_rejects_token_without_refresh_prefix(fake_redis: Any) -> None:
    """REQ-25: a bearer that lacks klai_mcp_rt_ prefix is invalid_grant."""
    db = _mock_session()
    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token="klai_mcp_AAAAAAAAA",  # access prefix, not refresh
        expected_resource="https://mcp.getklai.com",
    )
    assert outcome == RefreshOutcome(failure_reason="invalid_grant")
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_rejects_unknown_hash(fake_redis: Any) -> None:
    """No row matching the refresh-hash → invalid_grant, no chain-revoke."""
    db = _mock_session(scalar_results=[None])
    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token=f"{REFRESH_TOKEN_PREFIX}unknown",
        expected_resource="https://mcp.getklai.com",
    )
    assert outcome == RefreshOutcome(failure_reason="invalid_grant")


@pytest.mark.asyncio
async def test_refresh_rejects_expired_resource_mismatch(fake_redis: Any) -> None:
    """REQ-14 audience-binding: a row whose resource_uri ≠ expected → reject."""
    row = _make_token_row(resource_uri="https://other.example.com")
    db = _mock_session(scalar_results=[row])
    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token=f"{REFRESH_TOKEN_PREFIX}xyz",
        expected_resource="https://mcp.getklai.com",
    )
    assert outcome.failure_reason == "invalid_grant"
    assert outcome.success is None
    assert outcome.revoked_chain is False


# ─── refresh_access_token: replay detection + grace window ───────────────


@pytest.mark.asyncio
async def test_refresh_replay_within_grace_window_is_soft_fail(fake_redis: Any) -> None:
    """Stale retry within 30s of rotation → invalid_grant, NO chain-revoke.

    Anchors REQ-26 grace-window semantics: client retries with the just-
    rotated refresh-token shouldn't trigger a mass revoke.
    """
    just_revoked_at = datetime.now(UTC) - timedelta(seconds=5)
    row = _make_token_row(revoked_at=just_revoked_at, replaced_by_token_id=999)
    db = _mock_session(scalar_results=[row])

    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token=f"{REFRESH_TOKEN_PREFIX}xyz",
        expected_resource="https://mcp.getklai.com",
    )

    assert outcome.failure_reason == "invalid_grant"
    assert outcome.revoked_chain is False, "Inside grace window must NOT trigger chain-revoke"
    # Only the initial SELECT — no UPDATE issued by _revoke_chain.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_refresh_replay_outside_grace_window_triggers_chain_revoke(fake_redis: Any) -> None:
    """Replay > 30s after rotation → real replay → mass-revoke."""
    long_ago_revoked = datetime.now(UTC) - timedelta(seconds=120)
    replayed_row = _make_token_row(revoked_at=long_ago_revoked, replaced_by_token_id=999)
    # _revoke_chain's SELECT returns the active rows for (client, user).
    active_row_a = _make_token_row(token_id=100, access_hash=b"\xaa" * 32)
    active_row_b = _make_token_row(token_id=101, access_hash=b"\xbb" * 32)

    db = _mock_session(scalar_results=[replayed_row, [active_row_a, active_row_b], None])

    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token=f"{REFRESH_TOKEN_PREFIX}xyz",
        expected_resource="https://mcp.getklai.com",
    )

    assert outcome.failure_reason == "invalid_grant"
    assert outcome.revoked_chain is True
    # 1 SELECT (lookup) + 1 SELECT (chain) + 1 UPDATE (chain) = 3
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_refresh_replay_naive_revoked_at_is_handled(fake_redis: Any) -> None:
    """Older rows may carry naive datetimes; service must coerce safely."""
    naive_revoked = (datetime.now(UTC) - timedelta(seconds=120)).replace(tzinfo=None)
    replayed = _make_token_row(revoked_at=naive_revoked, replaced_by_token_id=42)
    db = _mock_session(scalar_results=[replayed, [], None])
    # Should not raise on tz-coercion path.
    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token=f"{REFRESH_TOKEN_PREFIX}xyz",
        expected_resource="https://mcp.getklai.com",
    )
    assert outcome.failure_reason == "invalid_grant"
    assert outcome.revoked_chain is True


# ─── refresh_access_token: happy-path rotation ───────────────────────────


@pytest.mark.asyncio
async def test_refresh_happy_path_rotates_and_marks_old_revoked(fake_redis: Any) -> None:
    """Successful rotation: old row gets revoked_at + replaced_by_token_id.

    The service uses ``await db.flush()`` + ``await db.refresh(row)`` which
    populates row.id with the post-flush primary key. We monkey-patch
    ``_new_access_token``/``_new_refresh_token`` to deterministic values
    and let session.refresh assign id=999 onto the new PortalMcpToken
    instance.
    """
    old_row = _make_token_row(token_id=42, org_id=8, user_id=16, client_id=7)
    db = _mock_session(scalar_results=[old_row])

    # session.add receives the new PortalMcpToken; session.refresh fills its id.
    added_rows: list[Any] = []

    def _add(row: Any) -> None:
        row.id = 999
        added_rows.append(row)

    db.add = MagicMock(side_effect=_add)

    outcome = await refresh_access_token(
        db,
        fake_redis,
        raw_refresh_token=f"{REFRESH_TOKEN_PREFIX}original",
        expected_resource="https://mcp.getklai.com",
    )

    assert outcome.failure_reason is None
    assert outcome.success is not None
    assert outcome.success.token_id == 999
    assert outcome.success.org_id == 8
    assert outcome.success.user_id == 16
    assert outcome.success.access_token.startswith("klai_mcp_")
    assert outcome.success.refresh_token.startswith(REFRESH_TOKEN_PREFIX)
    # Old row mutated in-place.
    assert old_row.revoked_at is not None
    assert old_row.replaced_by_token_id == 999
    # add() called once for the new token row.
    assert len(added_rows) == 1


# ─── revoke_token: idempotency + scoping ─────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_token_returns_false_on_unknown_id(fake_redis: Any) -> None:
    db = _mock_session(scalar_results=[None])
    ok = await revoke_token(db, fake_redis, token_id=42, org_id=8, user_id=16)
    assert ok is False
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_token_idempotent_on_already_revoked(fake_redis: Any) -> None:
    """REQ-DELETE-IDEMPOTENT: calling revoke twice is a success both times."""
    already_revoked = _make_token_row(revoked_at=datetime.now(UTC) - timedelta(hours=1))
    db = _mock_session(scalar_results=[already_revoked])

    ok = await revoke_token(db, fake_redis, token_id=42, org_id=8, user_id=16)

    assert ok is True
    # Already-revoked path skips flush + cache invalidation.
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_token_marks_active_token_revoked(fake_redis: Any) -> None:
    """Active row → revoked_at gets set, cache invalidated."""
    active = _make_token_row(revoked_at=None, access_hash=b"\xcc" * 32)
    db = _mock_session(scalar_results=[active])

    ok = await revoke_token(db, fake_redis, token_id=42, org_id=8, user_id=16)

    assert ok is True
    assert active.revoked_at is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_token_scoped_by_user_and_org(fake_redis: Any) -> None:
    """The lookup must filter by token_id AND org_id AND user_id (REQ-22).

    Captured by inspecting the executed select() WHERE clause.
    """
    captured_calls: list[Any] = []

    async def _execute_capture(stmt: Any, *_args: Any, **_kwargs: Any) -> MagicMock:
        captured_calls.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute_capture)
    db.flush = AsyncMock()

    await revoke_token(db, fake_redis, token_id=42, org_id=8, user_id=16)

    assert len(captured_calls) == 1
    stmt_str = str(captured_calls[0]).lower()
    # All three scoping columns present in the WHERE clause.
    for column in ("portal_mcp_tokens.id", "portal_mcp_tokens.org_id", "portal_mcp_tokens.user_id"):
        assert column in stmt_str, f"Missing scope column {column} in select"


# ─── _revoke_chain: noop on empty active set ─────────────────────────────


@pytest.mark.asyncio
async def test_revoke_chain_noop_when_no_active_tokens(fake_redis: Any) -> None:
    """No active tokens for (client, user) → no UPDATE, no log warning."""
    db = _mock_session(scalar_results=[[]])
    await svc._revoke_chain(db, fake_redis, client_db_id=7, user_id=16)
    # Only the SELECT was issued, no UPDATE.
    assert db.execute.await_count == 1
