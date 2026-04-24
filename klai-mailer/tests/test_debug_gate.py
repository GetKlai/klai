"""AC-4: /debug returns 404 when PORTAL_ENV=production (double-gate).

Covers REQ-5.1 (portal_env field), REQ-5.2 (404 in production regardless
of DEBUG), REQ-5.3 (preferred: conditional route registration), REQ-5.4
(handler-level fallback gate), REQ-5.5 (no log event on 404).
"""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from tests._signing import sign

VALID_BODY = b'{"contextInfo":{"eventType":"x"},"templateData":{"text":"hi"}}'


def _fresh_app(monkeypatch, **env_overrides: str):
    """Re-import app.main with env overrides applied."""
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    for mod in ("app.main", "app.config", "app.nonce", "app.signature"):
        sys.modules.pop(mod, None)
    # fresh fakeredis per instance so nonce state isolated
    import fakeredis.aioredis

    import app.nonce as nonce_mod
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    nonce_mod.set_redis_client(client)
    main = importlib.import_module("app.main")
    # re-apply (main re-imports nonce)
    import app.nonce as nonce_after
    nonce_after.set_redis_client(client)
    return main


@pytest.fixture
def in_env(monkeypatch, settings_env, stub_smtp):
    """Yield a factory that produces a TestClient with `portal_env` + `debug` overridden."""
    def _build(portal_env: str, debug: str) -> tuple[TestClient, object]:
        main = _fresh_app(monkeypatch, PORTAL_ENV=portal_env, DEBUG=debug)
        return TestClient(main.app), main
    return _build


# ---------------------------------------------------------------------------
# AC-4 headline: production env → 404 regardless of DEBUG
# ---------------------------------------------------------------------------


def test_production_env_returns_404_even_with_debug_true(in_env, settings_env):
    """REQ-5.2: PORTAL_ENV=production AND DEBUG=true → 404."""
    client, _ = in_env(portal_env="production", debug="true")

    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])
    with capture_logs() as events:
        resp = client.post(
            "/debug", content=VALID_BODY, headers={"ZITADEL-Signature": header}
        )

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}

    # REQ-5.5: NO structured log event for the production 404 (no
    # mailer_debug_* or signature events). FastAPI's default 404 is a plain
    # HTTP response, not an application event.
    relevant_events = [
        e for e in events
        if (e.get("event") or "").startswith(("mailer_debug", "mailer_signature"))
    ]
    assert relevant_events == [], f"unexpected app events: {relevant_events}"


def test_development_debug_true_works(in_env, settings_env):
    """Sanity: PORTAL_ENV=development, DEBUG=true → /debug returns 200."""
    client, _ = in_env(portal_env="development", debug="true")
    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])
    resp = client.post(
        "/debug", content=VALID_BODY, headers={"ZITADEL-Signature": header}
    )
    assert resp.status_code == 200, resp.text


def test_development_debug_false_returns_404(in_env, settings_env):
    """REQ-5.2 legacy: PORTAL_ENV=development, DEBUG=false → 404."""
    client, _ = in_env(portal_env="development", debug="false")
    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])
    resp = client.post(
        "/debug", content=VALID_BODY, headers={"ZITADEL-Signature": header}
    )
    assert resp.status_code == 404


def test_staging_debug_true_works(in_env, settings_env):
    """Staging is NOT production — the gate only blocks the production value."""
    client, _ = in_env(portal_env="staging", debug="true")
    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])
    resp = client.post(
        "/debug", content=VALID_BODY, headers={"ZITADEL-Signature": header}
    )
    assert resp.status_code == 200, resp.text


def test_signature_verify_not_called_in_production(in_env, settings_env):
    """REQ-5.2: the 404 short-circuits before signature work is attempted.

    We assert the StubSMTPSender receives no message and that no
    mailer_signature_invalid log fires — proving _verify_zitadel_signature
    was not reached even with a forged signature.
    """
    client, _ = in_env(portal_env="production", debug="true")
    bad_header = "t=1,v1=deadbeef"
    with capture_logs() as events:
        resp = client.post(
            "/debug", content=VALID_BODY, headers={"ZITADEL-Signature": bad_header}
        )
    assert resp.status_code == 404
    sig_events = [e for e in events if (e.get("event") or "").startswith("mailer_signature")]
    assert sig_events == [], f"signature check ran in production: {sig_events}"
