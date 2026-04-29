"""Tests for the /internal/librechat/regenerate endpoint.

SPEC-SEC-INTERNAL-001 REQ-2 + AC-2.x: cache invalidation goes through SCAN+UNLINK
on the configured key pattern (``configs:*`` by default), NEVER through FLUSHALL.

Invariants pinned by these tests:

1. FLUSHALL is never invoked. The handler uses ``scan_iter`` + ``unlink`` so
   unrelated keys (rate-limit buckets, SSO cache, partner-API state) survive.
2. Only keys matching ``settings.librechat_cache_key_pattern`` are unlinked.
3. A Redis failure surfaces as a ``redis-cache-invalidation: ...`` entry in
   the response ``errors`` list -- librechat.yaml has no TTL, so a silent
   swallow leaves every tenant reading stale config forever.
4. Per-slug container restart failures do not cancel other slugs.
5. The post-error restart step still runs (``redis-cache-invalidation`` ->
   restart still happens; LibreChat re-reads yaml from disk on startup).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import docker.errors
import pytest
from redis.exceptions import RedisError


def _org(slug: str, org_id: int, mcp_servers: list[str] | None = None) -> MagicMock:
    org = MagicMock()
    org.slug = slug
    org.id = org_id
    org.mcp_servers = mcp_servers or []
    org.provisioning_status = "ready"
    return org


def _db_returning_orgs(orgs: list[MagicMock]) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = orgs
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _redis_mock(
    *,
    keys_for_pattern: dict[str, list[str]] | None = None,
    scan_side_effect: Exception | None = None,
    unlink_side_effect: Exception | None = None,
) -> MagicMock:
    """Fake aioredis.Redis() supporting scan_iter + unlink + async ctx manager.

    ``keys_for_pattern`` maps a glob pattern to the keys SCAN should yield.
    Tests assert against ``client.scan_iter.call_args`` and ``client.unlink.call_args_list``.
    """
    keys_map = keys_for_pattern or {"configs:*": []}
    unlinked: list[tuple[str, ...]] = []

    def scan_iter(match: str, count: int = 100) -> AsyncIterator[str]:
        keys = keys_map.get(match, [])

        async def _aiter() -> AsyncIterator[str]:
            if scan_side_effect is not None:
                raise scan_side_effect
            for key in keys:
                yield key

        return _aiter()

    async def unlink(*keys: str) -> int:
        if unlink_side_effect is not None:
            raise unlink_side_effect
        unlinked.append(tuple(keys))
        return len(keys)

    client = MagicMock()
    client.scan_iter = MagicMock(side_effect=scan_iter)
    client.unlink = AsyncMock(side_effect=unlink)
    # Defensive: a regression to FLUSHALL would silently call this attribute.
    # Make the call fail loud so tests catch it immediately.
    client.flushall = AsyncMock(
        side_effect=AssertionError("FLUSHALL must never be called -- SPEC-SEC-INTERNAL-001 REQ-2")
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client._unlinked_calls = unlinked
    return client


def _docker_client(restart_raises: dict[str, Exception] | None = None) -> MagicMock:
    raises = restart_raises or {}
    client = MagicMock()

    def _get(name: str) -> MagicMock:
        ctr = MagicMock()
        if name in raises:
            ctr.restart = MagicMock(side_effect=raises[name])
        else:
            ctr.restart = MagicMock(return_value=None)
        ctr._name = name
        return ctr

    client.containers = MagicMock()
    client.containers.get = MagicMock(side_effect=_get)
    return client


@asynccontextmanager
async def _regenerate_setup(
    orgs: list[MagicMock],
    redis_client: MagicMock,
    docker_client: MagicMock,
    base_config_exists: bool = True,
) -> AsyncIterator[MagicMock]:
    """Patches every external dep of regenerate_librechat_configs."""
    request = MagicMock()
    request.state = MagicMock()

    path_exists = MagicMock(return_value=base_config_exists)

    with (
        patch("app.api.internal._require_internal_token", AsyncMock(return_value=None)),
        patch("app.api.internal._audit_internal_call", AsyncMock(return_value=None)),
        patch("app.api.internal.Path.exists", path_exists),
        patch("app.api.internal.Path.mkdir", MagicMock(return_value=None)),
        patch("app.api.internal.Path.write_text", MagicMock(return_value=None)),
        patch(
            "app.services.provisioning.generators._generate_librechat_yaml",
            MagicMock(return_value="version: 1.3.8\n"),
        ),
        patch("app.api.internal.aioredis.Redis", MagicMock(return_value=redis_client)),
        patch("docker.from_env", MagicMock(return_value=docker_client)),
    ):
        yield request


# ---------------------------------------------------------------------------
# AC-2.1 + AC-2.2: SCAN/UNLINK invariant
# ---------------------------------------------------------------------------


class TestRegenerateUsesScanUnlink:
    @pytest.mark.asyncio
    async def test_handler_calls_scan_iter_then_unlink_no_flushall(self):
        """AC-2.2: SCAN + UNLINK on the protocol client; FLUSHALL never invoked."""
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(
            keys_for_pattern={"configs:*": ["configs:librechat-config", "configs:librechat-config:acme"]},
        )
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        # SCAN with the configured pattern (default "configs:*").
        redis_client.scan_iter.assert_called_once()
        scan_kwargs = redis_client.scan_iter.call_args.kwargs
        assert scan_kwargs["match"] == "configs:*"

        # Both keys went through UNLINK in a single batched call.
        assert redis_client._unlinked_calls == [("configs:librechat-config", "configs:librechat-config:acme")]
        redis_client.flushall.assert_not_called()

        assert docker_client.containers.get.call_count == 2
        for call in docker_client.containers.get.call_args_list:
            assert call.args[0].startswith("librechat-"), call.args

        assert sorted(resp.tenants_updated) == ["getklai", "voys"]
        assert resp.errors == []


# ---------------------------------------------------------------------------
# AC-2.3: Targeted invalidation does not destroy unrelated keys
# ---------------------------------------------------------------------------


class TestTargetedInvalidationLeavesUnrelatedKeys:
    @pytest.mark.asyncio
    async def test_only_pattern_matching_keys_are_unlinked(self):
        """AC-2.3: SCAN(match=configs:*) ignores rate-limit / SSO / partner keys.

        The fake Redis only yields the configs:* keys for the configured
        pattern -- the unrelated keys are never returned by SCAN, so UNLINK
        cannot touch them. This pins the contract that the handler depends
        purely on the SCAN match and never blanket-deletes.
        """
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(
            keys_for_pattern={
                "configs:*": ["configs:librechat-config", "configs:librechat-config:acme"],
                # Unrelated keys exist in Redis but are not matched by the SCAN pattern.
                # Listing them here is purely for documentation -- the mock filters by
                # pattern, so they would never be unlinked.
            },
        )
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        # Two configs:* keys unlinked, nothing else.
        unlinked_keys = [k for batch in redis_client._unlinked_calls for k in batch]
        assert sorted(unlinked_keys) == ["configs:librechat-config", "configs:librechat-config:acme"]
        for k in unlinked_keys:
            assert k.startswith("configs:")
        assert resp.errors == []


# ---------------------------------------------------------------------------
# AC-2.4: Partial Redis failure does not break the response contract
# ---------------------------------------------------------------------------


class TestRedisFailureSurfaceAndContinue:
    @pytest.mark.asyncio
    async def test_unlink_failure_surfaced_in_errors_but_does_not_block_restart(self):
        """AC-2.4: RedisError from the invalidation surfaces; restart still runs."""
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(
            keys_for_pattern={"configs:*": ["configs:librechat-config"]},
            unlink_side_effect=RedisError("connection refused"),
        )
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        # AC-2.4: errors list contains the cache-invalidation prefix.
        assert any(e.startswith("redis-cache-invalidation:") for e in resp.errors), resp.errors
        # No legacy `redis-flushall:` prefix anywhere.
        assert not any(e.startswith("redis-flushall:") for e in resp.errors), resp.errors
        # Restart still attempted (REQ-2.5).
        docker_client.containers.get.assert_called_once_with("librechat-getklai")
        # ... and the FLUSHALL trip-wire never fired.
        redis_client.flushall.assert_not_called()


# ---------------------------------------------------------------------------
# Existing per-tenant restart isolation -- preserved through the SCAN/UNLINK refactor
# ---------------------------------------------------------------------------


class TestRestartIsolation:
    @pytest.mark.asyncio
    async def test_per_tenant_restart_error_isolated(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2), _org("acme", 3)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(
            keys_for_pattern={"configs:*": ["configs:librechat-config"]},
        )
        docker_client = _docker_client(
            restart_raises={"librechat-voys": docker.errors.APIError("500 boom")},
        )

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        assert sorted(resp.tenants_updated) == ["acme", "getklai", "voys"]
        assert any(e.startswith("voys:") for e in resp.errors), resp.errors
        assert docker_client.containers.get.call_count == 3
        redis_client.flushall.assert_not_called()


class TestEmptyTenantList:
    @pytest.mark.asyncio
    async def test_empty_tenant_list_skips_invalidation_and_restart(self):
        from app.api import internal as internal_mod

        db = _db_returning_orgs([])
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        async with _regenerate_setup([], redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        redis_client.scan_iter.assert_not_called()
        redis_client.unlink.assert_not_called()
        redis_client.flushall.assert_not_called()
        docker_client.containers.get.assert_not_called()
        assert resp.tenants_updated == []
