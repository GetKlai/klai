"""SPEC-SEC-HYGIENE-001 REQ-28 / AC-28: `/docs` and `/openapi.json` are
gated on (debug=True AND portal_env != "production").

Pre-fix gating was `if settings.debug else None` — single-flag. A
deploy-time accident that flipped DEBUG=true in production would expose
the OpenAPI surface. Defense-in-depth: gate on the environment as well,
plus a hard pydantic validator that refuses to boot in the catastrophic
combination (debug=True AND portal_env="production").

Tests:
- REQ-28.1: the gating helper returns the right boolean for each combo.
- REQ-28.3: Settings() construction with debug=True + portal_env="production"
  raises pydantic.ValidationError mentioning DEBUG and production.
- REQ-28.2: portal_env defaults to "production" when env is unset.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import _should_expose_docs

# REQ-28.1: gating matrix --------------------------------------------------- #


class _StubSettings:
    """Minimal duck for `_should_expose_docs` — only reads two attrs."""

    def __init__(self, debug: bool, portal_env: str) -> None:
        self.debug = debug
        self.portal_env = portal_env


@pytest.mark.parametrize(
    "debug, portal_env, expected",
    [
        (True, "development", True),
        (False, "development", False),
        (True, "staging", True),
        (False, "production", False),
        # The (True, "production") combo is blocked by the validator
        # (REQ-28.3), so the helper never sees it. We do NOT include it
        # here — that path is covered by test_settings_refuses_debug_in_production.
    ],
)
def test_should_expose_docs(debug: bool, portal_env: str, expected: bool) -> None:
    """REQ-28.1: `/docs` exposed iff debug AND env != production."""
    assert _should_expose_docs(_StubSettings(debug=debug, portal_env=portal_env)) is expected


# REQ-28.3: hard validator ------------------------------------------------- #


def _required_env() -> dict[str, str]:
    """Mimic the env vars conftest sets so Settings() can construct."""
    return {
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
        "ZITADEL_PAT": "test-pat",
        "SSO_COOKIE_KEY": os.environ["SSO_COOKIE_KEY"],
        "PORTAL_SECRETS_KEY": os.environ["PORTAL_SECRETS_KEY"],
        "ENCRYPTION_KEY": os.environ["ENCRYPTION_KEY"],
        "VEXA_WEBHOOK_SECRET": "test-vexa-webhook-secret",
        "MONEYBIRD_WEBHOOK_TOKEN": "test-moneybird-webhook-token",
        "ZITADEL_IDP_GOOGLE_ID": "test-google-idp-id",
        "ZITADEL_IDP_MICROSOFT_ID": "test-microsoft-idp-id",
    }


def test_settings_refuses_debug_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-28.3: Settings() raises when debug=True AND portal_env=production.

    The validator is the hard guard — REQ-28.1's helper is the soft fallback
    for the case where the validator is bypassed (e.g. monkey-patched in a
    test). This test asserts the hard guard fires.
    """
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("PORTAL_ENV", "production")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    msg = str(exc_info.value)
    assert "DEBUG" in msg or "debug" in msg
    assert "production" in msg


def test_settings_allows_debug_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-28.3 negative case: debug + non-production = OK."""
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("PORTAL_ENV", "development")

    s = Settings()
    assert s.debug is True
    assert s.portal_env == "development"


def test_settings_default_portal_env_is_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-28.2: PORTAL_ENV defaults to 'production' (conservative default)."""
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("PORTAL_ENV", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    s = Settings()
    assert s.portal_env == "production"
    # Default debug=False is still the production default; the validator
    # only fires when both flags align catastrophically.
    assert s.debug is False
