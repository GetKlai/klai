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


@pytest.fixture(autouse=True)
def _reset_process_caches() -> Iterator[None]:
    """The conversation code keeps short-lived per-process state; no test may see another's."""
    from app.conversation import render, sessions, turn
    from app.services import allowed_kbs, slack_users

    yield
    for cache in (
        allowed_kbs._cache,
        slack_users._langs,
        sessions._unsupported,
        render._sources,
        turn._pending,
    ):
        cache.clear()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, fake_db: object) -> Any:
    """In-memory replacement for the repo functions the conversation code calls."""
    from app import repo
    from tests.fakes import KEY, Store

    state = Store()
    for name in Store.REPO_FUNCTIONS:
        monkeypatch.setattr(repo, name, getattr(state, name))
    monkeypatch.setattr(repo, "link_api_key", lambda _link: KEY)
    return state


@pytest.fixture
def partner(monkeypatch: pytest.MonkeyPatch) -> Any:
    import httpx

    from app.clients.partner import PartnerClient
    from app.services import allowed_kbs
    from tests.fakes import Partner

    fake = Partner()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(
        allowed_kbs,
        "_partner",
        lambda base_url, api_key: PartnerClient(base_url, api_key, transport=transport),
    )
    return fake


@pytest.fixture
def slack() -> Any:
    from tests.fakes import FakeSlack

    return FakeSlack()
