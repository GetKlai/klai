"""AC-7: Empty or whitespace-only required secrets refuse startup.

Covers REQ-9.1 (WEBHOOK_SECRET) and REQ-9.2 (INTERNAL_SECRET). Fail-closed
at `Settings()` construction via pydantic-settings field_validator(mode="after").

The module-level `settings = Settings()` in app/config.py means the failure
manifests at import-time. We construct `Settings()` directly from the class
so the assertion can be scoped precisely.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from pydantic import ValidationError


def _fresh_settings_class():
    """Import `Settings` without triggering the module-level singleton."""
    sys.modules.pop("app.config", None)
    # Import the class only; reference `Settings()` inline so the singleton
    # failure path is exercised AT the construction site of the test body.
    # But app.config runs `settings = Settings()` at module load, which is
    # exactly the startup behaviour we want to test. So instead we re-import
    # via a guarded reload and capture the exception.
    from app.config import Settings

    return Settings


def test_valid_secrets_construct(settings_env, monkeypatch):
    """Sanity: valid env → Settings() succeeds."""
    SettingsCls = _fresh_settings_class()
    settings = SettingsCls()
    assert settings.webhook_secret == "webhook-test-secret"
    assert settings.internal_secret == "internal-test-secret"


@pytest.mark.parametrize("value", ["", "   ", "\t\n "], ids=["empty", "spaces", "whitespace"])
def test_empty_webhook_secret_refuses_startup(settings_env, monkeypatch, value):
    """REQ-9.1: empty / whitespace-only WEBHOOK_SECRET raises ValidationError."""
    monkeypatch.setenv("WEBHOOK_SECRET", value)
    # Drop cached module so re-import triggers the module-level Settings()
    sys.modules.pop("app.config", None)
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("app.config")
    assert "Missing required: WEBHOOK_SECRET" in str(exc_info.value)


@pytest.mark.parametrize("value", ["", "   ", "\t\n "], ids=["empty", "spaces", "whitespace"])
def test_empty_internal_secret_refuses_startup(settings_env, monkeypatch, value):
    """REQ-9.2: empty / whitespace-only INTERNAL_SECRET raises ValidationError."""
    monkeypatch.setenv("INTERNAL_SECRET", value)
    sys.modules.pop("app.config", None)
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("app.config")
    assert "Missing required: INTERNAL_SECRET" in str(exc_info.value)


def test_both_empty_raises(settings_env, monkeypatch):
    """Both secrets empty → import fails with at least one missing error."""
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    monkeypatch.setenv("INTERNAL_SECRET", "")
    sys.modules.pop("app.config", None)
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("app.config")
    msg = str(exc_info.value)
    assert "Missing required: WEBHOOK_SECRET" in msg
    assert "Missing required: INTERNAL_SECRET" in msg


def test_startup_exits_nonzero(settings_env, monkeypatch):
    """REQ-9 operational claim: uvicorn entrypoint cannot bind with empty secret.

    Spawn a subprocess that imports `app.config`; assert non-zero exit and
    the expected error on stderr. Documents the fail-closed property at the
    OS level — a container with empty WEBHOOK_SECRET refuses to boot.
    """
    import os
    import subprocess
    import sys as _sys
    from pathlib import Path

    env = dict(os.environ)
    env["WEBHOOK_SECRET"] = ""

    result = subprocess.run(
        [_sys.executable, "-c", "import app.config"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0, f"expected non-zero exit, stderr: {result.stderr}"
    assert "Missing required: WEBHOOK_SECRET" in result.stderr


# ---------------------------------------------------------------------------
# REQ-6.5 boot validator: REDIS_URL must be structurally parseable.
#
# The 2026-04-29 outage exposed the "service starts cleanly, then 5xx every
# webhook" failure mode. PR #231 fixed the runtime path; this test class
# pins the boot-time fail-fast property added in this commit so a future
# refactor cannot silently regress.
# ---------------------------------------------------------------------------


class TestRedisUrlFailFastValidator:
    """The Settings validator on `redis_url` MUST refuse boot when the URL
    is structurally broken, AND MUST accept any URL that the runtime
    `parse_redis_url` accepts (including passwords with reserved chars)."""

    def test_redis_url_with_reserved_chars_in_password_is_accepted(self, settings_env, monkeypatch):
        """Regression: the 2026-04-29 prod URL had `:`, `/`, `+` in the
        password. The validator MUST accept it because `parse_redis_url`
        handles it. If this regresses, every deploy fails until SOPS is
        edited — exactly the validator-env-parity pitfall."""
        monkeypatch.setenv("REDIS_URL", "redis://:p:hPKBf/abc+def@redis:6379/0")
        sys.modules.pop("app.config", None)
        # No exception expected — settings construct cleanly.
        from app.config import Settings

        settings = Settings()
        assert settings.redis_url == "redis://:p:hPKBf/abc+def@redis:6379/0"

    def test_missing_scheme_refuses_boot(self, settings_env, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "no-scheme")
        sys.modules.pop("app.config", None)
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.config")
        assert "Invalid REDIS_URL" in str(exc_info.value)

    def test_unsupported_scheme_refuses_boot(self, settings_env, monkeypatch):
        """Operator paste-error: `memcached://...` looks similar to
        `redis://...`. The validator catches it at boot, not later."""
        monkeypatch.setenv("REDIS_URL", "memcached://host:11211")
        sys.modules.pop("app.config", None)
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.config")
        assert "Invalid REDIS_URL" in str(exc_info.value)

    def test_non_integer_port_refuses_boot(self, settings_env, monkeypatch):
        """Operator paste-error: a non-numeric port sneaks past the SOPS
        round-trip. Boot fails loudly with the explicit field name."""
        monkeypatch.setenv("REDIS_URL", "redis://redis:not-a-number/0")
        sys.modules.pop("app.config", None)
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.config")
        assert "Invalid REDIS_URL" in str(exc_info.value)

    def test_missing_host_refuses_boot(self, settings_env, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://")
        sys.modules.pop("app.config", None)
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.config")
        assert "Invalid REDIS_URL" in str(exc_info.value)

    def test_default_value_in_dev_settings_is_parseable(self, settings_env, monkeypatch):
        """Conftest default of `redis://redis:6379/0` MUST validate cleanly,
        so the test suite itself doesn't regress whenever a future change
        edits the validator."""
        monkeypatch.delenv("REDIS_URL", raising=False)  # use default
        sys.modules.pop("app.config", None)
        from app.config import Settings

        settings = Settings()
        # The conftest sets a default; whatever it is, it must be valid.
        from app.redis_url import parse_redis_url

        parsed = parse_redis_url(settings.redis_url)
        assert parsed.host  # non-empty
