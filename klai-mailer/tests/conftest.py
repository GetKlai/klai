"""Shared pytest fixtures for klai-mailer tests.

Provides:
- `settings_env`: minimal valid env vars for constructing `Settings`
- `fake_redis`: fakeredis.aioredis client
- `stub_smtp`: captures outbound SMTP calls without hitting the network
- `portal_api_mock`: respx router pre-seeded with common portal-api responses
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

import pytest

# Minimal env vars required by Settings() — applied via monkeypatch per test that
# constructs Settings. Do NOT leak into os.environ unconditionally (other tests
# may want the absence-case).
DEFAULT_TEST_ENV: dict[str, str] = {
    "SMTP_HOST": "smtp.test.local",
    "SMTP_USERNAME": "mailer@test",
    "SMTP_PASSWORD": "smtp-test-secret",
    "SMTP_FROM": "noreply@test.example",
    "WEBHOOK_SECRET": "webhook-test-secret",
    "INTERNAL_SECRET": "internal-test-secret",
    "BRAND_URL": "https://test.klai.example",
    "LOGO_URL": "https://test.klai.example/logo.png",
    "PORTAL_API_URL": "http://portal-api.test.local:8010",
    "PORTAL_INTERNAL_SECRET": "portal-internal-test-secret",
    "PORTAL_ENV": "development",
    "REDIS_URL": "redis://redis-test.local:6379/0",
    "DEBUG": "false",
}


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Apply the default valid env vars so `Settings()` succeeds."""
    for k, v in DEFAULT_TEST_ENV.items():
        monkeypatch.setenv(k, v)
    return dict(DEFAULT_TEST_ENV)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty env so per-test monkeypatch.setenv is deterministic."""
    for key in list(os.environ):
        if key in DEFAULT_TEST_ENV or key.startswith((
            "SMTP_", "WEBHOOK_", "INTERNAL_", "BRAND_", "LOGO_",
            "PORTAL_", "REDIS_", "DEBUG", "MAILER_",
        )):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# StubSMTPSender — captures send_email() calls in-process
# ---------------------------------------------------------------------------


@dataclass
class StubSMTPSender:
    """Captures outbound email calls made via app.mailer.send_email."""

    sent: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        to_address: str,
        subject: str,
        html_body: str,
    ) -> None:
        self.sent.append({
            "to_address": to_address,
            "subject": subject,
            "html_body": html_body,
        })

    def reset(self) -> None:
        self.sent.clear()


@pytest.fixture
def stub_smtp(monkeypatch: pytest.MonkeyPatch) -> StubSMTPSender:
    """Install a stub SMTP sender that captures calls in-process.

    Both `app.mailer.send_email` AND any module that has already imported it as
    `from app.mailer import send_email` must be patched. We patch both the
    source module and main.py (which is where the import lands at runtime).
    """
    stub = StubSMTPSender()
    # Patch source module
    import app.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "send_email", stub, raising=True)
    # Patch the re-export used by main.py (only if main is already imported)
    try:
        import app.main as main_module
        if hasattr(main_module, "send_email"):
            monkeypatch.setattr(main_module, "send_email", stub, raising=False)
    except ImportError:
        pass
    return stub


# ---------------------------------------------------------------------------
# fakeredis fixture — in-memory Redis for nonce + rate-limit tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def fake_redis() -> AsyncIterator[Any]:
    """Async fakeredis client shared across a single test."""
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def broken_redis() -> Any:
    """A Redis-shaped object whose every operation raises ConnectionError.

    Used to exercise REQ-4.3 (fail-open on rate-limit Redis outage) and REQ-6.3
    (fail-closed on nonce Redis outage).
    """

    class _BrokenPipeline:
        """Sync-queue pipeline whose execute() raises ConnectionError.

        Real redis-py pipelines queue commands synchronously; only `execute`
        is async. We mirror that so rate_limit.py's pipeline use doesn't
        produce un-awaited-coroutine warnings.
        """

        def zadd(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        def zremrangebyscore(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        def zcard(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        def zrange(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        def zrem(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        def expire(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        def set(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return self

        async def execute(self) -> None:
            raise ConnectionError("fake redis unavailable")

        async def __aenter__(self) -> _BrokenPipeline:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _BrokenRedis:
        async def set(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def get(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def zadd(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def zremrangebyscore(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def zcard(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def zrange(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def zrem(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def expire(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("fake redis unavailable")

        async def aclose(self) -> None:
            return None

        def pipeline(self, *args: Any, **kwargs: Any) -> _BrokenPipeline:
            return _BrokenPipeline()

    return _BrokenRedis()


# ---------------------------------------------------------------------------
# App factory — yields a fresh FastAPI app with isolated deps
# ---------------------------------------------------------------------------


@pytest.fixture
def app_factory(monkeypatch: pytest.MonkeyPatch, settings_env: dict[str, str]) -> Iterator[Any]:
    """Factory that yields a fresh `app.main` module on demand.

    Re-imports app.main after env changes so module-level state (settings,
    conditional route registration) reflects the test configuration.
    """
    import importlib
    import sys

    # Ensure clean module graph; drop cached config + main
    for mod in ("app.main", "app.config", "app.renderer", "app.nonce", "app.rate_limit"):
        sys.modules.pop(mod, None)

    created_modules: list[str] = []

    def _factory(**env_overrides: str) -> Any:
        for k, v in env_overrides.items():
            monkeypatch.setenv(k.upper(), v)
        # Drop cached modules again so new env takes effect
        for mod in ("app.main", "app.config", "app.renderer", "app.nonce", "app.rate_limit"):
            sys.modules.pop(mod, None)
        module = importlib.import_module("app.main")
        created_modules.append("app.main")
        return module

    yield _factory

    for mod in (
        *created_modules,
        "app.main",
        "app.config",
        "app.renderer",
        "app.nonce",
        "app.rate_limit",
    ):
        sys.modules.pop(mod, None)
