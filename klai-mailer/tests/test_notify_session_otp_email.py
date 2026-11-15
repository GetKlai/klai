"""The e-mail code a sign-in asks for reaches the user, without a link.

Zitadel sends ``session.otp.email.challenged`` when the portal login (or the
e-mail code enrolment) challenges a session. The payload below has the shape
Zitadel v4.20.1 posts to this service, captured by
klai-portal/backend/tests/contract/test_zitadel_login_contract.py with
synthetic values. The code is in ``templateData.text``; ``templateData.url``
points at Zitadel's hosted login (``/ui/v2/login/otp/email``), which the
deployed Zitadel image does not serve (404), and the portal reads the code
from its own login page. So the mail carries the code and no button.
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from fastapi.testclient import TestClient

from tests._signing import sign

_CODE = "29134674"
_CHALLENGE = {
    "contextInfo": {
        "eventType": "session.otp.email.challenged",
        "provider": {"id": "1", "description": "klai-mailer"},
        "recipientEmailAddress": "erin@example.com",
    },
    "templateData": {
        "title": "Verify OTP",
        "preHeader": "Verify OTP",
        "subject": "Verify OTP",
        "greeting": "Hello Erin B,",
        "text": (
            f"Please use the OTP {_CODE} to authenticate within the next five minutes "
            'or click the "Authenticate" button.'
        ),
        "url": f"http://localhost:8080/ui/v2/login/otp/email?code={_CODE}&sessionId=1&userId=2",
        "buttonText": "Authenticate",
        "footerText": "VerifyEmailOTP.Footer",
    },
    "args": {"oTP": _CODE, "expiry": 300000000000, "loginName": "erin@example.com"},
}


@pytest.fixture
async def client(settings_env, fake_redis, stub_smtp, monkeypatch):
    for mod in ("app.main", "app.nonce", "app.signature", "app.portal_client"):
        sys.modules.pop(mod, None)
    main = importlib.import_module("app.main")
    import app.nonce as nonce_mod

    nonce_mod.set_redis_client(fake_redis)

    async def _no_lang(_email: str) -> str | None:
        return None

    monkeypatch.setattr(main, "get_user_language", _no_lang)
    return TestClient(main.app)


def test_session_email_code_is_mailed_with_the_code_and_without_a_link(
    client, settings_env, stub_smtp
):
    raw = json.dumps(_CHALLENGE).encode()
    header, _ = sign(raw, settings_env["WEBHOOK_SECRET"])

    resp = client.post("/notify", content=raw, headers={"ZITADEL-Signature": header})

    assert resp.status_code == 200, resp.text
    assert len(stub_smtp.sent) == 1
    sent = stub_smtp.sent[0]
    assert sent["to_address"] == "erin@example.com"
    assert _CODE in sent["html_body"]
    assert "/ui/v2/login/" not in sent["html_body"]


def _send(client, settings_env, stub_smtp) -> dict:
    raw = json.dumps(_CHALLENGE).encode()
    header, _ = sign(raw, settings_env["WEBHOOK_SECRET"])
    resp = client.post("/notify", content=raw, headers={"ZITADEL-Signature": header})
    assert resp.status_code == 200, resp.text
    return stub_smtp.sent[-1]


def test_session_email_code_mail_is_written_by_the_mailer_not_by_the_provider(
    client, settings_env, stub_smtp
):
    """The provider's text points at a button this mail does not have and its
    footer is an untranslated key; neither reaches the reader."""
    sent = _send(client, settings_env, stub_smtp)

    assert sent["subject"] == "Your Klai sign-in code"
    assert "Enter it on the sign-in page" in sent["html_body"]
    assert "valid for 5 minutes" in sent["html_body"]
    assert f">{_CODE}</strong>" in sent["html_body"]
    assert "Authenticate" not in sent["html_body"]
    assert "VerifyEmailOTP" not in sent["html_body"]


def test_session_email_code_mail_follows_the_readers_language(
    client, settings_env, stub_smtp, monkeypatch
):
    main = sys.modules["app.main"]

    async def _dutch(_email: str) -> str | None:
        return "nl"

    monkeypatch.setattr(main, "get_user_language", _dutch)

    sent = _send(client, settings_env, stub_smtp)

    assert sent["subject"] == "Je inlogcode voor Klai"
    assert _CODE in sent["html_body"]
    assert "5 minuten geldig" in sent["html_body"]
