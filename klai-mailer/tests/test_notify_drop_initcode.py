"""SPEC-INFRA-TENANT-DELETE-003 Bug 4 — InitCode events MUST be delivered.

Reverts SPEC-MAILER-DROP-INITCODE-001's blanket drop. The drop was based
on a wrong assumption that admin-invite fired both `initialization.code.added`
AND `invite.code.added` (producing duplicate mails). In production Zitadel
behaviour, admin-invite explicitly sends ``sendCodes: false`` so init-code
is NOT fired — there was never a duplicate to suppress.

The drop's only effect was killing the verify-mail for every REGULAR
signup (which uses ``sendCodes: true`` by default), leaving new tenants
stuck in USER_STATE_INITIAL with no way to activate.

These tests now enforce the correct contract: InitCode events render and
mail like any other event-type. The legacy tests for InviteUser /
PasswordChange remain unchanged — they were never affected by the drop.
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


def test_initcode_event_renders_and_sends(client, settings_env, stub_smtp):
    """SPEC-INFRA-TENANT-DELETE-003 Bug 4 — InitCode events fire on every
    regular signup (where Zitadel's default ``sendCodes: true`` triggers
    the activation mail). The mailer must render + send these, not drop
    them. Without this the entire regular-signup flow is silently broken:
    user lands in USER_STATE_INITIAL, mailer drops the event, no mail.

    Verified against production incident 2026-05-13 17:30 UTC.
    """
    body = {
        "contextInfo": {
            "eventType": "user.human.initialization.code.added",
            "recipientEmailAddress": "alice@example.com",
        },
        "templateData": {
            "subject": "Activeer je Klai-account",
            "greeting": "Hallo Alice,",
            "text": "We hebben een Klai-account voor je aangemaakt.",
            "url": "https://auth.getklai.com/ui/login/user/init?code=ABC&userID=42",
            "buttonText": "Account activeren",
        },
    }
    resp = _signed_post(client, body, settings_env["WEBHOOK_SECRET"])
    assert resp.status_code == 200, f"expected 200 (rendered), got {resp.status_code}: {resp.text}"
    assert len(stub_smtp.sent) == 1, (
        "InitCode event MUST trigger exactly one SMTP send — regression of "
        "SPEC-MAILER-DROP-INITCODE-001 would re-introduce the silent-mail bug."
    )
    sent = stub_smtp.sent[0]
    assert sent["to_address"] == "alice@example.com"
    assert "Activeer je Klai-account" in sent["subject"]


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
