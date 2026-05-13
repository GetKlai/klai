"""SPEC-MAILER-DROP-INITCODE-001 — drop legacy InitCode events at /notify.

Zitadel auto-fires ``user.human.initialization.code.added`` when a user is
created in ``USER_STATE_INITIAL``, regardless of ``sendCodes`` on the
import call. Klai migrated admin invites to the v2 invite_code flow with
its own urlTemplate (SPEC-PORTAL-AUTH-EMAIL-LINKS-001), so the InitCode
mail became a duplicate pointing at Zitadel's hosted UI. This test fixes
the contract: InitCode events return 204 with no SMTP send.
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from fastapi.testclient import TestClient

from tests._signing import sign


@pytest.fixture
async def client(settings_env, fake_redis, stub_smtp, monkeypatch):
    """Test client with in-process fakeredis + stubbed portal language lookup."""
    for mod in ("app.main", "app.nonce", "app.signature", "app.portal_client"):
        sys.modules.pop(mod, None)
    import app.nonce as nonce_mod

    nonce_mod.set_redis_client(fake_redis)

    async def _fake_lang(_email: str) -> str | None:
        return "nl"

    main = importlib.import_module("app.main")
    import app.nonce as nonce_after

    nonce_after.set_redis_client(fake_redis)
    # main.py uses `from app.portal_client import get_user_language` → the name
    # `get_user_language` is bound at module-level on app.main. Patch THAT,
    # not the source module — source patches don't affect already-bound names.
    monkeypatch.setattr(main, "get_user_language", _fake_lang)
    return TestClient(main.app)


def _signed_post(client: TestClient, body: dict, secret: str):
    raw = json.dumps(body).encode()
    header, _ = sign(raw, secret)
    return client.post(
        "/notify",
        content=raw,
        headers={"ZITADEL-Signature": header, "Content-Type": "application/json"},
    )


def test_initcode_event_returns_204_and_does_not_send(client, settings_env, stub_smtp):
    """REQ: InitCode event is accepted with 204 and produces zero SMTP calls."""
    body = {
        "contextInfo": {
            "eventType": "user.human.initialization.code.added",
            "recipientEmailAddress": "alice@example.com",
        },
        "templateData": {
            "subject": "Activeer je Klai-account",
            "text": "We hebben een Klai-account voor je aangemaakt.",
            "url": "https://auth.getklai.com/ui/login/user/init?code=ABC&userID=42",
            "buttonText": "Account activeren",
        },
    }
    resp = _signed_post(client, body, settings_env["WEBHOOK_SECRET"])
    assert resp.status_code == 204, f"expected 204, got {resp.status_code}: {resp.text}"
    assert resp.content == b"", "204 must have empty body"
    assert stub_smtp.sent == [], (
        f"InitCode event MUST NOT trigger an SMTP send. Got: {stub_smtp.sent}"
    )


def test_invite_event_still_sends(client, settings_env, stub_smtp):
    """Regression guard: drop is targeted — InviteUser still mails."""
    body = {
        "contextInfo": {
            "eventType": "user.human.invite.code.added",
            "recipientEmailAddress": "bob@example.com",
        },
        "templateData": {
            "subject": "Je bent uitgenodigd voor Klai",
            "greeting": "Hallo Bob,",
            "text": "Eén van je collega's nodigt je uit.",
            "url": "https://my.getklai.com/password/set?userID=42&code=ABC&orgID=8",
            "buttonText": "Account instellen",
        },
    }
    resp = _signed_post(client, body, settings_env["WEBHOOK_SECRET"])
    assert resp.status_code == 200
    assert len(stub_smtp.sent) == 1
    sent = stub_smtp.sent[0]
    assert sent["to_address"] == "bob@example.com"
    assert "Je bent uitgenodigd voor Klai" in sent["subject"]


def test_password_changed_event_still_sends(client, settings_env, stub_smtp):
    """Regression guard: drop is targeted — PasswordChange still mails."""
    body = {
        "contextInfo": {
            "eventType": "user.human.password.changed",
            "recipientEmailAddress": "carol@example.com",
        },
        "templateData": {
            "subject": "Je wachtwoord is gewijzigd",
            "greeting": "Hallo Carol,",
            "text": "Het wachtwoord van je Klai-account is zojuist gewijzigd.",
        },
    }
    resp = _signed_post(client, body, settings_env["WEBHOOK_SECRET"])
    assert resp.status_code == 200
    assert len(stub_smtp.sent) == 1
    assert stub_smtp.sent[0]["to_address"] == "carol@example.com"
