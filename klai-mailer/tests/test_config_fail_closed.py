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
