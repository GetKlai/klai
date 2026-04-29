"""
Tests for SPEC-SEC-001: NEN 7510 security hardening.

Fix 1: Audit logging on auth endpoints (login, login_failed, totp, totp_failed, logout).
Fix 2: MFA policy enforcement (org mfa_policy="required" blocks login without MFA).

Pure unit tests -- all async sessions, Zitadel calls, and audit service are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from helpers import make_request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError with the given status code."""
    request = httpx.Request("POST", "https://zitadel.test/session")
    response = httpx.Response(status_code, request=request, text="error")
    return httpx.HTTPStatusError("error", request=request, response=response)


def _make_session_response() -> dict:
    """Return a minimal Zitadel session dict."""
    return {"sessionId": "sess-123", "sessionToken": "tok-abc"}


# ---------------------------------------------------------------------------
# Test class: Auth audit logging (Fix 1)
# ---------------------------------------------------------------------------


class TestAuthAuditLogging:
    """Verify that auth endpoints write audit log entries via audit.log_event."""

    @pytest.mark.asyncio
    async def test_successful_login_writes_auth_login_audit(self) -> None:
        """After a successful password login, audit.log_event must be called
        with action='auth.login'."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="user@test.com", password="pass123", auth_request_id="ar-1")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
        ):
            # find_user_by_email returns (user_id, org_id), has_totp = False
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-1", "zorg-1"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(return_value=_make_session_response())
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://chat.getklai.com/callback")
            mock_zitadel.has_any_mfa = AsyncMock(return_value=True)

            # Mock the portal user lookup for MFA (Fix 2 runs before Fix 1 audit)
            mock_portal_user = MagicMock()
            mock_portal_user.org_id = 42
            mock_org = MagicMock()
            mock_org.mfa_policy = "optional"
            with patch("app.api.auth.select"):
                db.scalar = AsyncMock(return_value=mock_portal_user)
                db.get = AsyncMock(return_value=mock_org)

                mock_audit.log_event = AsyncMock()

                await login(body=body, response=response, request=make_request(), db=db)

            # Verify audit.log_event was called with action="auth.login"
            mock_audit.log_event.assert_called()
            call_kwargs = mock_audit.log_event.call_args
            assert call_kwargs[1]["action"] == "auth.login" or (
                len(call_kwargs[0]) >= 4 and call_kwargs[0][3] == "auth.login"
            )

    @pytest.mark.asyncio
    async def test_failed_login_writes_audit_login_failed(self) -> None:
        """When create_session_with_password fails (401), audit must log
        action='auth.login.failed'."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="bad@test.com", password="wrong", auth_request_id="ar-2")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-2", "zorg-2"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(side_effect=_make_http_error(401))
            mock_audit.log_event = AsyncMock()

            with pytest.raises(Exception) as exc_info:
                await login(body=body, response=response, request=make_request(), db=db)

            assert exc_info.value.status_code == 401  # type: ignore[union-attr]

            # Verify audit was called with login.failed
            mock_audit.log_event.assert_called()
            # Check action is auth.login.failed
            found = False
            for call in mock_audit.log_event.call_args_list:
                kwargs = call.kwargs if call.kwargs else {}
                positional = call.args if call.args else ()
                if kwargs.get("action") == "auth.login.failed" or (
                    len(positional) >= 4 and positional[3] == "auth.login.failed"
                ):
                    found = True
                    break
            assert found, "Expected audit.log_event called with action='auth.login.failed'"

    @pytest.mark.asyncio
    async def test_logout_writes_auth_logout_audit(self) -> None:
        """The BFF logout endpoint must write an audit entry with action='auth.logout',
        scoped to the session's real org_id so tenant-scoped audit queries see it.
        """
        from app.api.auth_bff import logout
        from app.core.session import SessionContext
        from app.services.bff_session import SessionRecord

        session = SessionContext(
            sid="sess-xyz",
            zitadel_user_id="user-42",
            access_token="atk",
            csrf_token="csrf-xyz",
            access_token_expires_at=0,
        )
        record = SessionRecord(
            sid="sess-xyz",
            zitadel_user_id="user-42",
            org_id=8,
            access_token="atk",
            refresh_token="",
            access_token_expires_at=0,
            id_token="",
            csrf_token="csrf-xyz",
            created_at=0,
            last_seen_at=0,
            user_agent_hash="",
            ip_hash="",
        )

        with (
            patch("app.api.auth_bff.audit") as mock_audit,
            patch("app.api.auth_bff.session_service") as mock_session_service,
        ):
            mock_audit.log_event = AsyncMock()
            mock_session_service.load = AsyncMock(return_value=record)
            mock_session_service.revoke = AsyncMock()

            await logout(session=session)

            mock_audit.log_event.assert_called_once()
            call = mock_audit.log_event.call_args
            assert call.kwargs.get("action") == "auth.logout"
            assert call.kwargs.get("actor") == "user-42"
            assert call.kwargs.get("resource_id") == "sess-xyz"
            assert call.kwargs.get("org_id") == 8, "audit must carry real org_id, not sentinel 0"

    @pytest.mark.asyncio
    async def test_totp_login_writes_auth_login_totp(self, fake_redis) -> None:
        """After a successful TOTP verification, audit must log action='auth.login.totp'."""
        from app.api.auth import TOTPLoginRequest, _totp_pending_create, totp_login

        # Pre-populate the Redis-backed TOTP pending state (SPEC-SEC-SESSION-001 REQ-1)
        temp_token = await _totp_pending_create(
            session_id="sess-totp",
            session_token="tok-totp",
            ua_hash="",
            ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
        )
        body = TOTPLoginRequest(temp_token=temp_token, code="123456", auth_request_id="ar-totp")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
        ):
            mock_zitadel.update_session_with_totp = AsyncMock(
                return_value={
                    "sessionId": "sess-totp",
                    "sessionToken": "tok-totp-new",
                }
            )
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://chat.getklai.com/callback")
            mock_audit.log_event = AsyncMock()

            await totp_login(body=body, response=response, db=db)

            mock_audit.log_event.assert_called()
            found = False
            for call in mock_audit.log_event.call_args_list:
                kwargs = call.kwargs if call.kwargs else {}
                positional = call.args if call.args else ()
                if kwargs.get("action") == "auth.login.totp" or (
                    len(positional) >= 4 and positional[3] == "auth.login.totp"
                ):
                    found = True
                    break
            assert found, "Expected audit.log_event called with action='auth.login.totp'"

    @pytest.mark.asyncio
    async def test_totp_failed_writes_audit_totp_failed(self, fake_redis) -> None:
        """When TOTP verification fails (400), audit must log action='auth.totp.failed'."""
        from app.api.auth import TOTPLoginRequest, _totp_pending_create, totp_login

        temp_token = await _totp_pending_create(
            session_id="sess-totp-2",
            session_token="tok-totp-2",
            ua_hash="",
            ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
        )
        body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-totp-2")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
        ):
            mock_zitadel.update_session_with_totp = AsyncMock(side_effect=_make_http_error(400))
            mock_audit.log_event = AsyncMock()

            with pytest.raises(HTTPException):
                await totp_login(body=body, response=response, db=db)

            mock_audit.log_event.assert_called()
            found = False
            for call in mock_audit.log_event.call_args_list:
                kwargs = call.kwargs if call.kwargs else {}
                positional = call.args if call.args else ()
                if kwargs.get("action") == "auth.totp.failed" or (
                    len(positional) >= 4 and positional[3] == "auth.totp.failed"
                ):
                    found = True
                    break
            assert found, "Expected audit.log_event called with action='auth.totp.failed'"

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_block_login(self) -> None:
        """If audit.log_event raises, login must still succeed (non-fatal)."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="user@test.com", password="pass123", auth_request_id="ar-3")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-1", "zorg-1"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(return_value=_make_session_response())
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://chat.getklai.com/callback")
            mock_zitadel.has_any_mfa = AsyncMock(return_value=True)

            # Mock portal user lookup for MFA
            mock_portal_user = MagicMock()
            mock_portal_user.org_id = 42
            mock_org = MagicMock()
            mock_org.mfa_policy = "optional"
            with patch("app.api.auth.select"):
                db.scalar = AsyncMock(return_value=mock_portal_user)
                db.get = AsyncMock(return_value=mock_org)

                # audit.log_event raises -- must NOT block login
                mock_audit.log_event = AsyncMock(side_effect=Exception("audit DB down"))

                # Should not raise -- login must succeed despite audit failure
                result = await login(body=body, response=response, request=make_request(), db=db)
                assert result is not None


# ---------------------------------------------------------------------------
# Test class: MFA policy enforcement (Fix 2)
# ---------------------------------------------------------------------------


class TestMFAPolicyEnforcement:
    """Verify that org-level MFA policy is enforced during login."""

    @pytest.mark.asyncio
    async def test_mfa_required_no_mfa_enrolled_returns_403(self) -> None:
        """When org.mfa_policy='required' and user has no MFA, login returns 403."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="user@test.com", password="pass123", auth_request_id="ar-mfa-1")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.select"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-mfa", "zorg-mfa"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(return_value=_make_session_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            # Portal user belongs to an org with mfa_policy=required
            mock_portal_user = MagicMock()
            mock_portal_user.org_id = 10
            mock_org = MagicMock()
            mock_org.mfa_policy = "required"
            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_org)

            mock_audit.log_event = AsyncMock()

            with pytest.raises(Exception) as exc_info:
                await login(body=body, response=response, request=make_request(), db=db)

            assert exc_info.value.status_code == 403  # type: ignore[union-attr]
            assert "MFA required" in str(exc_info.value.detail)  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_mfa_required_with_mfa_enrolled_proceeds(self, fake_redis) -> None:
        """When org.mfa_policy='required' and user has MFA, login proceeds normally."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="user@test.com", password="pass123", auth_request_id="ar-mfa-2")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.select"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-mfa", "zorg-mfa"))
            mock_zitadel.has_totp = AsyncMock(return_value=True)
            mock_zitadel.create_session_with_password = AsyncMock(return_value=_make_session_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=True)
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://chat.getklai.com/callback")

            mock_portal_user = MagicMock()
            mock_portal_user.org_id = 10
            mock_org = MagicMock()
            mock_org.mfa_policy = "required"
            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_org)

            mock_audit.log_event = AsyncMock()

            # Should NOT raise 403 -- user has MFA enrolled
            # (will return totp_required since has_totp=True)
            result = await login(body=body, response=response, request=make_request(), db=db)
            assert result.status == "totp_required"

    @pytest.mark.asyncio
    async def test_mfa_optional_no_enforcement(self) -> None:
        """When org.mfa_policy='optional', has_any_mfa is NOT called."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="user@test.com", password="pass123", auth_request_id="ar-mfa-3")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.select"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-opt", "zorg-opt"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(return_value=_make_session_response())
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://chat.getklai.com/callback")
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            mock_portal_user = MagicMock()
            mock_portal_user.org_id = 10
            mock_org = MagicMock()
            mock_org.mfa_policy = "optional"
            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_org)

            mock_audit.log_event = AsyncMock()

            result = await login(body=body, response=response, request=make_request(), db=db)
            # Login should succeed -- no 403
            assert result is not None
            # has_any_mfa should NOT have been called
            mock_zitadel.has_any_mfa.assert_not_called()

    @pytest.mark.asyncio
    async def test_mfa_policy_lookup_failure_defaults_to_optional(self) -> None:
        """If portal_user lookup itself raises, MFA enforcement defaults to optional (fail-open).

        SPEC-SEC-MFA-001 REQ-5.4 narrowed: this test covers ONLY the
        "portal_user lookup raised" arm — the call returns ``None`` semantics.
        The complementary "portal_user found but PortalOrg fetch raises" arm
        is fail-CLOSED and is covered by
        ``test_auth_mfa_fail_closed.py::test_portal_user_found_org_fetch_raises_returns_503``.
        """
        from app.api.auth import LoginRequest, login

        body = LoginRequest(email="user@test.com", password="pass123", auth_request_id="ar-mfa-4")
        response = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.select"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-err", "zorg-err"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(return_value=_make_session_response())
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://chat.getklai.com/callback")

            # DB throws on portal_user lookup — cannot map email to org → fail-open
            db.scalar = AsyncMock(side_effect=Exception("DB connection lost"))
            db.get = AsyncMock(return_value=None)

            mock_audit.log_event = AsyncMock()

            # Should NOT raise -- fail-open means login proceeds (provisioning grace)
            result = await login(body=body, response=response, request=make_request(), db=db)
            assert result is not None

    # SPEC-SEC-MFA-001 REQ-5.3: ``test_mfa_check_failure_defaults_to_pass`` is
    # intentionally REMOVED. The fail-open behaviour it documented (has_any_mfa
    # 5xx => user_has_mfa = True) is now an anti-pattern: under
    # ``mfa_policy="required"`` we fail-CLOSED with HTTP 503 instead.
    # Replacement coverage lives in
    # ``test_auth_mfa_fail_closed.py::test_required_has_any_mfa_500_returns_503``.
