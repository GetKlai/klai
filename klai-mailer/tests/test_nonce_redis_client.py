"""Tests for `app.nonce.get_redis()` configuration shape selection.

Why this test file exists:
    The production REDIS_PASSWORD contains URL-special characters
    (`/`, `+`, `=`). `redis.asyncio.from_url()` delegates to urllib's
    URL parser, which mis-parses the first `/` after the password as a
    path-segment break — raising `ValueError: Port could not be cast to
    integer value as 'XXX'`. The fix is to support a structured-fields
    config path (host/port/password/db) and use it whenever `redis_host`
    is set, falling back to the URL form for tests using fakeredis URLs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_module_singleton(settings_env):
    """Drop the module-level redis client before each test.

    Depends on `settings_env` to ensure required env (SMTP_*, WEBHOOK_SECRET,
    INTERNAL_SECRET) is set before `app.config.Settings()` is constructed.
    """
    import app.nonce as nonce_mod

    nonce_mod.reset_redis_client()
    yield
    nonce_mod.reset_redis_client()


class TestStructuredRedisConfig:
    """When redis_host is set, get_redis() uses host/port/password/db
    fields directly — bypassing URL parsing."""

    def test_uses_structured_fields_when_redis_host_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import nonce as nonce_mod
        from app.config import settings

        monkeypatch.setattr(settings, "redis_host", "redis")
        monkeypatch.setattr(settings, "redis_port", 6379)
        monkeypatch.setattr(
            settings, "redis_password", "hPKBf/KXA+//OixZhvLswbuQNRFP8zVlMCTcfPsEcDw="
        )
        monkeypatch.setattr(settings, "redis_db", 0)

        with (
            patch("redis.asyncio.Redis") as mock_redis_cls,
            patch("redis.asyncio.from_url") as mock_from_url,
        ):
            nonce_mod.get_redis()

            mock_redis_cls.assert_called_once_with(
                host="redis",
                port=6379,
                password="hPKBf/KXA+//OixZhvLswbuQNRFP8zVlMCTcfPsEcDw=",
                db=0,
                decode_responses=False,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            mock_from_url.assert_not_called()

    def test_empty_password_passed_as_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty redis_password env should map to password=None,
        otherwise redis-py would auth-fail against an unprotected redis."""
        from app import nonce as nonce_mod
        from app.config import settings

        monkeypatch.setattr(settings, "redis_host", "redis")
        monkeypatch.setattr(settings, "redis_password", "")

        with patch("redis.asyncio.Redis") as mock_redis_cls:
            nonce_mod.get_redis()

            _, kwargs = mock_redis_cls.call_args
            assert kwargs["password"] is None


class TestUrlFallback:
    """When redis_host is empty, get_redis() falls back to redis_url —
    used by tests that point at fakeredis or local TCP redis without auth."""

    def test_falls_back_to_url_when_redis_host_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import nonce as nonce_mod
        from app.config import settings

        monkeypatch.setattr(settings, "redis_host", "")
        monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")

        with (
            patch("redis.asyncio.from_url") as mock_from_url,
            patch("redis.asyncio.Redis") as mock_redis_cls,
        ):
            nonce_mod.get_redis()

            mock_from_url.assert_called_once_with(
                "redis://localhost:6379/0",
                decode_responses=False,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            mock_redis_cls.assert_not_called()
