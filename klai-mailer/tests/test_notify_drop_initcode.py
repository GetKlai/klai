"""InitCode events are DROPPED — verified against prod 2026-05-22.

Re-applies the drop of ``user.human.initialization.code.added`` after the
2026-05-13 revert (SPEC-INFRA-TENANT-DELETE-003 Bug 4). The revert assumed
(a) admin-invite never fires init-code (``sendCodes: false``) and (b) regular
signup relies on init-code. BOTH are outdated:

  - ``sendCodes: false`` on ``/management/v1/users/human/_import`` suppresses
    Zitadel's own SMTP but NOT the HTTP-notification event Klai consumes —
    so admin-invite DOES fire init-code (live-captured 2026-05-22), pointing
    at Zitadel's hosted UI (the WRONG link), on top of the correct
    ``invite.code.added`` mail. Init-code is the duplicate.
  - Regular signup uses ``create_human_user_v2_with_verify`` →
    ``email.code.added`` (NOT init-code). Real signup lars.houben@nerds.nl
    2026-05-20 fired email.code.added and reached ACTIVE+verified. So the
    2026-05-13 failure mode cannot recur.
  - ``create_human_user`` (the only other init-code source) has zero
    production callers.

These tests enforce: init-code is dropped (204, no SMTP); invite-code and
password-changed still mail (targeted drop, regression guards).
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


def test_initcode_event_is_dropped(client, settings_env, stub_smtp):
    """InitCode is the wrong-link duplicate of the admin-invite flow and is
    dropped: 204, no SMTP send. Admin-invite's correct mail is
    ``invite.code.added`` (Klai-branded /password/set link); init-code points
    at Zitadel's hosted UI. Signup uses ``email.code.added``, not init-code,
    so this drop does not touch signup. Verified against prod 2026-05-22.
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
    assert resp.status_code == 204, f"expected 204 (dropped), got {resp.status_code}: {resp.text}"
    assert len(stub_smtp.sent) == 0, (
        "InitCode event MUST be dropped (no SMTP) — it is the wrong-link "
        "duplicate of the admin-invite invite.code.added mail."
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
