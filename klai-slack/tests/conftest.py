"""Shared fixtures. Every value here is a synthetic placeholder."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

TEST_ENV: dict[str, str] = {
    "SLACK_CLIENT_ID": "1111.2222",
    "SLACK_CLIENT_SECRET": "test-client-secret",
    "SLACK_SIGNING_SECRET": "test-signing-secret",
    "SLACK_ENCRYPTION_KEY": base64.b64encode(bytes(range(32))).decode(),
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/klai_slack_test",
    "PORTAL_API_URL": "http://portal-api.test:8010",
    "PORTAL_INTERNAL_SECRET": "test-portal-internal-secret",
    "INTERNAL_SECRET": "test-internal-secret",
    "PUBLIC_BASE_URL": "https://slack.example.com",
    "KLAI_ENV": "test",
}

# Module-level so `get_settings()` (used lazily by app.crypto) resolves in every test.
for _key, _value in TEST_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in TEST_ENV:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> object:
    """Replace ``db.session()`` with a context manager yielding a sentinel session."""
    from app import db

    sentinel = object()

    @asynccontextmanager
    async def _session() -> AsyncIterator[Any]:
        yield sentinel

    monkeypatch.setattr(db, "session", _session)
    return sentinel
