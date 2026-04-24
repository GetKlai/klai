"""
SPEC-SEC-WEBHOOK-001 REQ-3 / REQ-4 — Moneybird webhook auth hardening.

Pre-SPEC defects closed:
- `if settings.moneybird_webhook_token:` made the check optional when the env
  var was empty → any unauthenticated POST could flip PortalOrg.billing_status
  to active, cancelled, or payment_failed.
- Token comparison used Python `!=`, a non-constant-time primitive.
- Auth failure returned HTTP 200, hiding the rejection from upstream monitoring.

After this SPEC:
- Empty/whitespace `MONEYBIRD_WEBHOOK_TOKEN` aborts app startup (REQ-3.1).
- Token compared with `hmac.compare_digest` on byte-encoded operands (REQ-4.1).
- Auth failure returns HTTP 401 and emits `moneybird_webhook_auth_failed`
  structlog event (REQ-4.2, REQ-4.3).

NOTE on env-parity: this test file pairs with the
`_require_moneybird_webhook_token` pydantic validator. The var MUST be
present in klai-infra/core-01/.env.sops before this code lands in prod —
see pitfall `validator-env-parity` in .claude/rules/klai/pitfalls/process-rules.md.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# ---------------------------------------------------------------------------
# REQ-3: Startup validator rejects empty / whitespace-only secret
# ---------------------------------------------------------------------------


def test_settings_startup_fails_without_moneybird_webhook_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty MONEYBIRD_WEBHOOK_TOKEN at Settings() construction aborts startup."""
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("ZITADEL_PAT", os.environ["ZITADEL_PAT"])
    monkeypatch.setenv("SSO_COOKIE_KEY", os.environ["SSO_COOKIE_KEY"])
    monkeypatch.setenv("PORTAL_SECRETS_KEY", os.environ["PORTAL_SECRETS_KEY"])
    monkeypatch.setenv("ENCRYPTION_KEY", os.environ["ENCRYPTION_KEY"])
    monkeypatch.setenv("VEXA_WEBHOOK_SECRET", os.environ["VEXA_WEBHOOK_SECRET"])
    monkeypatch.setenv("MONEYBIRD_WEBHOOK_TOKEN", "")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "MONEYBIRD_WEBHOOK_TOKEN" in str(excinfo.value)


def test_settings_startup_fails_with_whitespace_only_moneybird_webhook_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only value is equivalent to empty — same rejection."""
    monkeypatch.setenv("MONEYBIRD_WEBHOOK_TOKEN", "   ")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "MONEYBIRD_WEBHOOK_TOKEN" in str(excinfo.value)


# ---------------------------------------------------------------------------
# REQ-4: hmac.compare_digest + HTTP 401 on failure + structlog event
# ---------------------------------------------------------------------------


@pytest.fixture
def moneybird_client():
    """TestClient bound to a throwaway app that mounts ONLY the webhooks router.

    The DB dependency is overridden with a stub because auth failures reject
    before touching the DB.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.webhooks import router as webhooks_router
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(webhooks_router)

    async def _fake_db():
        class _Stub:
            async def execute(self, *_args, **_kwargs):  # pragma: no cover - unreached
                raise AssertionError("DB must not be touched on auth failure")

            async def commit(self):  # pragma: no cover - unreached
                raise AssertionError("DB must not be touched on auth failure")

        yield _Stub()

    app.dependency_overrides[get_db] = _fake_db

    with TestClient(app) as client:
        yield client


def test_moneybird_webhook_wrong_token_returns_401(moneybird_client) -> None:
    """REQ-4.2: wrong token → HTTP 401, not 200."""
    response = moneybird_client.post(
        "/api/webhooks/moneybird",
        json={
            "webhook_token": "wrong-token",
            "entity_type": "Contact",
            "event": "contact_mandate_request_succeeded",
            "entity": {"id": "123"},
        },
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_moneybird_webhook_missing_token_returns_401(moneybird_client) -> None:
    """Payload without a webhook_token field is rejected — empty string does not
    match the configured secret because compare_digest is constant-time against
    any non-matching operand, not lenient on length."""
    response = moneybird_client.post(
        "/api/webhooks/moneybird",
        json={"entity_type": "Contact", "event": "whatever"},
    )
    assert response.status_code == 401


def test_moneybird_webhook_correct_token_does_not_401(moneybird_client) -> None:
    """REQ-4.2 positive path: a matching token passes the auth gate.

    Conftest default `MONEYBIRD_WEBHOOK_TOKEN=test-moneybird-webhook-token`
    is the expected token value.
    """
    response = moneybird_client.post(
        "/api/webhooks/moneybird",
        json={
            "webhook_token": "test-moneybird-webhook-token",
            "entity_type": "Unknown",
            "event": "ignored",
        },
    )
    assert response.status_code == 200


def test_moneybird_webhook_uses_constant_time_compare(moneybird_client) -> None:
    """REQ-4.1: hmac.compare_digest must be used. We spy on the symbol imported
    into the webhooks module and verify byte-encoded operands."""
    import hmac
    from unittest.mock import patch

    with patch("app.api.webhooks.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
        moneybird_client.post(
            "/api/webhooks/moneybird",
            json={"webhook_token": "test-moneybird-webhook-token", "entity_type": "X", "event": "y"},
        )

    assert spy.called
    args, _kwargs = spy.call_args
    assert isinstance(args[0], bytes)
    assert isinstance(args[1], bytes)


def test_moneybird_webhook_auth_failure_emits_structlog_event(moneybird_client) -> None:
    """REQ-4.3: auth failure emits `moneybird_webhook_auth_failed` with
    correlation fields. Patch the module-level structlog logger and assert on
    its `warning()` call — robust regardless of the process-wide structlog
    renderer configuration."""
    from unittest.mock import MagicMock, patch

    fake_logger = MagicMock()
    with patch("app.api.webhooks._structlog_logger", fake_logger):
        moneybird_client.post(
            "/api/webhooks/moneybird",
            json={"webhook_token": "wrong", "entity_type": "Contact", "event": "the_event"},
        )

    fake_logger.warning.assert_called_once()
    args, kwargs = fake_logger.warning.call_args
    assert args[0] == "moneybird_webhook_auth_failed"
    assert kwargs.get("event_type") == "the_event"
    assert kwargs.get("entity_type") == "Contact"
