"""SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-12 — fail-closed validator for
``GITEA_WEBHOOK_SECRET``.

Gitea POSTs to ``/webhooks/gitea`` on every push to a tenant-tracked
repository. The handler verifies the X-Gitea-Signature HMAC against
``Settings.gitea_webhook_secret`` via ``hmac.compare_digest``. With an
empty/whitespace-only value, every comparison would succeed against an
attacker request that also has an empty signature — exact fail-open-auth
pattern from .claude/rules/klai/pitfalls/process-rules.md.

Pre-flight: GITEA_WEBHOOK_SECRET verified populated in /opt/klai/.env
on core-01 before this validator landed (validator-env-parity pitfall).
"""

from __future__ import annotations

import importlib
import sys

import pytest
from pydantic import ValidationError


def _reload_config_module(monkeypatch: pytest.MonkeyPatch):
    """Re-import knowledge_ingest.config so the module-level Settings()
    instantiation re-runs under the test's monkey-patched env.

    Saves and restores the original ``knowledge_ingest.config`` module
    via ``monkeypatch.setitem`` so subsequent tests in the suite still
    see the original ``settings`` singleton. Without this restoration,
    the autouse fixture in ``conftest.py`` that patches
    ``settings.enrichment_enabled = False`` ends up patching a stale
    settings instance, which in turn lets the FastAPI lifespan hit the
    enrichment-worker bootstrap and cascade-fail across ~50 unrelated
    TestClient-based tests.
    """
    original = sys.modules.get("knowledge_ingest.config")
    if original is not None:
        # Snapshot the current module so monkeypatch puts it back on
        # teardown. ``monkeypatch.setitem`` restores both the value and
        # the key's presence/absence, mirroring the contract.
        monkeypatch.setitem(sys.modules, "knowledge_ingest.config", original)
    sys.modules.pop("knowledge_ingest.config", None)


def _import_settings_class():
    import knowledge_ingest.config as config_module

    return config_module.Settings


def test_valid_gitea_webhook_secret_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: non-empty value lets the module import + the singleton construct.

    Imports knowledge_ingest.config to exercise the actual production code
    path (module-level ``settings = Settings()`` runs under the patched env),
    not just direct ``Settings()`` construction. Audit 2026-05-05 finding 2:
    the previous test bypassed import-time validation and would silently
    pass even if the module-level singleton was broken.
    """
    monkeypatch.setenv("GITEA_WEBHOOK_SECRET", "valid-non-empty-webhook-secret")
    _reload_config_module(monkeypatch)
    config_module = importlib.import_module("knowledge_ingest.config")
    assert config_module.settings.gitea_webhook_secret == "valid-non-empty-webhook-secret"


@pytest.mark.parametrize("value", ["", "   ", "\t\n "], ids=["empty", "spaces", "whitespace"])
def test_empty_or_whitespace_gitea_webhook_secret_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """REQ-12: empty / whitespace-only GITEA_WEBHOOK_SECRET raises ValidationError.

    The module-level ``settings = Settings()`` in knowledge_ingest.config
    means the failure surfaces at IMPORT TIME — that is the actual
    production behaviour (container refuses to start). The ``with
    pytest.raises`` block must wrap the import itself, not the
    Settings() class call after a successful import.
    """
    monkeypatch.setenv("GITEA_WEBHOOK_SECRET", value)
    _reload_config_module(monkeypatch)
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("knowledge_ingest.config")
    msg = str(exc_info.value)
    assert "GITEA_WEBHOOK_SECRET" in msg
    assert "SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-12" in msg


def test_missing_gitea_webhook_secret_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """GITEA_WEBHOOK_SECRET absent triggers the validator: field default is empty string."""
    monkeypatch.delenv("GITEA_WEBHOOK_SECRET", raising=False)
    _reload_config_module(monkeypatch)
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("knowledge_ingest.config")
    assert "GITEA_WEBHOOK_SECRET" in str(exc_info.value)


def test_module_level_settings_singleton_fails_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module-level ``settings = Settings()`` in knowledge_ingest.config
    triggers the same validator. An empty env var refuses module import,
    which is the actual production behaviour: container fails to start."""
    monkeypatch.setenv("GITEA_WEBHOOK_SECRET", "")
    sys.modules.pop("knowledge_ingest.config", None)
    with pytest.raises(ValidationError):
        importlib.import_module("knowledge_ingest.config")
