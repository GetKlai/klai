"""Pytest bootstrap for scribe-api unit tests.

Sets required env vars BEFORE `app.core.config.Settings` is instantiated at
module import time. pytest-asyncio is configured to auto mode in
pyproject.toml, so async tests run without per-function markers.
"""
from __future__ import annotations

import os

# `app.core.config` instantiates Settings() at module import, which fails
# without these env vars. Set them here so importing anything from the app
# inside a test module works out of the box.
_DEFAULTS: dict[str, str] = {
    "POSTGRES_DSN": "postgresql+asyncpg://test:test@localhost:5432/scribe_test",
    # Must satisfy the allowlist in `app.core.config._WHISPER_ALLOWED_HOSTS`
    # (SPEC-SEC-HYGIENE-001 REQ-37.1). `whisper-server` is in the allowlist.
    "WHISPER_SERVER_URL": "http://whisper-server:8080",
    "WHISPER_PROVIDER_NAME": "vexa-transcription-service",
    "STT_PROVIDER": "whisper_http",
    "ZITADEL_ISSUER": "https://auth.test.local",
}
for _k, _v in _DEFAULTS.items():
    os.environ.setdefault(_k, _v)
