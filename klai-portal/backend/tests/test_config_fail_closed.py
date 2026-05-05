"""SPEC-SEC-VALIDATOR-COVERAGE-001: fail-closed startup validators for portal-api.

Each of the 10 @model_validator(mode="after") methods in
klai-portal/backend/app/core/config.py must raise ValidationError when its
corresponding env var is empty or whitespace-only.

Test strategy:
- For each field, monkeypatch its env var to "" (empty) and "   " (whitespace).
- All OTHER required env vars must be non-empty so only the field under test
  fails. The conftest.py module-level os.environ.setdefault calls handle the
  baseline; individual tests override only the field under test.
- We re-import the module after each monkeypatch so the module-level
  `settings = Settings()` singleton fires again with the patched env.

Pattern mirrors klai-mailer/tests/test_config_fail_closed.py.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _reimport_config() -> None:
    """Drop the cached app.core.config module and re-import it.

    The module-level `settings = Settings()` call fires on import, so this
    triggers the validator under test with whatever env is currently patched.
    """
    sys.modules.pop("app.core.config", None)
    importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-1: INTERNAL_SECRET
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_internal_secret(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-1: empty / whitespace INTERNAL_SECRET raises ValidationError at startup."""
    monkeypatch.setenv("INTERNAL_SECRET", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="INTERNAL_SECRET"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-2: KLAI_CONNECTOR_SECRET
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_klai_connector_secret(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-2: empty / whitespace KLAI_CONNECTOR_SECRET raises ValidationError at startup."""
    monkeypatch.setenv("KLAI_CONNECTOR_SECRET", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="KLAI_CONNECTOR_SECRET"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-3: KNOWLEDGE_INGEST_SECRET
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_knowledge_ingest_secret(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-3: empty / whitespace KNOWLEDGE_INGEST_SECRET raises ValidationError at startup."""
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="KNOWLEDGE_INGEST_SECRET"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-4: RETRIEVAL_API_INTERNAL_SECRET
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_retrieval_api_internal_secret(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """REQ-4: empty / whitespace RETRIEVAL_API_INTERNAL_SECRET raises ValidationError at startup."""
    monkeypatch.setenv("RETRIEVAL_API_INTERNAL_SECRET", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="RETRIEVAL_API_INTERNAL_SECRET"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-5: DOCS_INTERNAL_SECRET
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_docs_internal_secret(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-5: empty / whitespace DOCS_INTERNAL_SECRET raises ValidationError at startup."""
    monkeypatch.setenv("DOCS_INTERNAL_SECRET", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="DOCS_INTERNAL_SECRET"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-6: ZITADEL_PORTAL_CLIENT_SECRET
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_zitadel_portal_client_secret(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """REQ-6: empty / whitespace ZITADEL_PORTAL_CLIENT_SECRET raises ValidationError at startup."""
    monkeypatch.setenv("ZITADEL_PORTAL_CLIENT_SECRET", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="ZITADEL_PORTAL_CLIENT_SECRET"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-7: PORTAL_SECRETS_KEY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_portal_secrets_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-7: empty / whitespace PORTAL_SECRETS_KEY raises ValidationError at startup."""
    monkeypatch.setenv("PORTAL_SECRETS_KEY", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="PORTAL_SECRETS_KEY"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-8: ENCRYPTION_KEY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_encryption_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-8: empty / whitespace ENCRYPTION_KEY raises ValidationError at startup."""
    monkeypatch.setenv("ENCRYPTION_KEY", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-9: SSO_COOKIE_KEY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_sso_cookie_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-9: empty / whitespace SSO_COOKIE_KEY raises ValidationError at startup."""
    monkeypatch.setenv("SSO_COOKIE_KEY", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="SSO_COOKIE_KEY"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# REQ-10: BFF_SESSION_KEY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "whitespace"])
def test_settings_startup_fails_without_bff_session_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """REQ-10: empty / whitespace BFF_SESSION_KEY raises ValidationError at startup."""
    monkeypatch.setenv("BFF_SESSION_KEY", value)
    sys.modules.pop("app.core.config", None)
    with pytest.raises(ValidationError, match="BFF_SESSION_KEY"):
        importlib.import_module("app.core.config")


# ---------------------------------------------------------------------------
# Sanity: all fields populated -> Settings constructs without error
# ---------------------------------------------------------------------------


def test_valid_env_constructs_settings() -> None:
    """Sanity check: with all required env vars set, Settings() succeeds.

    This test relies on conftest.py having set all required env vars via
    os.environ.setdefault at module load time. If this test fails, a required
    env var is missing from conftest.py.
    """
    sys.modules.pop("app.core.config", None)
    import app.core.config as cfg

    assert cfg.settings.internal_secret
    assert cfg.settings.klai_connector_secret
    assert cfg.settings.knowledge_ingest_secret
    assert cfg.settings.retrieval_api_internal_secret
    assert cfg.settings.docs_internal_secret
    assert cfg.settings.zitadel_portal_client_secret
    assert cfg.settings.portal_secrets_key
    assert cfg.settings.encryption_key
    assert cfg.settings.sso_cookie_key
    assert cfg.settings.bff_session_key
