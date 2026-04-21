"""Tests for the /internal/librechat/regenerate endpoint.

Focus on the Redis-FLUSHALL-via-protocol path introduced after SEC-021 closed
off docker-socket-proxy's /exec/*/start endpoint. Two invariants to pin:

1. FLUSHALL goes through the Redis protocol client, NOT docker exec. Any
   regression back to `redis_ctr.exec_run([...])` would fail in production
   (403 from docker-socket-proxy) — catch it here.
2. A Redis failure is surfaced as an entry in the response `errors` list
   (librechat.yaml in Redis has no TTL, so a silent swallow leaves every
   tenant reading stale config forever).
3. Per-slug container restart failures do not cancel other slugs.
"""

from __future__ import annotations

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


def _redis_mock(flushall_side_effect=None) -> MagicMock:
    """Fake aioredis.Redis() instance supporting async-context-manager + flushall()."""
    client = MagicMock()
    client.flushall = AsyncMock(side_effect=flushall_side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _docker_client(restart_raises: dict[str, Exception] | None = None) -> MagicMock:
    """Fake docker client. `restart_raises` maps container_name → exception to raise on restart()."""
    raises = restart_raises or {}
    client = MagicMock()

    def _get(name: str):
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
):
    """Patches every external dep of regenerate_librechat_configs.

    Yields the request mock so tests can make assertions afterwards if needed.
    """
    request = MagicMock()
    request.state = MagicMock()

    # Base yaml path existence
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
        # `docker` is imported lazily inside regenerate_librechat_configs, so we
        # patch the top-level `docker.from_env` attribute directly.
        patch("docker.from_env", MagicMock(return_value=docker_client)),
    ):
        yield request


class TestRegenerateRedisFlushPath:
    @pytest.mark.asyncio
    async def test_successful_regenerate_uses_redis_protocol_client(self):
        """Happy path: FLUSHALL is called on the Redis protocol client, not via docker exec."""
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock()
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        # FLUSHALL on the Redis protocol client exactly once — never via docker exec.
        redis_client.flushall.assert_awaited_once()
        assert docker_client.containers.get.call_count == 2  # only the two restarts
        for call in docker_client.containers.get.call_args_list:
            assert call.args[0].startswith("librechat-"), call.args

        assert sorted(resp.tenants_updated) == ["getklai", "voys"]
        assert resp.errors == []

    @pytest.mark.asyncio
    async def test_redis_failure_surfaced_in_errors_but_does_not_block_restart(self):
        """Redis outage must be visible to the caller (CI / operator) because
        librechat.yaml has no TTL in Redis — silent failure = permanent stale config.
        """
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(flushall_side_effect=RedisError("connection refused"))
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        assert any(e.startswith("redis-flushall:") for e in resp.errors), resp.errors
        # Restart still attempted — FLUSHALL failure should not cancel restarts.
        docker_client.containers.get.assert_called_once_with("librechat-getklai")

    @pytest.mark.asyncio
    async def test_per_tenant_restart_error_isolated(self):
        """If one tenant's container restart fails, other tenants still restart."""
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2), _org("acme", 3)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock()
        docker_client = _docker_client(
            restart_raises={"librechat-voys": docker.errors.APIError("500 boom")},
        )

        async with _regenerate_setup(orgs, redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        assert sorted(resp.tenants_updated) == ["acme", "getklai", "voys"]
        assert any(e.startswith("voys:") for e in resp.errors), resp.errors
        # Other two containers still got restarted.
        assert docker_client.containers.get.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_tenant_list_skips_flush_and_restart(self):
        """No ready tenants → no Redis/Docker work."""
        from app.api import internal as internal_mod

        db = _db_returning_orgs([])
        redis_client = _redis_mock()
        docker_client = _docker_client()

        async with _regenerate_setup([], redis_client, docker_client) as request:
            resp = await internal_mod.regenerate_librechat_configs(request=request, db=db)

        redis_client.flushall.assert_not_called()
        docker_client.containers.get.assert_not_called()
        assert resp.tenants_updated == []
