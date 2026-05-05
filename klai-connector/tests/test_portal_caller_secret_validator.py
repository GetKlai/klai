"""SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-11 — fail-closed validator for
``PORTAL_CALLER_SECRET``.

klai-connector verifies portal-api's inbound calls (sync trigger,
OAuth callbacks) by comparing the X-Internal-Secret header against
``Settings.portal_caller_secret`` via ``hmac.compare_digest``. An
empty/whitespace value would make every comparison succeed against a
literal-empty attacker request — fail-open-auth pitfall.

Mirrors ``test_encryption_key_validator.py`` for fixture pattern.
Pre-flight: PORTAL_CALLER_SECRET (sourced from PORTAL_API_KLAI_CONNECTOR_SECRET
in compose) verified populated in /opt/klai/.env on core-01 before this
validator landed (validator-env-parity pitfall).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator

import pytest
from pydantic import ValidationError


@pytest.fixture
def _required_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set every OTHER required env var so only PORTAL_CALLER_SECRET drives the outcome."""
    valid_secret = "valid-non-empty-secret-string"
    valid_key = base64.b64encode(b"\x00" * 32).decode()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://klai:pw@localhost:5432/klai")
    monkeypatch.setenv("ZITADEL_INTROSPECTION_URL", "https://auth.example.com/introspect")
    monkeypatch.setenv("ZITADEL_CLIENT_ID", "klai-connector")
    monkeypatch.setenv("ZITADEL_CLIENT_SECRET", valid_secret)
    monkeypatch.setenv("ZITADEL_API_AUDIENCE", "klai-connector-app")
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")
    monkeypatch.setenv("KNOWLEDGE_INGEST_URL", "http://knowledge-ingest:8000")
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", valid_secret)
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", valid_secret)
    monkeypatch.setenv("ENCRYPTION_KEY", valid_key)
    yield


def _import_settings_class():
    """Import Settings via importlib so each test sees a fresh validator pass."""
    import importlib

    import app.core.config as config_module

    importlib.reload(config_module)
    return config_module.Settings  # noqa: N806 (returning class)


def test_valid_portal_caller_secret_accepted(
    _required_settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTAL_CALLER_SECRET", "valid-non-empty-secret-string")
    settings_cls = _import_settings_class()
    s = settings_cls()
    assert s.portal_caller_secret == "valid-non-empty-secret-string"


def test_empty_portal_caller_secret_rejected_with_actionable_message(
    _required_settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTAL_CALLER_SECRET", "")
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    msg = str(exc_info.value)
    assert "PORTAL_CALLER_SECRET" in msg
    assert "SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-11" in msg


def test_whitespace_only_portal_caller_secret_rejected(
    _required_settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTAL_CALLER_SECRET", "   ")
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError):
        settings_cls()


def test_missing_portal_caller_secret_env_rejected(
    _required_settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PORTAL_CALLER_SECRET entirely absent triggers the validator since the field default is ``""``."""
    monkeypatch.delenv("PORTAL_CALLER_SECRET", raising=False)
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    assert "PORTAL_CALLER_SECRET" in str(exc_info.value)


def test_env_state_clean_after_monkeypatch_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pytest's monkeypatch teardown removed env mutations from prior tests.

    Pre-fix the assertion was `X is None or X != ""` — logically equivalent to
    `X != ""`, which is True for any non-empty value AND True for None. A real
    env-leak (PORTAL_CALLER_SECRET inherited from another test's setenv that
    bypassed monkeypatch) would still produce X != "" → assertion still passes.
    The test was a tautology. (Audit 2026-05-05 finding 1.)

    Real isolation check: assert the env var is either entirely absent OR
    matches what conftest seeds at module-load time (if any). In our setup
    conftest.py does NOT seed PORTAL_CALLER_SECRET — only the in-test
    monkeypatch fixtures do. So a clean teardown means absent.
    """
    assert os.environ.get("PORTAL_CALLER_SECRET") is None, (
        "PORTAL_CALLER_SECRET leaked across tests — monkeypatch teardown failed "
        "or some fixture bypassed monkeypatch with os.environ direct mutation."
    )
