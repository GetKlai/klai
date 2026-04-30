"""Tests for ``ZitadelClient.create_session_with_password`` and the login
handler's call into it.

Regression coverage for the 2026-04-30 case-sensitive-loginName bug.

Bug context: ``find_user_by_email`` (used at the start of the login flow)
matches loginName case-insensitively via ``TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE``,
so it correctly resolves a user typing ``steven@getklai.com`` to the
Zitadel record whose stored loginName is ``Steven@getklai.com``. But
``create_session_with_password`` then passed the user-typed email back
into Zitadel as ``{"user": {"loginName": email}}``, and Zitadel's
``/v2/sessions`` endpoint matches that field CASE-SENSITIVELY against the
stored loginName. Result: HTTP 400 from Zitadel → portal 401 → login
fails for any user whose stored loginName has different case from what
they type. Steven Buurma hit this on 2026-04-30 morning.

The fix passes the canonical Zitadel ``user_id`` (already resolved by
``find_user_by_email``) to ``create_session_with_password`` and the
function sends ``{"user": {"userId": ...}}`` instead of loginName.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _zitadel_client_with_mocked_http(session_payload: dict | None = None):
    """Construct a ZitadelClient with a mocked _http returning a session dict."""
    from app.services.zitadel import ZitadelClient

    client = ZitadelClient.__new__(ZitadelClient)
    mock_resp = MagicMock()
    mock_resp.json.return_value = session_payload or {
        "sessionId": "sess-1",
        "sessionToken": "tok-1",
    }
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    client._http = mock_http
    return client, mock_http


class TestCreateSessionPayloadShape:
    """The Zitadel /v2/sessions payload MUST use ``userId``, NOT ``loginName``,
    for the user-check field. Asserting the wire shape guards against a
    refactor that re-introduces the case-sensitive-loginName regression.
    """

    @pytest.mark.asyncio
    async def test_user_check_uses_user_id_not_loginname(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.create_session_with_password(user_id="u-364818", password="hunter2")

        mock_http.post.assert_awaited_once()
        _args, kwargs = mock_http.post.call_args
        body = kwargs["json"]
        user_check = body["checks"]["user"]
        assert "userId" in user_check, f"user check uses {user_check!r}; must use 'userId'"
        assert "loginName" not in user_check, (
            "loginName MUST NOT appear in the user check — it caused the 2026-04-30 case-sensitivity regression"
        )
        assert user_check["userId"] == "u-364818"

    @pytest.mark.asyncio
    async def test_password_passes_through_unchanged(self) -> None:
        client, mock_http = _zitadel_client_with_mocked_http()

        await client.create_session_with_password(user_id="u-1", password="P@ss/W:rd+1")

        _args, kwargs = mock_http.post.call_args
        assert kwargs["json"]["checks"]["password"]["password"] == "P@ss/W:rd+1"

    @pytest.mark.asyncio
    async def test_returns_session_dict(self) -> None:
        client, _ = _zitadel_client_with_mocked_http(session_payload={"sessionId": "abc", "sessionToken": "xyz"})

        result = await client.create_session_with_password(user_id="u-1", password="x")

        assert result == {"sessionId": "abc", "sessionToken": "xyz"}


class TestLoginHandlerForwardsUserIdNotEmail:
    """Regression: the /api/auth/login handler MUST pass the resolved
    Zitadel user_id (not the user-typed email) to create_session_with_password.
    """

    @pytest.mark.asyncio
    async def test_login_passes_zitadel_user_id_when_user_found(self) -> None:
        """When find_user_by_email resolves a user, create_session must be
        called with that user_id — NOT the user-typed email."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(
            email="steven@getklai.com",  # user types lower-case
            password="hunter2",
            auth_request_id="ar-1",
        )
        response = MagicMock()
        request = MagicMock()
        db = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit"),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth._resolve_and_enforce_mfa", new=AsyncMock(return_value=None)),
            patch("app.api.auth._finalize_and_set_cookie", new=AsyncMock()),
        ):
            # find_user_by_email resolves to the canonical id, even though
            # the user typed lower-case.
            mock_zitadel.find_user_by_email = AsyncMock(return_value=("zitadel-uid-364818", "org-123"))
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(return_value={"sessionId": "s", "sessionToken": "t"})

            await login(body=body, response=response, request=request, db=db)

            mock_zitadel.create_session_with_password.assert_awaited_once()
            call_args = mock_zitadel.create_session_with_password.call_args
            # Accept positional or kwarg form: in either case, the first
            # arg MUST be the canonical user_id, NOT the user-typed email.
            passed = call_args.kwargs.get("user_id") or (call_args.args[0] if call_args.args else None)
            assert passed == "zitadel-uid-364818", (
                f"login handler passed {passed!r} to create_session_with_password; "
                "expected the canonical zitadel_user_id resolved by "
                "find_user_by_email. Passing the user-typed email back to "
                "Zitadel triggers the case-sensitive-loginName regression."
            )
            # Anti-regression: the user-typed email MUST NOT be the value
            # we forwarded.
            assert passed != "steven@getklai.com"

    @pytest.mark.asyncio
    async def test_login_uses_sentinel_when_user_not_found(self) -> None:
        """When find_user_by_email returns None (user does not exist),
        create_session is still called with a sentinel user_id so Zitadel
        returns 4xx and the handler emits the same uniform 401 — preserving
        the anti-enumeration property from SPEC-SEC-MFA-001 finding #12."""
        from app.api.auth import LoginRequest, login

        body = LoginRequest(
            email="nobody@example.com",
            password="any",
            auth_request_id="ar-1",
        )
        response = MagicMock()
        request = MagicMock()
        db = AsyncMock()

        # Construct a 401 httpx error — what Zitadel returns for an
        # unknown user_id under the new payload shape.
        zitadel_request = httpx.Request("POST", "https://zitadel.test/v2/sessions")
        zitadel_response = httpx.Response(404, request=zitadel_request, text="user not found")
        zitadel_error = httpx.HTTPStatusError("user not found", request=zitadel_request, response=zitadel_response)

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.audit") as mock_audit,
            patch("app.api.auth.emit_event"),
        ):
            mock_zitadel.find_user_by_email = AsyncMock(return_value=None)
            mock_zitadel.has_totp = AsyncMock(return_value=False)
            mock_zitadel.create_session_with_password = AsyncMock(side_effect=zitadel_error)
            mock_audit.log_event = AsyncMock()

            with pytest.raises(Exception) as exc_info:
                await login(body=body, response=response, request=request, db=db)

            # The handler raises 401 with the uniform message
            assert exc_info.value.status_code == 401
            assert exc_info.value.detail == "Email address or password is incorrect"

            # Critically: create_session_with_password WAS called even
            # though the user wasn't found — this is the anti-enumeration
            # invariant (timing close to the user-found path).
            mock_zitadel.create_session_with_password.assert_awaited_once()
            call_args = mock_zitadel.create_session_with_password.call_args
            passed = call_args.kwargs.get("user_id") or call_args.args[0]
            # Pin against the named constant rather than the literal string
            # so a single rename (constant → semantic value) stays in sync.
            from app.api.auth import _NONEXISTENT_USER_ID_SENTINEL

            assert passed == _NONEXISTENT_USER_ID_SENTINEL
