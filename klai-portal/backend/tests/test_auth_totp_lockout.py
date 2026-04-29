"""SPEC-SEC-SESSION-001 REQ-1 Redis-backed TOTP regression suite.

Covers acceptance scenarios 1 (cross-replica lockout) and 7 (Redis-down
fail-closed), plus the REQ-1.8 invariant that the in-memory ``_pending_totp``
global is gone.

Pre-SPEC, ``_pending_totp = TTLCache(...)`` lived in process memory: each
portal-api replica had its own ``failures`` counter, so 4 wrongs on replica A
plus 4 wrongs on replica B locked the token at attempt 8 (or 12, or 16,
linearly with replica count). The Redis-backed atomic ``INCR`` here makes
the 5-failure ceiling cross-replica consistent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs


def _zitadel_400_error() -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "wrong code",
        request=httpx.Request("POST", "https://example.invalid/v2/sessions"),
        response=httpx.Response(400, json={"message": "invalid"}),
    )


# ---------------------------------------------------------------------------
# REQ-1.4 + REQ-1.5 + REQ-6.1 — cross-replica lockout
# ---------------------------------------------------------------------------


async def test_cross_replica_lockout_at_attempt_5(fake_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """5th wrong TOTP returns 429 regardless of how attempts are distributed
    across replicas. The Redis-backed atomic counter is the proof.
    """
    from app.api.auth import (
        _TOTP_PENDING_FAILURES_PREFIX,
        _TOTP_PENDING_KEY_PREFIX,
        TOTPLoginRequest,
        _totp_pending_create,
        totp_login,
    )

    temp_token = await _totp_pending_create(
        session_id="sess-abc",
        session_token="tok-xyz",
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
    )

    monkeypatch.setattr(
        "app.api.auth.zitadel.update_session_with_totp",
        AsyncMock(side_effect=_zitadel_400_error()),
    )
    monkeypatch.setattr("app.api.auth.audit.log_event", AsyncMock())

    body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-x")
    db = AsyncMock(spec=AsyncSession)

    statuses: list[int] = []
    with capture_logs() as captured:
        for _ in range(5):
            try:
                await totp_login(body=body, response=Response(), db=db)
            except HTTPException as exc:
                statuses.append(exc.status_code)

    assert statuses == [400, 400, 400, 400, 429], f"expected 4 invalid-code rejections then 429 lockout, got {statuses}"

    # REQ-1.5: both Redis keys deleted after lockout
    assert await fake_redis.exists(f"{_TOTP_PENDING_KEY_PREFIX}{temp_token}") == 0
    assert await fake_redis.exists(f"{_TOTP_PENDING_FAILURES_PREFIX}{temp_token}") == 0

    # REQ-5.1 + REQ-6.5 PII guard: lockout event emitted once with token_prefix only
    lockout = [e for e in captured if e.get("event") == "totp_pending_lockout"]
    assert len(lockout) == 1, f"expected 1 lockout event, got {lockout!r}"
    assert lockout[0]["log_level"] == "warning"
    assert lockout[0]["failures"] == 5
    assert lockout[0]["token_prefix"] == temp_token[:8]
    # Token prefix only — no full token, no Zitadel session credentials.
    assert "session_id" not in lockout[0]
    assert "session_token" not in lockout[0]
    assert "temp_token" not in lockout[0]


async def test_sixth_attempt_after_lockout_returns_session_expired(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-1.3 + acceptance scenario 1: a 6th attempt after the lockout
    returns HTTP 400 ``Session expired`` because the keys are gone.
    """
    from app.api.auth import TOTPLoginRequest, _totp_pending_create, totp_login

    temp_token = await _totp_pending_create(
        session_id="sess-abc",
        session_token="tok-xyz",
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
    )
    monkeypatch.setattr(
        "app.api.auth.zitadel.update_session_with_totp",
        AsyncMock(side_effect=_zitadel_400_error()),
    )
    monkeypatch.setattr("app.api.auth.audit.log_event", AsyncMock())

    body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-x")
    db = AsyncMock(spec=AsyncSession)

    # Drive 5 failures → lockout
    for _ in range(5):
        try:
            await totp_login(body=body, response=Response(), db=db)
        except HTTPException:
            pass

    # 6th call: keys gone → 400 "Session expired"
    with pytest.raises(HTTPException) as exc_info:
        await totp_login(body=body, response=Response(), db=db)
    assert exc_info.value.status_code == 400
    assert "Session expired" in exc_info.value.detail


# ---------------------------------------------------------------------------
# REQ-1.7 + REQ-5.3 — fail-closed on Redis unavailability
# ---------------------------------------------------------------------------


async def test_redis_unavailable_fails_closed_when_pool_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-1.7: Redis pool unavailable → HTTP 503, NOT a degraded fall-through.

    Failing open here lifts the brute-force ceiling entirely (the in-memory
    counter is the bug we're fixing). Different threat model from
    ``partner_rate_limit.check_rate_limit`` which fails open by design.
    """
    from app.api.auth import TOTPLoginRequest, totp_login
    from app.services import redis_client

    redis_client._pool_holder["pool"] = None
    monkeypatch.setattr(
        "app.services.redis_client.get_redis_pool",
        AsyncMock(return_value=None),
    )

    body = TOTPLoginRequest(temp_token="any-token", code="000000", auth_request_id="ar-x")
    db = AsyncMock(spec=AsyncSession)

    with capture_logs() as captured:
        with pytest.raises(HTTPException) as exc_info:
            await totp_login(body=body, response=Response(), db=db)

    assert exc_info.value.status_code == 503
    assert "Authentication unavailable" in exc_info.value.detail

    unavail = [e for e in captured if e.get("event") == "totp_pending_redis_unavailable"]
    assert len(unavail) >= 1
    assert any(e.get("log_level") == "error" for e in unavail)


async def test_redis_connection_error_during_get_fails_closed(fake_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-1.7 second case: Redis is configured but raises mid-call → 503."""
    import redis.exceptions as redis_exc

    from app.api.auth import TOTPLoginRequest, totp_login

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise redis_exc.ConnectionError("network down")

    monkeypatch.setattr(fake_redis, "hgetall", _boom)

    body = TOTPLoginRequest(temp_token="any-token", code="000000", auth_request_id="ar-x")
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as exc_info:
        await totp_login(body=body, response=Response(), db=db)

    assert exc_info.value.status_code == 503


async def test_redis_connection_error_during_create_fails_closed(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-1.7 + REQ-5.3: a ``ConnectionError`` raised by the pipeline
    transaction during ``_totp_pending_create`` is mapped to HTTP 503 +
    ``totp_pending_redis_unavailable`` event with ``phase=create``.

    The pipeline replaces the legacy sequential ``HSET`` + ``EXPIRE``
    pair (closes the orphan-hash window on portal-api crash mid-create);
    the fail-closed contract still applies when the pipeline itself
    cannot reach Redis.
    """
    import redis.exceptions as redis_exc

    from app.api.auth import _totp_pending_create

    class _BoomPipeline:
        def __init__(self) -> None:
            self.queued: list[str] = []

        def hset(self, *_a: Any, **_kw: Any) -> _BoomPipeline:
            self.queued.append("hset")
            return self

        def expire(self, *_a: Any, **_kw: Any) -> _BoomPipeline:
            self.queued.append("expire")
            return self

        def set(self, *_a: Any, **_kw: Any) -> _BoomPipeline:
            self.queued.append("set")
            return self

        async def execute(self) -> Any:
            raise redis_exc.ConnectionError("network down mid-pipeline")

        async def __aenter__(self) -> _BoomPipeline:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

    monkeypatch.setattr(fake_redis, "pipeline", lambda *_a, **_kw: _BoomPipeline())

    with capture_logs() as captured:
        with pytest.raises(HTTPException) as exc_info:
            await _totp_pending_create(
                session_id="sess-x",
                session_token="tok-x",
                ua_hash="",
                ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
            )

    assert exc_info.value.status_code == 503
    unavail = [e for e in captured if e.get("event") == "totp_pending_redis_unavailable"]
    assert any(e.get("phase") == "create" for e in unavail)


async def test_redis_connection_error_during_incr_fails_closed(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-1.7: ``INCR`` failing mid-flight on a wrong TOTP code → 503.

    This is the worst-case leg from a UX perspective: the user already
    submitted a (wrong) code, so the audit log fires for ``invalid_code``
    and THEN Redis fails on the counter increment. Fail-CLOSED still
    applies — opening the door would let an attacker dodge the brute-force
    counter by saturating Redis network capacity.
    """
    import redis.exceptions as redis_exc

    from app.api.auth import TOTPLoginRequest, _totp_pending_create, totp_login

    temp_token = await _totp_pending_create(
        session_id="sess-incr",
        session_token="tok-incr",
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
    )

    monkeypatch.setattr(
        "app.api.auth.zitadel.update_session_with_totp",
        AsyncMock(side_effect=_zitadel_400_error()),
    )
    monkeypatch.setattr("app.api.auth.audit.log_event", AsyncMock())

    async def _boom_incr(*_a: Any, **_kw: Any) -> Any:
        raise redis_exc.ConnectionError("network down mid-incr")

    monkeypatch.setattr(fake_redis, "incr", _boom_incr)

    body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-incr")
    db = AsyncMock(spec=AsyncSession)

    with capture_logs() as captured:
        with pytest.raises(HTTPException) as exc_info:
            await totp_login(body=body, response=Response(), db=db)

    assert exc_info.value.status_code == 503
    unavail = [e for e in captured if e.get("event") == "totp_pending_redis_unavailable"]
    assert any(e.get("phase") == "incr" for e in unavail)


async def test_redis_connection_error_during_delete_fails_closed(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-1.7: ``DEL`` failing during cleanup after a successful TOTP
    verification → 503 (the SSO cookie is NOT minted).

    Unlike a successful flow, this scenario leaves a stranded
    ``totp_pending`` hash in Redis. That hash will eventually expire via
    its TTL; the user retries from the password screen. The trade-off
    is documented under SPEC §Fail modes — opening the door (returning
    success despite the failed cleanup) would re-introduce a window
    where the same token could be reused, which is the very property
    REQ-1.6 demands we close.
    """
    import redis.exceptions as redis_exc

    from app.api.auth import TOTPLoginRequest, _totp_pending_create, totp_login

    temp_token = await _totp_pending_create(
        session_id="sess-del",
        session_token="tok-del",
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
    )

    monkeypatch.setattr(
        "app.api.auth.zitadel.update_session_with_totp",
        AsyncMock(return_value={"sessionId": "sess-del", "sessionToken": "tok-del-renewed"}),
    )
    monkeypatch.setattr("app.api.auth.audit.log_event", AsyncMock())

    async def _boom_delete(*_a: Any, **_kw: Any) -> Any:
        raise redis_exc.ConnectionError("network down mid-delete")

    monkeypatch.setattr(fake_redis, "delete", _boom_delete)

    body = TOTPLoginRequest(temp_token=temp_token, code="123456", auth_request_id="ar-del")
    db = AsyncMock(spec=AsyncSession)

    with capture_logs() as captured:
        with pytest.raises(HTTPException) as exc_info:
            await totp_login(body=body, response=Response(), db=db)

    assert exc_info.value.status_code == 503
    unavail = [e for e in captured if e.get("event") == "totp_pending_redis_unavailable"]
    assert any(e.get("phase") == "delete" for e in unavail)


# ---------------------------------------------------------------------------
# REQ-1.8 — in-memory global removed
# ---------------------------------------------------------------------------


def test_in_memory_pending_totp_global_removed() -> None:
    """The pre-SPEC ``_pending_totp`` module global SHALL be gone.

    The ``TTLCache`` class itself MAY remain as a generic utility (REQ-1.8),
    but the production TOTP path must not route through it.
    """
    from app.api import auth

    assert not hasattr(auth, "_pending_totp"), (
        "Pre-SPEC `_pending_totp` global must be removed (REQ-1.8). "
        "Use the Redis-backed `_totp_pending_*` helpers instead."
    )
