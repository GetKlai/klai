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
from fastapi import Response
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


def _docker_client(
    restart_raises: dict[str, Exception] | None = None,
    compose_managed: set[str] | None = None,
) -> MagicMock:
    """Fake docker client.

    ``compose_managed`` names the containers that carry the
    ``com.docker.compose.project`` label, i.e. the ones portal-api must NOT
    take over on recreate.
    """
    raises = restart_raises or {}
    compose = compose_managed or set()

    client = MagicMock()
    # Cache per name so tests can assert on a container AFTER the handler ran
    # without a fresh ``containers.get`` call polluting ``call_args_list``.
    made: dict[str, MagicMock] = {}
    client._containers = made

    def _get(name: str) -> MagicMock:
        if name in made:
            return made[name]
        ctr = MagicMock()
        if name in raises:
            ctr.restart = MagicMock(side_effect=raises[name])
        else:
            ctr.restart = MagicMock(return_value=None)
        labels = {"klai.managed_by": "portal-api-provisioning"}
        if name in compose:
            labels = {"com.docker.compose.project": "klai-core", "com.docker.compose.service": name}
        ctr.attrs = {"Config": {"Labels": labels}}
        ctr._name = name
        made[name] = ctr
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
    generate_yaml: MagicMock | None = None,
    reconcile_env: MagicMock | None = None,
) -> AsyncIterator[tuple[MagicMock, Response]]:
    """Patches every external dep of regenerate_librechat_configs.

    Yields ``(request, response)`` -- ``response`` is a real ``fastapi.Response``
    so tests can assert on ``response.status_code`` exactly as FastAPI would
    set it for a real HTTP call (see finding 3C: fail-loud status on recreate
    errors).

    ``generate_yaml`` lets a test override config-regeneration (Step 1) per
    tenant -- e.g. to simulate one tenant's regen failing -- by passing a
    ``MagicMock(side_effect=...)`` keyed on the ``mcp_servers`` argument.

    ``reconcile_env`` lets a test override the per-tenant env reconciliation
    call (SPEC-TENANT-ENV-RECONCILE-001) -- default is a no-op (``[]``, i.e.
    nothing missing) so existing tests are unaffected by the reconcile step.
    """
    request = MagicMock()
    request.state = MagicMock()
    request.query_params = {}
    response = Response()

    path_exists = MagicMock(return_value=base_config_exists)

    with (
        patch("app.api.internal._require_internal_token", AsyncMock(return_value=None)),
        patch("app.api.internal._audit_internal_call", AsyncMock(return_value=None)),
        patch("app.api.internal.Path.exists", path_exists),
        patch("app.api.internal.Path.mkdir", MagicMock(return_value=None)),
        patch("app.api.internal.Path.write_text", MagicMock(return_value=None)),
        patch(
            "app.services.provisioning.generators._generate_librechat_yaml",
            generate_yaml or MagicMock(return_value="version: 1.3.8\n"),
        ),
        patch(
            "app.services.provisioning.generators.reconcile_librechat_env",
            reconcile_env or MagicMock(return_value=[]),
        ),
        patch("app.api.internal.aioredis.Redis", MagicMock(return_value=redis_client)),
        patch("docker.from_env", MagicMock(return_value=docker_client)),
        # Shared bind sources are intact unless a test says otherwise; the
        # failure path has its own coverage in TestSharedMountPreflight.
        patch(
            "app.api.internal.assert_shared_librechat_mount_sources_intact",
            MagicMock(return_value=None),
        ),
    ):
        yield request, response


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

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

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

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

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

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

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

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert sorted(resp.tenants_updated) == ["acme", "getklai", "voys"]
        assert any(e.startswith("voys:") for e in resp.errors), resp.errors
        assert docker_client.containers.get.call_count == 3
        redis_client.flushall.assert_not_called()
        # Finding 3E: restart-only mode keeps its pre-existing semantics --
        # a per-tenant restart failure is reported but does NOT flip the
        # endpoint to 500 (that fail-loud behaviour is reserved for the
        # destructive recreate_containers=true path).
        assert response.status_code == 200
        assert resp.tenants_skipped == []


class TestRecreateMode:
    @pytest.mark.asyncio
    async def test_recreate_mode_recreates_without_restart(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            request.query_params = {"recreate_containers": "true"}
            with patch(
                "app.services.provisioning.infrastructure._start_librechat_container",
                MagicMock(return_value=None),
            ) as start_librechat:
                resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert sorted(resp.tenants_updated) == ["getklai", "voys"]
        assert resp.errors == []
        assert resp.tenants_skipped == []
        assert response.status_code == 200
        assert start_librechat.call_count == 2
        start_librechat.assert_any_call("getklai", "/opt/klai/librechat/getklai/.env", None, rollback_on_failure=True)
        start_librechat.assert_any_call("voys", "/opt/klai/librechat/voys/.env", None, rollback_on_failure=True)
        # Recreate mode probes each container's labels first so it can leave
        # compose-managed containers alone (see TestComposeManagedOwnership).
        assert docker_client.containers.get.call_count == 2
        redis_client.flushall.assert_not_called()


class TestComposeManagedOwnership:
    """A container declared in deploy/docker-compose.yml has ONE owner: compose.

    Incident 2026-08-14: a fleet-wide ``recreate_containers=true`` replaced the
    compose-managed canary ``librechat-getklai`` with a provisioning-managed
    container of the same name. The container itself kept working, but every
    later ``docker compose up`` failed with ``Conflict. The container name
    "/librechat-getklai" is already in use``, permanently breaking the
    deploy-compose gate for ALL services.

    Contract: recreate mode restarts a compose-managed container (so config
    changes still land) and never force-removes it. The image rollout for
    those containers belongs to deploy-compose.
    """

    @pytest.mark.asyncio
    async def test_recreate_mode_does_not_take_over_compose_managed_container(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client(compose_managed={"librechat-getklai"})

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            request.query_params = {"recreate_containers": "true"}
            with patch(
                "app.services.provisioning.infrastructure._start_librechat_container",
                MagicMock(return_value=None),
            ) as start_librechat:
                resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        # The compose-managed canary was never force-removed/recreated.
        assert start_librechat.call_count == 1
        start_librechat.assert_called_once_with("voys", "/opt/klai/librechat/voys/.env", None, rollback_on_failure=True)
        # ... it was restarted instead, so the regenerated config still applies.
        assert docker_client._containers["librechat-getklai"].restart.called
        assert not docker_client._containers["librechat-voys"].restart.called

        # Visible in the response, not silently degraded: an operator running an
        # image rollout must see that this tenant did NOT get a new image.
        assert resp.compose_managed_skipped == ["getklai"]
        assert sorted(resp.tenants_updated) == ["getklai", "voys"]
        assert resp.errors == []
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_recreate_still_creates_a_container_that_does_not_exist_yet(self):
        """The ownership probe must not break create-from-nothing.

        Recreate mode is the path that repairs a tenant whose container was
        removed. Probing labels first means a ``NotFound`` now happens BEFORE
        the create -- it must not be mistaken for a failure and abort the fleet.
        """
        from app.api import internal as internal_mod

        orgs = [_org("gone", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()
        docker_client.containers.get = MagicMock(side_effect=docker.errors.NotFound("no such container"))

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            request.query_params = {"recreate_containers": "true"}
            with patch(
                "app.services.provisioning.infrastructure._start_librechat_container",
                MagicMock(return_value=None),
            ) as start_librechat:
                resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert start_librechat.call_count == 2
        assert resp.errors == []
        assert resp.tenants_skipped == []
        assert resp.compose_managed_skipped == []
        assert response.status_code == 200


class TestEmptyTenantList:
    @pytest.mark.asyncio
    async def test_empty_tenant_list_skips_invalidation_and_restart(self):
        from app.api import internal as internal_mod

        db = _db_returning_orgs([])
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        async with _regenerate_setup([], redis_client, docker_client) as (request, response):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        redis_client.scan_iter.assert_not_called()
        redis_client.unlink.assert_not_called()
        redis_client.flushall.assert_not_called()
        docker_client.containers.get.assert_not_called()
        assert resp.tenants_updated == []
        assert resp.tenants_skipped == []
        # No tenants at all is not a failure -- must not be conflated with
        # "every tenant failed config regeneration" (finding 3E).
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Finding 3 (adversarial review 2026-08-13): recreate mode must abort the
# fleet loop at the first failure instead of replaying a broken shared
# image/config against every remaining tenant, and must fail loud (500)
# instead of a green 200 that hides a crashlooping fleet from CI.
# ---------------------------------------------------------------------------


class TestRecreateModeAbortsOnFirstFailure:
    @pytest.mark.asyncio
    async def test_first_tenant_failure_aborts_loop_and_skips_the_rest(self):
        """AC (finding 3B/3C): first recreate-or-health-gate failure stops the
        loop. Later tenants are reported as skipped, never attempted. The
        failing tenant's error is first in ``errors`` (config regen and cache
        invalidation both succeed here, so nothing precedes it). Response is
        500.
        """
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2), _org("acme", 3)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            request.query_params = {"recreate_containers": "true"}
            with patch(
                "app.services.provisioning.infrastructure._start_librechat_container",
                MagicMock(side_effect=RuntimeError("boot failed for getklai")),
            ) as start_librechat:
                resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        # Only the first tenant was attempted -- the loop aborted before
        # touching voys or acme.
        assert start_librechat.call_count == 1
        start_librechat.assert_called_once_with(
            "getklai", "/opt/klai/librechat/getklai/.env", None, rollback_on_failure=True
        )

        assert resp.errors, resp.errors
        assert resp.errors[0].startswith("getklai:"), resp.errors
        assert sorted(resp.tenants_skipped) == ["acme", "voys"]
        # getklai still regenerated its config (Step 1 succeeded); the
        # failure was in the destructive recreate step.
        assert sorted(resp.tenants_updated) == ["acme", "getklai", "voys"]
        assert response.status_code == 500


class TestConfigOnlyRegenerationSemantics:
    """Finding 3E: the non-recreate (config-only) path keeps its pre-existing
    accumulate-and-report semantics, EXCEPT when every tenant fails config
    regeneration -- that is always a fail-loud 500 regardless of
    ``recreate_containers``.
    """

    @pytest.mark.asyncio
    async def test_partial_config_regen_failure_stays_200(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1, mcp_servers=["ok"]), _org("voys", 2, mcp_servers=["boom"])]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        def _generate(_base_path, mcp_servers):
            if mcp_servers == ["boom"]:
                raise RuntimeError("bad mcp config")
            return "version: 1.3.8\n"

        generate_yaml = MagicMock(side_effect=_generate)

        async with _regenerate_setup(orgs, redis_client, docker_client, generate_yaml=generate_yaml) as (
            request,
            response,
        ):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert resp.tenants_updated == ["getklai"]
        assert any(e.startswith("voys:") for e in resp.errors), resp.errors
        assert resp.tenants_skipped == []
        # Unchanged pre-existing semantics: partial failure still reports 200.
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_total_config_regen_failure_is_500(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1, mcp_servers=["boom"]), _org("voys", 2, mcp_servers=["boom"])]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        generate_yaml = MagicMock(side_effect=RuntimeError("base template is broken"))

        async with _regenerate_setup(orgs, redis_client, docker_client, generate_yaml=generate_yaml) as (
            request,
            response,
        ):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert resp.tenants_updated == []
        assert len(resp.errors) == 2
        assert resp.tenants_skipped == []
        # Finding 3E exception: every tenant failed config regen -- fail loud
        # even though recreate_containers was never requested.
        assert response.status_code == 500
        # Cache invalidation / restart never ran -- there was nothing to apply.
        redis_client.scan_iter.assert_not_called()
        docker_client.containers.get.assert_not_called()


# ---------------------------------------------------------------------------
# SPEC-TENANT-ENV-RECONCILE-001: per-tenant .env reconciliation wired into
# Step 1 of the fleet regenerate.
# ---------------------------------------------------------------------------


class TestEnvReconciliationWiring:
    @pytest.mark.asyncio
    async def test_env_keys_added_surfaced_in_response(self):
        """A tenant whose .env was missing keys reports them in env_keys_added."""
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        def _reconcile(_env_path, _required):
            # Simulate getklai already having the keys (no-op) and voys missing them.
            return []

        added_for_voys = ["PORTAL_INTERNAL_URL", "PORTAL_INTERNAL_SECRET"]
        reconcile_env = MagicMock(side_effect=[[], added_for_voys])

        async with _regenerate_setup(orgs, redis_client, docker_client, reconcile_env=reconcile_env) as (
            request,
            response,
        ):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert resp.errors == []
        assert response.status_code == 200
        # Only the tenant with actual additions appears; empty-list tenants are omitted.
        assert resp.env_keys_added == {"voys": added_for_voys}

    @pytest.mark.asyncio
    async def test_reconcile_failure_isolated_per_tenant_in_restart_only_mode(self):
        """A single tenant's reconcile failure (e.g. missing .env) does not
        block other tenants and does not flip restart-only mode to 500 --
        mirrors the existing config-regen partial-failure semantics.
        """
        from app.api import internal as internal_mod
        from app.services.provisioning.generators import EnvFileMissingError

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        def _reconcile(env_path, _required):
            if "voys" in str(env_path):
                raise EnvFileMissingError("tenant env file not found")
            return []

        reconcile_env = MagicMock(side_effect=_reconcile)

        async with _regenerate_setup(orgs, redis_client, docker_client, reconcile_env=reconcile_env) as (
            request,
            response,
        ):
            resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        assert resp.tenants_updated == ["getklai"]
        assert any("voys" in e and "tenant env file not found" in e for e in resp.errors), resp.errors
        assert response.status_code == 200
        assert resp.env_keys_added == {}

    @pytest.mark.asyncio
    async def test_reconcile_failure_with_recreate_mode_yields_500(self):
        """A reconcile failure in recreate mode is destructive-path fail-loud
        (finding 3C semantics): the failing tenant is excluded from the
        container-recreate step and the response is 500.
        """
        from app.api import internal as internal_mod
        from app.services.provisioning.generators import EnvFileMissingError

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        def _reconcile(env_path, _required):
            if "voys" in str(env_path):
                raise EnvFileMissingError("tenant env file not found")
            return []

        reconcile_env = MagicMock(side_effect=_reconcile)

        async with _regenerate_setup(orgs, redis_client, docker_client, reconcile_env=reconcile_env) as (
            request,
            response,
        ):
            request.query_params = {"recreate_containers": "true"}
            with patch(
                "app.services.provisioning.infrastructure._start_librechat_container",
                MagicMock(return_value=None),
            ) as start_librechat:
                resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        # voys never reached the recreate step -- only getklai (the tenant
        # whose config+env reconciliation both succeeded) was recreated.
        start_librechat.assert_called_once_with(
            "getklai", "/opt/klai/librechat/getklai/.env", None, rollback_on_failure=True
        )
        assert any("voys" in e for e in resp.errors), resp.errors
        assert response.status_code == 500


class TestSharedMountPreflight:
    """A broken fleet-shared bind source blocks the whole apply step.

    Incident 2026-08-14: the containers that went down were RESTARTED, not
    recreated -- so the create-path guard never ran. Restarting a healthy
    container against a deleted mount source destroys a working process, so
    the only correct move is to refuse and say why.

    This is fleet-wide, not per-tenant: every tenant mounts the same patch
    files. It therefore fails loud (500) in BOTH modes, unlike a per-tenant
    restart failure which restart-only mode reports at 200 by design.
    """

    @staticmethod
    def _broken_preflight():
        return patch(
            "app.api.internal.assert_shared_librechat_mount_sources_intact",
            MagicMock(
                side_effect=RuntimeError(
                    "LibreChat shared mount sources are not usable: /librechat/patches/stream.cjs [directory ...]"
                )
            ),
        )

    @pytest.mark.asyncio
    async def test_restart_only_refuses_and_fails_loud(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            with self._broken_preflight():
                resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        # No healthy container was touched.
        docker_client.containers.get.assert_not_called()
        # Loud, with the offending path in the message.
        assert response.status_code == 500
        assert any("stream.cjs" in e for e in resp.errors)
        assert sorted(resp.tenants_skipped) == ["getklai", "voys"]

    @pytest.mark.asyncio
    async def test_recreate_refuses_before_removing_anything(self):
        from app.api import internal as internal_mod

        orgs = [_org("getklai", 1), _org("voys", 2)]
        db = _db_returning_orgs(orgs)
        redis_client = _redis_mock(keys_for_pattern={"configs:*": ["configs:librechat-config"]})
        docker_client = _docker_client()

        async with _regenerate_setup(orgs, redis_client, docker_client) as (request, response):
            request.query_params = {"recreate_containers": "true"}
            with self._broken_preflight():
                with patch(
                    "app.services.provisioning.infrastructure._start_librechat_container",
                    MagicMock(return_value=None),
                ) as start_librechat:
                    resp = await internal_mod.regenerate_librechat_configs(request=request, response=response, db=db)

        # Recreate force-removes before it creates. Nothing may be removed when
        # we already know the replacement cannot boot.
        start_librechat.assert_not_called()
        assert response.status_code == 500
        assert sorted(resp.tenants_skipped) == ["getklai", "voys"]
