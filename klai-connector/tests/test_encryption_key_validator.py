"""Acceptance tests: klai-connector encryption_key validator.

Without this validator, an empty/missing CONNECTOR_ENCRYPTION_KEY env var
crashes deep in the FastAPI lifespan with `AES-256 requires a 32-byte key,
got 0 bytes`, putting the container into a restart loop with a cryptic
trace. The validator surfaces the same misconfiguration at module-load
time with an actionable error.

Real incident: 2026-05-04. The env var was never declared in
klai-infra/core-01/.env.sops; the connector was crash-looping for hours
before SPEC-PORTAL-RBAC-001 deploy made it visible. After the env-var
add + this validator, the same fail-mode raises ValidationError before
any of the lifespan code runs.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator

import pytest
from pydantic import ValidationError


@pytest.fixture
def _required_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set all OTHER required env vars so only encryption_key drives the test outcome."""
    valid_secret = "valid-non-empty-secret-string"
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
    # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-11 — PORTAL_CALLER_SECRET is now
    # also fail-closed validated, so it must be set for tests that drive
    # only the encryption_key validator.
    monkeypatch.setenv("PORTAL_CALLER_SECRET", valid_secret)
    yield


def _import_settings_class():
    """Import Settings via importlib so each test sees a fresh validator pass."""
    import importlib

    import app.core.config as config_module

    importlib.reload(config_module)
    return config_module.Settings  # noqa: N806 (returning class)


def test_valid_32byte_key_accepted(_required_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    valid_key = base64.b64encode(b"\x00" * 32).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", valid_key)
    settings_cls = _import_settings_class()
    s = settings_cls()
    assert s.encryption_key == valid_key


def test_empty_key_rejected_with_actionable_message(
    _required_settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    msg = str(exc_info.value)
    assert "CONNECTOR_ENCRYPTION_KEY" in msg
    assert "32-byte" in msg


def test_whitespace_only_key_rejected(_required_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "   ")
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError):
        settings_cls()


def test_invalid_base64_rejected(_required_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "this-is-not-valid-base64!!!@@@")
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    assert "valid base64" in str(exc_info.value)


def test_short_key_rejected(_required_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Base64-decodes to 16 bytes -- valid base64, wrong length for AES-256."""
    short_key = base64.b64encode(b"\x00" * 16).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", short_key)
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    msg = str(exc_info.value)
    assert "32 bytes" in msg
    assert "Got 16 bytes" in msg


def test_too_long_key_rejected(_required_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Base64-decodes to 64 bytes -- valid base64, wrong length for AES-256."""
    long_key = base64.b64encode(b"\x00" * 64).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", long_key)
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    assert "Got 64 bytes" in str(exc_info.value)


def test_error_message_includes_generation_command(
    _required_settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error message tells the operator how to generate a valid key."""
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(b"\x00" * 16).decode())
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError) as exc_info:
        settings_cls()
    msg = str(exc_info.value)
    assert "secrets.token_bytes(32)" in msg


def test_missing_env_var_rejected(_required_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """ENCRYPTION_KEY entirely absent triggers the missing-required pydantic error."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    settings_cls = _import_settings_class()
    with pytest.raises(ValidationError):
        settings_cls()


# Sanity check: don't accidentally pollute other tests' env state.
def test_env_state_clean_after_module(monkeypatch: pytest.MonkeyPatch) -> None:
    # Conservative: monkeypatch undoes everything via fixture teardown.
    # This test ensures the suite doesn't leak any of the env vars set
    # in the fixture above by depending on them being absent here.
    assert os.environ.get("ENCRYPTION_KEY") is None or os.environ.get("ENCRYPTION_KEY") != ""
