"""Invited-but-not-activated users choosing social login must not dead-end.

Reproduction for the 2026-08-12 incident: a portal invite creates the Zitadel
account (username = email) up front, so when the invitee ignores the invite
mail and clicks "Log in / Sign up with Google", the IdP intent has no linked
user, ``create_zitadel_user_from_idp`` returns 409 "User already exists", and
both callbacks bounced to a generic failure page with no way forward.

Contract under test: on 409 the callbacks resolve the existing account by the
IdP-asserted email, link the IdP identity to it (POST /v2/users/{id}/links),
and continue the normal session flow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from helpers import make_request

INTENT_EMAIL = "anne.daalman@voys.nl"

# Completed Google intent for a person Zitadel has no IdP link for.
INTENT_NO_LINKED_USER = {
    "idpInformation": {
        "idpId": "idp-google",
        "userId": "google-sub-123",
        "userName": INTENT_EMAIL,
        "rawInformation": {
            "User": {
                "email": INTENT_EMAIL,
                "given_name": "Anne",
                "family_name": "Daalman",
                "name": "Anne Daalman",
            }
        },
    }
}

EXISTING_ZITADEL_USER_ID = "zuser-existing"


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://auth.example.com/v2/users/human")
    return httpx.HTTPStatusError(
        f"{status}",
        request=req,
        response=httpx.Response(status, request=req),
    )


def _zitadel_for_conflict() -> MagicMock:
    """Zitadel client double: create → 409, existing user found by email."""
    zit = MagicMock()
    zit.retrieve_idp_intent = AsyncMock(return_value=dict(INTENT_NO_LINKED_USER))
    zit.create_zitadel_user_from_idp = AsyncMock(side_effect=_http_status_error(409))
    zit.find_user_by_email = AsyncMock(return_value=(EXISTING_ZITADEL_USER_ID, "zorg-1"))
    zit.link_idp_to_user = AsyncMock(return_value=None)
    zit.create_session_for_user_idp = AsyncMock(return_value={"sessionId": "sid", "sessionToken": "stk"})
    zit.get_session = AsyncMock(
        return_value={
            "session": {
                "factors": {
                    "user": {
                        "id": EXISTING_ZITADEL_USER_ID,
                        "displayName": "Anne Daalman",
                        "loginName": INTENT_EMAIL,
                    },
                    "intent": {"idpInformation": INTENT_NO_LINKED_USER["idpInformation"]},
                }
            }
        }
    )
    # idp_callback (login path) fetches a flattened variant.
    zit.get_session_details = AsyncMock(return_value={"zitadel_user_id": "", "email": INTENT_EMAIL})
    return zit


class TestSignupCallbackLinksExistingUser:
    @pytest.mark.asyncio
    async def test_409_links_idp_and_logs_invited_member_in(self) -> None:
        """Anne's scenario: invited (portal row exists) → Google → logged in."""
        from app.api.auth import idp_signup_callback

        zit = _zitadel_for_conflict()
        existing_member = MagicMock()
        existing_member.org_id = 1
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=existing_member)

        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.audit.log_event", AsyncMock()),
            patch("app.api.auth._encrypt_sso", return_value="ENC"),
        ):
            response = await idp_signup_callback(
                id="intent-1",
                token="tok-1",
                request=make_request(),
                locale="nl",
                invite_token=None,
                db=mock_db,
            )

        zit.find_user_by_email.assert_awaited_once_with(INTENT_EMAIL)
        zit.link_idp_to_user.assert_awaited_once()
        assert zit.link_idp_to_user.await_args.args[0] == EXISTING_ZITADEL_USER_ID
        zit.create_session_for_user_idp.assert_awaited_once()
        assert zit.create_session_for_user_idp.await_args.args[0] == EXISTING_ZITADEL_USER_ID
        assert response.status_code == 302
        assert "error=idp_failed" not in response.headers["location"]
        assert "klai_sso" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_409_with_dangling_identity_continues_signup(self) -> None:
        """Zitadel identity exists but no portal row → normal social-form leg."""
        from app.api.auth import idp_signup_callback

        zit = _zitadel_for_conflict()
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=None)

        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.audit.log_event", AsyncMock()),
            patch("app.api.auth._get_sso_fernet") as mock_fernet,
        ):
            mock_fernet.return_value.encrypt = MagicMock(return_value=b"ENCRYPTED_PENDING")
            response = await idp_signup_callback(
                id="intent-1",
                token="tok-1",
                request=make_request(),
                locale="nl",
                invite_token=None,
                db=mock_db,
            )

        zit.link_idp_to_user.assert_awaited_once()
        assert response.status_code == 302
        assert "/signup/social" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_409_without_email_match_still_fails(self) -> None:
        """No account matches the intent email → original failure leg stays."""
        from app.api.auth import idp_signup_callback

        zit = _zitadel_for_conflict()
        zit.find_user_by_email = AsyncMock(return_value=None)

        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.audit.log_event", AsyncMock()),
        ):
            response = await idp_signup_callback(
                id="intent-1",
                token="tok-1",
                request=make_request(),
                locale="nl",
                invite_token=None,
                db=AsyncMock(),
            )

        zit.link_idp_to_user.assert_not_awaited()
        zit.create_session_for_user_idp.assert_not_awaited()
        assert response.status_code == 302
        assert "error=idp_failed" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_link_conflict_treated_as_already_linked(self) -> None:
        """409 on the link call (half-completed earlier attempt) → proceed."""
        from app.api.auth import idp_signup_callback

        zit = _zitadel_for_conflict()
        zit.link_idp_to_user = AsyncMock(side_effect=_http_status_error(409))
        existing_member = MagicMock()
        existing_member.org_id = 1
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=existing_member)

        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.audit.log_event", AsyncMock()),
            patch("app.api.auth._encrypt_sso", return_value="ENC"),
        ):
            response = await idp_signup_callback(
                id="intent-1",
                token="tok-1",
                request=make_request(),
                locale="nl",
                invite_token=None,
                db=mock_db,
            )

        zit.create_session_for_user_idp.assert_awaited_once()
        assert response.status_code == 302
        assert "error=idp_failed" not in response.headers["location"]


class TestLoginCallbackLinksExistingUser:
    @pytest.mark.asyncio
    async def test_409_links_idp_and_creates_session_for_existing_user(self) -> None:
        """Login-with-Google (SPEC-AUTH-010 R3 leg) resolves the 409 the same way."""
        from app.api.auth import idp_callback

        zit = _zitadel_for_conflict()

        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.audit.log_event", AsyncMock()),
            patch("app.api.auth._get_sso_fernet") as mock_fernet,
        ):
            mock_fernet.return_value.encrypt = MagicMock(return_value=b"ENCRYPTED_PENDING")
            response = await idp_callback(
                id="intent-1",
                token="tok-1",
                auth_request_id="ar-1",
                request=make_request(),
                db=AsyncMock(),
            )

        zit.find_user_by_email.assert_awaited_once_with(INTENT_EMAIL)
        zit.link_idp_to_user.assert_awaited_once()
        zit.create_session_for_user_idp.assert_awaited_once()
        assert zit.create_session_for_user_idp.await_args.args[0] == EXISTING_ZITADEL_USER_ID
        assert response.status_code == 302
        assert response.headers["location"] != "/login?authRequest=ar-1"
