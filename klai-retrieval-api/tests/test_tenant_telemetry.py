"""SPEC-PRIVACY-QUERY-SHADOW-001 cleanup — canonical level lookup + resolver.

Pure unit tests with mocked asyncpg pool. The cache is module-global,
so each test resets it via ``_reset_cache_for_tests`` to keep tests
order-independent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    from retrieval_api.services import tenant_telemetry as tt

    tt._reset_cache_for_tests()
    yield
    tt._reset_cache_for_tests()


def test_resolve_min_off_wins_over_full() -> None:
    """A tenant on 'off' caps any caller-requested level."""
    from retrieval_api.services.tenant_telemetry import resolve_effective_level

    assert resolve_effective_level("full", "off") == "off"
    assert resolve_effective_level("shadow", "off") == "off"
    assert resolve_effective_level("off", "off") == "off"


def test_resolve_min_caller_can_be_stricter_than_canonical() -> None:
    """A privacy-conscious caller's stricter request wins over a permissive tenant."""
    from retrieval_api.services.tenant_telemetry import resolve_effective_level

    assert resolve_effective_level("shadow", "full") == "shadow"
    assert resolve_effective_level("off", "full") == "off"
    assert resolve_effective_level("off", "shadow") == "off"


def test_resolve_passthrough_when_levels_match() -> None:
    from retrieval_api.services.tenant_telemetry import resolve_effective_level

    assert resolve_effective_level("shadow", "shadow") == "shadow"
    assert resolve_effective_level("full", "full") == "full"


@pytest.mark.asyncio
async def test_canonical_level_returns_db_value(monkeypatch):
    from retrieval_api.services import tenant_telemetry as tt

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value={"telemetry_level": "off"})
    monkeypatch.setattr(tt, "get_pool", lambda: fake_pool)

    level = await tt.get_canonical_level("voys-zit-id")
    assert level == "off"
    fake_pool.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_canonical_level_caches_within_ttl(monkeypatch):
    """A second lookup within TTL hits the cache, not the DB."""
    from retrieval_api.services import tenant_telemetry as tt

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value={"telemetry_level": "full"})
    monkeypatch.setattr(tt, "get_pool", lambda: fake_pool)

    level1 = await tt.get_canonical_level("voys-zit-id")
    level2 = await tt.get_canonical_level("voys-zit-id")
    assert level1 == level2 == "full"
    fake_pool.fetchrow.assert_awaited_once()  # cached on second call


@pytest.mark.asyncio
async def test_canonical_level_fail_open_to_shadow_when_pool_missing(monkeypatch):
    """No pool (test / cold start) → privacy-friendly default."""
    from retrieval_api.services import tenant_telemetry as tt

    monkeypatch.setattr(tt, "get_pool", lambda: None)

    level = await tt.get_canonical_level("any-org")
    assert level == "shadow"


@pytest.mark.asyncio
async def test_canonical_level_fail_open_to_shadow_on_db_error(monkeypatch):
    """Postgres blip → privacy-friendly default. Never default to 'full' on error."""
    from retrieval_api.services import tenant_telemetry as tt

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(side_effect=RuntimeError("connection refused"))
    monkeypatch.setattr(tt, "get_pool", lambda: fake_pool)

    level = await tt.get_canonical_level("any-org")
    assert level == "shadow"


@pytest.mark.asyncio
async def test_canonical_level_unknown_org_defaults_to_shadow(monkeypatch):
    """Tenant not in portal_orgs → privacy-friendly default."""
    from retrieval_api.services import tenant_telemetry as tt

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value=None)
    monkeypatch.setattr(tt, "get_pool", lambda: fake_pool)

    level = await tt.get_canonical_level("unknown-org")
    assert level == "shadow"


@pytest.mark.asyncio
async def test_canonical_level_unknown_enum_value_collapses_to_shadow(monkeypatch):
    """Defense-in-depth: a future enum value the code doesn't know about
    must NOT silently grant 'full'-tier behaviour."""
    from retrieval_api.services import tenant_telemetry as tt

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value={"telemetry_level": "future_mode"})
    monkeypatch.setattr(tt, "get_pool", lambda: fake_pool)

    level = await tt.get_canonical_level("any-org")
    assert level == "shadow"
