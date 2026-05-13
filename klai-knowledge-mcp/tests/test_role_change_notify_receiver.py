"""SPEC-PORTAL-RBAC-REFACTOR-001 REQ-18 — receiver-side fan-out endpoint.

Pinned behaviour:
- Auth: ``X-Internal-Secret`` matched via ``hmac.compare_digest`` against
  the ``PORTAL_INTERNAL_SECRET`` captured at module import. Wrong / empty
  → 401 with no detail leak.
- Body validation: malformed JSON → 400, missing/empty/non-string
  ``user_id`` → 422.
- Fan-out: snapshot the WeakSet via ``list(...)`` BEFORE iterating so a
  concurrent ``_register_session_for_user`` call cannot mutate the set
  during ``await session.send_tool_list_changed()``.
- Error isolation: a single session that raises during emit MUST NOT
  abort the fan-out; remaining sessions still get notified and the
  response counts only the successes.
- No active sessions: 200 with ``{"notified": 0}`` (idempotent — caller
  treats fan-out as fire-and-forget; absence ≠ failure).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _request(headers: dict | None = None, body=None, raise_on_json: bool = False) -> MagicMock:
    """Minimal Starlette ``Request`` stand-in for the route handler."""
    req = MagicMock()
    req.headers = headers or {}
    if raise_on_json:
        req.json = AsyncMock(side_effect=ValueError("bad json"))
    else:
        req.json = AsyncMock(return_value=body)
    return req


class _FakeSession:
    """Stand-in for ``ServerSession`` exposing ``send_tool_list_changed``."""

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls = 0

    async def send_tool_list_changed(self) -> None:
        self.calls += 1
        if self.raises:
            raise RuntimeError("simulated SSE write failure")


class TestNotifyRoleChangeAuth:
    @pytest.mark.asyncio
    async def test_missing_header_returns_401(self):
        from main import _notify_role_change

        resp = await _notify_role_change(_request(headers={}))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_401(self):
        from main import _notify_role_change

        resp = await _notify_role_change(_request(headers={"x-internal-secret": "wrong"}))
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_secret_passes_to_body_parse(self):
        """Valid secret + valid body → 200 (verifies the auth gate yields)."""
        from main import _active_user_sessions, _notify_role_change

        _active_user_sessions.clear()
        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": "zit-no-sessions"},
            )
        )
        assert resp.status_code == 200


class TestNotifyRoleChangeBody:
    @pytest.mark.asyncio
    async def test_malformed_json_returns_400(self):
        from main import _notify_role_change

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                raise_on_json=True,
            )
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_user_id_returns_422(self):
        from main import _notify_role_change

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={},
            )
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_user_id_returns_422(self):
        from main import _notify_role_change

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": ""},
            )
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_non_string_user_id_returns_422(self):
        from main import _notify_role_change

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": 12345},
            )
        )
        assert resp.status_code == 422


class TestNotifyRoleChangeFanout:
    @pytest.mark.asyncio
    async def test_no_active_sessions_returns_zero(self):
        from main import _active_user_sessions, _notify_role_change

        _active_user_sessions.clear()
        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": "zit-ghost"},
            )
        )
        assert resp.status_code == 200
        # JSONResponse stores body as bytes; access the raw rendered payload.
        import json

        payload = json.loads(bytes(resp.body))
        assert payload == {"notified": 0}

    @pytest.mark.asyncio
    async def test_all_sessions_notified_when_healthy(self):
        from main import _active_user_sessions, _notify_role_change, _register_session_for_user

        _active_user_sessions.clear()
        sessions = [_FakeSession(), _FakeSession(), _FakeSession()]
        for s in sessions:
            _register_session_for_user("zit-three", s)

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": "zit-three"},
            )
        )
        assert resp.status_code == 200
        import json

        assert json.loads(bytes(resp.body)) == {"notified": 3}
        for s in sessions:
            assert s.calls == 1

    @pytest.mark.asyncio
    async def test_one_failing_session_does_not_abort_fanout(self):
        """REQ-18 isolation: a single broken SSE write MUST NOT prevent
        the remaining sessions from receiving the notification."""
        from main import _active_user_sessions, _notify_role_change, _register_session_for_user

        _active_user_sessions.clear()
        ok1 = _FakeSession()
        broken = _FakeSession(raises=True)
        ok2 = _FakeSession()
        # Register in an order that puts the broken one in the middle to
        # prove fan-out continues past the failure point.
        for s in (ok1, broken, ok2):
            _register_session_for_user("zit-mixed", s)

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": "zit-mixed"},
            )
        )
        assert resp.status_code == 200
        import json

        # 2 healthy + 1 broken → notified=2 (broken is not counted but
        # also did not raise out of the handler).
        assert json.loads(bytes(resp.body)) == {"notified": 2}
        assert ok1.calls == 1
        assert ok2.calls == 1
        assert broken.calls == 1  # attempted

    @pytest.mark.asyncio
    async def test_session_registry_is_snapshotted_before_iteration(self):
        """Snapshot-via-list MUST detach the iteration target from the
        live WeakSet so a concurrent register during ``await
        send_tool_list_changed()`` does not mutate iteration order or
        raise ``RuntimeError: Set changed size during iteration``."""
        from main import _active_user_sessions, _notify_role_change, _register_session_for_user

        _active_user_sessions.clear()
        # WeakSet only retains via weakref — keep a strong ref locally so the
        # late-arriving session is not GC'd before len() observes it.
        late_strong_ref: list = []

        class _MutatingSession:
            calls = 0

            async def send_tool_list_changed(self) -> None:
                self.calls += 1
                # Sneak in a new session DURING the await — this would
                # detonate iteration if the handler iterated the live
                # WeakSet directly.
                late = _FakeSession()
                late_strong_ref.append(late)
                _register_session_for_user("zit-mutate", late)

        s = _MutatingSession()
        _register_session_for_user("zit-mutate", s)

        resp = await _notify_role_change(
            _request(
                headers={"x-internal-secret": "portal-test-secret"},
                body={"user_id": "zit-mutate"},
            )
        )
        assert resp.status_code == 200
        import json

        # Snapshot was 1 element; the late-arriving session MUST NOT be
        # counted (it joined after the snapshot was taken).
        assert json.loads(bytes(resp.body)) == {"notified": 1}
        # The mutation did succeed (registry now has 2 sessions: the
        # original mutator + the one it added during its own emit).
        assert len(_active_user_sessions["zit-mutate"]) == 2


class TestHashUserId:
    def test_hash_is_deterministic_and_truncated(self):
        from main import _hash_user_id

        h1 = _hash_user_id("zit-user-1")
        h2 = _hash_user_id("zit-user-1")
        assert h1 == h2
        assert len(h1) == 16
        assert all(c in "0123456789abcdef" for c in h1)

    def test_hash_differs_per_input(self):
        from main import _hash_user_id

        assert _hash_user_id("a") != _hash_user_id("b")
