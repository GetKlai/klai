"""Wire-level payload tests for SPEC-PORTAL-AUTH-EMAIL-LINKS-001.

For each of the three Zitadel v2 email-link calls, assert that the JSON body
posted to Zitadel contains the expected ``urlTemplate`` placeholders and
mandatory metadata (notificationType for password reset, applicationName for
invite). These tests are the regression net against silent fall-back to
Zitadel's default hosted-UI URL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _client_with_mocked_http():
    """Build a ZitadelClient with `self._http` stubbed to return 200 on POST."""
    from app.services.zitadel import ZitadelClient

    client = ZitadelClient.__new__(ZitadelClient)
    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value={"userId": "uid-1"})
    response.is_error = False
    response.aread = AsyncMock()
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=response)
    client._http = mock_http
    return client, mock_http


_KLAI_URL_TEMPLATE = "https://my.getklai.com/password/set?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"


# ---------------------------------------------------------------------------
# REQ-1 — send_password_reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_password_reset_uses_sendlink_with_url_template():
    client, mock_http = _client_with_mocked_http()

    await client.send_password_reset("uid-1", url_template=_KLAI_URL_TEMPLATE)

    args, kwargs = mock_http.post.call_args
    assert args[0] == "/v2/users/uid-1/password_reset"
    assert kwargs["json"] == {
        "sendLink": {
            "notificationType": "NOTIFICATION_TYPE_Email",
            "urlTemplate": _KLAI_URL_TEMPLATE,
        },
    }


@pytest.mark.asyncio
async def test_send_password_reset_requires_url_template_kwarg():
    """REQ-1: url_template is keyword-only and required — no positional, no default."""
    client, _ = _client_with_mocked_http()
    with pytest.raises(TypeError):
        # Missing url_template should raise — defends against caller drift.
        await client.send_password_reset("uid-1")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# REQ-2 — invite_user split: sendCodes=False + send_invite_code with template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_user_does_not_send_default_zitadel_mail():
    """REQ-2: invite_user creates the user WITHOUT triggering Zitadel's mail."""
    client, mock_http = _client_with_mocked_http()

    await client.invite_user(
        org_id="org-1",
        email="alice@example.com",
        first_name="Alice",
        last_name="Doe",
    )

    args, kwargs = mock_http.post.call_args
    assert args[0] == "/v2/users/human", (
        "invite_user MUST use the v2 AddHumanUser endpoint; the v1 _import "
        "path leaves users stuck in USER_STATE_INITIAL on Zitadel 6.x"
    )
    assert kwargs["json"]["email"]["verification"] == {"returnCode": {}}, (
        "invite_user MUST pass email.verification.returnCode so Zitadel "
        "returns the code instead of mailing it; the activation mail is "
        "issued separately via send_invite_code with a Klai urlTemplate"
    )


@pytest.mark.asyncio
async def test_send_invite_code_payload_shape():
    """REQ-2: send_invite_code passes urlTemplate + applicationName='Klai'."""
    client, mock_http = _client_with_mocked_http()

    await client.send_invite_code("uid-1", url_template=_KLAI_URL_TEMPLATE)

    args, kwargs = mock_http.post.call_args
    assert args[0] == "/v2/users/uid-1/invite_code"
    assert kwargs["json"] == {
        "sendCode": {
            "urlTemplate": _KLAI_URL_TEMPLATE,
            "applicationName": "Klai",
        },
    }


@pytest.mark.asyncio
async def test_send_invite_code_application_name_overridable():
    """For multi-tenant futures where the app-name should not be 'Klai'."""
    client, mock_http = _client_with_mocked_http()

    await client.send_invite_code(
        "uid-1",
        url_template=_KLAI_URL_TEMPLATE,
        application_name="CustomerPortal",
    )

    _args, kwargs = mock_http.post.call_args
    assert kwargs["json"]["sendCode"]["applicationName"] == "CustomerPortal"


@pytest.mark.asyncio
async def test_send_invite_code_requires_url_template_kwarg():
    """REQ-10: url_template is keyword-only and required; no Zitadel-cache fallback."""
    client, _ = _client_with_mocked_http()
    with pytest.raises(TypeError):
        await client.send_invite_code("uid-1")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# REQ-3 — resend_init_mail is gone; replaced by send_invite_code
# ---------------------------------------------------------------------------


def test_resend_init_mail_method_removed():
    """REQ-3: the legacy resend_init_mail method is deleted in the same commit."""
    from app.services.zitadel import ZitadelClient

    assert not hasattr(ZitadelClient, "resend_init_mail"), (
        "resend_init_mail must be removed in SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-3. "
        "Callers MUST use send_invite_code(url_template=...) instead."
    )
