"""
Characterization tests for provisioning infrastructure functions.

Tests Docker, MongoDB, Caddy, and Redis utility functions with mocked
external dependencies.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest
from pymongo.errors import OperationFailure
from redis.exceptions import RedisError


@pytest.fixture(autouse=True)
def _mock_settings():
    """Provide deterministic settings for all tests."""
    import app.services.provisioning.infrastructure  # noqa: F401  # pyright: ignore[reportUnusedImport]

    with patch("app.services.provisioning.infrastructure.settings") as mock:
        mock.domain = "getklai.com"
        mock.mongo_root_username = "root"
        mock.mongo_root_password = "test-mongo-pw"
        mock.caddy_tenants_path = "/tmp/test-caddy-tenants"  # noqa: S108
        mock.caddy_container_name = "klai-core-caddy-1"
        mock.redis_container_name = "redis"
        mock.redis_host = "redis"
        mock.redis_port = 6379
        mock.redis_password = "test-redis-pw"
        mock.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
        mock.librechat_host_data_path = "/opt/klai/librechat-data"
        mock.librechat_container_data_path = "/tmp/test-librechat-data"  # noqa: S108
        mock.mongodb_container_name = "mongodb"
        # SPEC-SEC-INTERNAL-001 REQ-2.3: configurable cache key pattern.
        mock.librechat_cache_key_pattern = "configs:*"
        yield mock


class TestCharacterizeSyncRemoveContainer:
    """Characterization tests for _sync_remove_container."""

    def test_removes_existing_container(self):
        from app.services.provisioning import _sync_remove_container

        mock_container = MagicMock()
        with patch("app.services.provisioning.infrastructure.docker") as mock_docker:
            mock_docker.from_env.return_value.containers.get.return_value = mock_container
            _sync_remove_container("librechat-acme")
            mock_container.remove.assert_called_once_with(force=True)

    def test_handles_not_found_gracefully(self):
        from app.services.provisioning import _sync_remove_container

        with patch("app.services.provisioning.infrastructure.docker") as mock_docker:
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_docker.from_env.return_value.containers.get.side_effect = mock_docker.errors.NotFound("not found")
            # Should not raise
            _sync_remove_container("nonexistent")


def _mock_mongo_client():
    """Factory: pymongo.MongoClient replacement supporting `with _mongo_admin_client() as c`.

    Returned tuple is (context-manager-factory, underlying_client) so tests can
    assert on `client[db_name].command(...)` calls.
    """
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.close = MagicMock()
    # client[db_name] returns a db mock with a command() method
    db = MagicMock()
    client.__getitem__ = MagicMock(return_value=db)
    return MagicMock(return_value=client), client, db


class TestCharacterizeSyncDropMongodbTenantUser:
    """Dropping a tenant MongoDB user via the pymongo protocol, not docker exec."""

    def test_issues_dropuser_command_against_correct_db(self):
        from app.services.provisioning import _sync_drop_mongodb_tenant_user

        factory, client, db = _mock_mongo_client()
        with patch("app.services.provisioning.infrastructure._mongo_admin_client", factory):
            _sync_drop_mongodb_tenant_user("acme")

        client.__getitem__.assert_called_once_with("librechat-acme")
        db.command.assert_called_once_with("dropUser", "librechat-acme")

    def test_idempotent_on_user_not_found(self):
        """dropUser on a missing user is not an error — offboarding must be re-runnable."""
        from app.services.provisioning import _sync_drop_mongodb_tenant_user

        factory, _client, db = _mock_mongo_client()
        db.command.side_effect = OperationFailure(
            "User not found",
            code=11,  # _MONGO_USER_NOT_FOUND
            details={"codeName": "UserNotFound"},
        )
        with patch("app.services.provisioning.infrastructure._mongo_admin_client", factory):
            # Should NOT raise
            _sync_drop_mongodb_tenant_user("acme")

    def test_propagates_other_operation_failures(self):
        from app.services.provisioning import _sync_drop_mongodb_tenant_user

        factory, _client, db = _mock_mongo_client()
        db.command.side_effect = OperationFailure("Auth failed", code=18, details={})
        with (
            patch("app.services.provisioning.infrastructure._mongo_admin_client", factory),
            pytest.raises(OperationFailure),
        ):
            _sync_drop_mongodb_tenant_user("acme")


class TestCharacterizeCreateMongodbTenantUser:
    """Creating a tenant MongoDB user via the pymongo protocol, not docker exec."""

    def test_creates_user_with_readwrite_role_on_tenant_db(self):
        from app.services.provisioning import _create_mongodb_tenant_user

        factory, client, db = _mock_mongo_client()
        with patch("app.services.provisioning.infrastructure._mongo_admin_client", factory):
            _create_mongodb_tenant_user("acme", "secret-pw")

        client.__getitem__.assert_called_once_with("librechat-acme")
        db.command.assert_called_once_with(
            "createUser",
            "librechat-acme",
            pwd="secret-pw",
            roles=[{"role": "readWrite", "db": "librechat-acme"}],
        )

    def test_raises_runtime_error_on_operation_failure(self):
        from app.services.provisioning import _create_mongodb_tenant_user

        factory, _client, db = _mock_mongo_client()
        db.command.side_effect = OperationFailure(
            "User already exists",
            code=51003,
            details={"codeName": "Location51003"},
        )
        with (
            patch("app.services.provisioning.infrastructure._mongo_admin_client", factory),
            pytest.raises(RuntimeError, match="MongoDB tenant user creation failed"),
        ):
            _create_mongodb_tenant_user("acme", "secret-pw")

    def test_never_calls_docker_exec(self):
        """Regression guard: MongoDB ops MUST NOT go through docker-socket-proxy
        (SEC-021 denies /exec/*/start). If anyone reintroduces `container.exec_run`,
        this test fails because the docker patch is never engaged.
        """
        from app.services.provisioning import _create_mongodb_tenant_user

        factory, _client, _db = _mock_mongo_client()
        with (
            patch("app.services.provisioning.infrastructure._mongo_admin_client", factory),
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
        ):
            _create_mongodb_tenant_user("acme", "secret-pw")

        mock_docker.from_env.assert_not_called()


class TestCharacterizeWriteTenantCaddyfile:
    """Characterization tests for _write_tenant_caddyfile."""

    def test_writes_caddyfile_with_correct_content(self, tmp_path):
        from app.services.provisioning import _write_tenant_caddyfile

        with patch("app.services.provisioning.infrastructure.settings") as mock_settings:
            mock_settings.domain = "getklai.com"
            mock_settings.caddy_tenants_path = str(tmp_path)
            _write_tenant_caddyfile("acme")

        caddyfile = tmp_path / "acme.caddyfile"
        assert caddyfile.exists()
        content = caddyfile.read_text()
        assert "chat-acme.getklai.com" in content
        assert "reverse_proxy librechat-acme:3080" in content
        assert "Strict-Transport-Security" in content
        assert "@chat_generation" in content
        assert "method POST" in content
        assert "path /api/agents/chat/* /api/ask/*" in content
        assert "rate_limit @chat_generation" in content

    def test_creates_directory_if_needed(self, tmp_path):
        from app.services.provisioning import _write_tenant_caddyfile

        target = tmp_path / "subdir" / "tenants"
        with patch("app.services.provisioning.infrastructure.settings") as mock_settings:
            mock_settings.domain = "getklai.com"
            mock_settings.caddy_tenants_path = str(target)
            _write_tenant_caddyfile("test")

        assert (target / "test.caddyfile").exists()


class TestCharacterizeReloadCaddy:
    """Characterization tests for _reload_caddy.

    Current implementation (since ``admin off`` disables Caddy's Admin API)
    restarts the container rather than calling ``caddy reload``. A ~1s TLS
    interruption is acceptable at current scale.
    """

    def test_restarts_caddy_container(self):
        from app.services.provisioning import _reload_caddy

        mock_caddy = MagicMock()
        with patch("app.services.provisioning.infrastructure.docker") as mock_docker:
            mock_docker.from_env.return_value.containers.get.return_value = mock_caddy
            _reload_caddy()

            mock_docker.from_env.assert_called_once()
            mock_docker.from_env.return_value.containers.get.assert_called_once_with("klai-core-caddy-1")
            mock_caddy.restart.assert_called_once_with(timeout=10)

    def test_propagates_when_container_not_found(self):
        """If Caddy isn't running, docker.NotFound propagates to the caller."""
        from app.services.provisioning import _reload_caddy

        with patch("app.services.provisioning.infrastructure.docker") as mock_docker:
            mock_docker.errors = docker.errors
            mock_docker.from_env.return_value.containers.get.side_effect = docker.errors.NotFound(
                "No such container: klai-core-caddy-1"
            )
            with pytest.raises(docker.errors.NotFound):
                _reload_caddy()

    def test_propagates_when_restart_fails(self):
        """Docker APIError on restart() propagates (no silent swallow)."""
        from app.services.provisioning import _reload_caddy

        mock_caddy = MagicMock()
        mock_caddy.restart.side_effect = docker.errors.APIError("restart failed")
        with patch("app.services.provisioning.infrastructure.docker") as mock_docker:
            mock_docker.errors = docker.errors
            mock_docker.from_env.return_value.containers.get.return_value = mock_caddy
            with pytest.raises(docker.errors.APIError):
                _reload_caddy()


def _mock_redis_sync_client(*, keys: list[str] | None = None, scan_raises: Exception | None = None):
    """Factory: redis.Redis replacement supporting ``with _redis_sync_client() as c``.

    Implements ``scan_iter`` and ``unlink`` so the tests can pin the
    SPEC-SEC-INTERNAL-001 REQ-2 SCAN/UNLINK behaviour. ``keys`` are returned
    by ``scan_iter`` regardless of the requested match pattern (the test
    fixes the pattern). ``flushall`` is also stubbed and asserted-not-called
    by tests as a regression-guard against falling back to FLUSHALL.
    """
    yielded = list(keys or [])
    unlinked: list[tuple[str, ...]] = []

    def _scan_iter(match: str, count: int = 100):
        if scan_raises is not None:
            raise scan_raises
        return iter(yielded)

    def _unlink(*ks: str) -> int:
        unlinked.append(tuple(ks))
        return len(ks)

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.scan_iter = MagicMock(side_effect=_scan_iter)
    client.unlink = MagicMock(side_effect=_unlink)
    client.flushall = MagicMock(
        side_effect=AssertionError(
            "FLUSHALL must never be called -- SPEC-SEC-INTERNAL-001 REQ-2",
        )
    )
    client.close = MagicMock()
    client._unlinked = unlinked
    return MagicMock(return_value=client), client


class TestCharacterizeFlushRedisAndRestartLibrechat:
    """_flush_redis_and_restart_librechat: Redis protocol + container restart.

    SPEC-SEC-INTERNAL-001 REQ-2 + AC-2: invalidation goes through SCAN+UNLINK
    on the configured key pattern (``configs:*``), NOT FLUSHALL. Pinned so we
    never regress back to a blanket cache wipe (which would clobber unrelated
    keys -- rate-limit buckets, SSO cache, partner-API state) and never to
    ``redis_container.exec_run([FLUSHALL])`` (which 403s under
    docker-socket-proxy, SEC-021).
    """

    def test_invalidates_via_scan_unlink_then_restarts_container(self):
        from app.services.provisioning import _flush_redis_and_restart_librechat

        redis_factory, redis_client = _mock_redis_sync_client(
            keys=["configs:librechat-config", "configs:librechat-config:acme"],
        )
        container = MagicMock()
        container.status = "running"

        with (
            patch("app.services.provisioning.infrastructure._redis_sync_client", redis_factory),
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
        ):
            mock_docker.from_env.return_value.containers.get.return_value = container
            _flush_redis_and_restart_librechat("acme")

        # SCAN with the configured pattern, batched UNLINK call, no FLUSHALL.
        redis_client.scan_iter.assert_called_once()
        scan_kwargs = redis_client.scan_iter.call_args.kwargs
        assert scan_kwargs["match"] == "configs:*"
        assert redis_client._unlinked == [("configs:librechat-config", "configs:librechat-config:acme")]
        redis_client.flushall.assert_not_called()

        mock_docker.from_env.return_value.containers.get.assert_called_once_with("librechat-acme")
        container.restart.assert_called_once_with(timeout=10)

    def test_redis_failure_raises_without_touching_container(self):
        """Fail-loud: a failed cache invalidation means LibreChat keeps serving
        the stale yaml while the operator thinks the change landed. Previously
        this was a warning-and-continue. Now the helper raises so the caller
        (provisioning orchestrator / mcp_servers restart task) sees the
        failure and can surface it.
        """
        from app.services.provisioning import _flush_redis_and_restart_librechat

        redis_factory, redis_client = _mock_redis_sync_client(
            scan_raises=RedisError("connection refused"),
        )

        container = MagicMock()
        container.status = "running"

        with (
            patch("app.services.provisioning.infrastructure._redis_sync_client", redis_factory),
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            pytest.raises(RedisError),
        ):
            mock_docker.from_env.return_value.containers.get.return_value = container
            _flush_redis_and_restart_librechat("acme")

        # Container restart must NOT run when invalidation failed -- we don't
        # want to bounce the tenant's LibreChat on a failed config update.
        container.restart.assert_not_called()
        redis_client.flushall.assert_not_called()

    def test_container_health_check_timeout_raises(self):
        """If the container doesn't reach 'running' state within the grace
        window, the helper raises. Previously this was a silent warning and
        provisioning returned success with a broken tenant.
        """
        import app.services.provisioning.infrastructure as infra_mod
        from app.services.provisioning import _flush_redis_and_restart_librechat

        redis_factory, _ = _mock_redis_sync_client()

        container = MagicMock()
        container.status = "restarting"  # never flips to 'running'

        with (
            patch("app.services.provisioning.infrastructure._redis_sync_client", redis_factory),
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            # Short-circuit the 30s deadline so the test runs in ms.
            patch.object(infra_mod.time, "monotonic", side_effect=[0.0, 31.0]),
            patch.object(infra_mod.time, "sleep"),
            pytest.raises(RuntimeError, match="did not reach running state"),
        ):
            mock_docker.from_env.return_value.containers.get.return_value = container
            _flush_redis_and_restart_librechat("acme")

    def test_never_calls_container_exec_run(self):
        """Regression guard against the SEC-021 bug (exec/*/start forbidden)."""
        from app.services.provisioning import _flush_redis_and_restart_librechat

        redis_factory, _redis_client = _mock_redis_sync_client()
        container = MagicMock()
        container.status = "running"

        with (
            patch("app.services.provisioning.infrastructure._redis_sync_client", redis_factory),
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
        ):
            mock_docker.from_env.return_value.containers.get.return_value = container
            _flush_redis_and_restart_librechat("acme")

        # If anyone reintroduces exec_run(), this assertion fails.
        assert not container.exec_run.called


class TestCharacterizeStartLibrechatContainer:
    """Characterization tests for _start_librechat_container."""

    @pytest.fixture(autouse=True)
    def _mock_openid_ready(self):
        with patch("app.services.provisioning.infrastructure._wait_for_librechat_openid_ready") as mock:
            yield mock

    def _write_lc_files(self, tmp_path: Path, slug: str = "acme") -> None:
        (tmp_path / "librechat.yaml").write_text("version: 1.0\n")
        tenant_dir = tmp_path / slug
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / ".env").write_text("MONGO_URI=mongodb://example\nALLOW_IFRAME=true\nJWT_SECRET=keep-me\n")
        patch_dir = tmp_path / "patches"
        patch_dir.mkdir(exist_ok=True)
        for name in ("format.cjs", "share.js", "stream.cjs", "search.cjs"):
            (patch_dir / name).write_text("// patch\n")
        # Klai light-theme entrypoint wrapper — fail-loud mounted, must exist.
        (tmp_path / "klai-entrypoint.sh").write_text('#!/bin/sh\nexec docker-entrypoint.sh "$@"\n')

    def test_starts_container_with_correct_config(self, tmp_path):
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = type("NotFound", (Exception,), {})("not found")

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

            mock_client.containers.create.assert_called_once()
            call_kwargs = mock_client.containers.create.call_args
            assert call_kwargs[1]["name"] == "librechat-acme"
            assert call_kwargs[1]["network"] == "klai-net"
            # OIDC boot-race fix (2026-05-22): create → connect all networks
            # while stopped → start, so the container boots ONCE with stable
            # networking and LibreChat's OpenID discovery succeeds. No `detach`
            # kwarg on create; no post-start restart (that earlier landed inside
            # the network-reconfig window and broke the working first boot).
            mock_client.containers.create.return_value.start.assert_called_once()

    def test_passes_tenant_env_as_process_environment(self, tmp_path):
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        mock_client = MagicMock()
        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

        environment = mock_client.containers.create.call_args[1]["environment"]
        assert environment["MONGO_URI"] == "mongodb://example"
        assert environment["JWT_SECRET"] == "keep-me"
        assert environment["ALLOW_SHARED_LINKS"] == "true"
        assert environment["ALLOW_SHARED_LINKS_PUBLIC"] == "true"
        env_file_content = (tmp_path / "acme" / ".env").read_text()
        assert "ALLOW_SHARED_LINKS=true" in env_file_content
        assert "ALLOW_SHARED_LINKS_PUBLIC=true" in env_file_content

    def test_mounts_live_librechat_patches(self, tmp_path):
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        mock_client = MagicMock()
        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

        volumes = mock_client.containers.create.call_args[1]["volumes"]
        assert volumes["/opt/klai/librechat-data/patches/format.cjs"] == {
            "bind": "/app/node_modules/@librechat/agents/dist/cjs/messages/format.cjs",
            "mode": "ro",
        }
        assert volumes["/opt/klai/librechat-data/patches/share.js"] == {
            "bind": "/app/api/server/routes/share.js",
            "mode": "ro",
        }
        assert volumes["/opt/klai/librechat-data/patches/stream.cjs"] == {
            "bind": "/app/node_modules/@librechat/agents/dist/cjs/stream.cjs",
            "mode": "ro",
        }
        assert volumes["/opt/klai/librechat-data/patches/search.cjs"] == {
            "bind": "/app/node_modules/@librechat/agents/dist/cjs/tools/search/search.cjs",
            "mode": "ro",
        }
        assert "/opt/klai/librechat-data/patches/createStreamServices.ts" not in volumes

    def test_forces_light_theme_via_entrypoint_wrapper(self, tmp_path):
        """Every provisioned tenant must boot through the Klai entrypoint
        wrapper that forces light theme. `entrypoint` clears the image CMD, so
        `command` MUST be passed through explicitly, and the wrapper script MUST
        be mounted read-only at /klai-entrypoint.sh.
        """
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        mock_client = MagicMock()
        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

        call_kwargs = mock_client.containers.create.call_args[1]
        assert call_kwargs["entrypoint"] == ["/bin/sh", "/klai-entrypoint.sh"]
        assert call_kwargs["command"] == ["npm", "run", "backend"]
        volumes = call_kwargs["volumes"]
        assert volumes["/opt/klai/librechat-data/klai-entrypoint.sh"] == {
            "bind": "/klai-entrypoint.sh",
            "mode": "ro",
        }

    def test_provisioning_labels_are_set(self, tmp_path):
        """SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2a: tenant-LibreChats MUST
        carry klai.managed_by, klai.tenant_slug, and klai.kind labels so
        hygiene-tooling (PreToolUse hook + weekly orphan-audit) recognises
        them as legitimate klasse-B containers, not as label-loose wezen.

        The librechat-voys cleanup-incident of 2026-05-02 happened because
        these labels did not exist. Without this test passing, the same
        class of mistake can recur for any future tenant.
        """
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path, slug="voys")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = type("NotFound", (Exception,), {})("not found")

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            _start_librechat_container("voys", "/opt/klai/librechat-data/voys/.env")

            call_kwargs = mock_client.containers.create.call_args
            labels = call_kwargs[1]["labels"]
            assert labels["klai.managed_by"] == "portal-api-provisioning"
            assert labels["klai.tenant_slug"] == "voys"
            assert labels["klai.kind"] == "librechat"

    def test_provisioning_labels_use_actual_slug(self, tmp_path):
        """klai.tenant_slug MUST reflect the tenant slug passed in, not a
        hard-coded value. Backfill scripts depend on this for tenant lookup.
        """
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path, slug="voys")
        self._write_lc_files(tmp_path, slug="acme-corp")
        self._write_lc_files(tmp_path, slug="klai-internal")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = type("NotFound", (Exception,), {})("not found")

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.5-rc1"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            for tenant_slug in ("voys", "acme-corp", "klai-internal"):
                mock_client.containers.create.reset_mock()
                _start_librechat_container(tenant_slug, f"/opt/klai/librechat-data/{tenant_slug}/.env")
                labels = mock_client.containers.create.call_args[1]["labels"]
                assert labels["klai.tenant_slug"] == tenant_slug


class TestStartLibrechatContainerRollback:
    """Finding 3A (adversarial review 2026-08-13): ``rollback_on_failure``
    records the running container's image before it is force-removed, and
    attempts a best-effort restore to that image if the replacement fails to
    create/boot/pass its OpenID readiness gate. Used only by the fleet
    regenerate endpoint's recreate path; other callers (initial provisioning,
    MCP-config-apply) default the flag off and keep their pre-existing
    behaviour.
    """

    def _write_lc_files(self, tmp_path: Path, slug: str = "acme") -> None:
        (tmp_path / "librechat.yaml").write_text("version: 1.0\n")
        tenant_dir = tmp_path / slug
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / ".env").write_text("MONGO_URI=mongodb://example\nALLOW_IFRAME=true\nJWT_SECRET=keep-me\n")
        patch_dir = tmp_path / "patches"
        patch_dir.mkdir(exist_ok=True)
        for name in ("format.cjs", "share.js", "stream.cjs", "search.cjs"):
            (patch_dir / name).write_text("// patch\n")
        (tmp_path / "klai-entrypoint.sh").write_text('#!/bin/sh\nexec docker-entrypoint.sh "$@"\n')

    def test_health_gate_failure_triggers_rollback_to_old_image(self, tmp_path):
        """The replacement fails its readiness gate; the restore recreates
        with the OLD image tag captured before removal, and succeeds."""
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        old_container = MagicMock()
        old_container.attrs = {"Config": {"Image": "ghcr.io/danny-avila/librechat:v0.8.6"}}
        # Reading .image would call GET /images/{id}/json, which
        # docker-socket-proxy denies (403). Make any access explode so this
        # test fails if the lazy property is reintroduced.
        type(old_container).image = property(
            lambda _self: (_ for _ in ()).throw(
                AssertionError("must not touch container.image: socket-proxy denies IMAGES")
            )
        )
        broken_container = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = [old_container, broken_container]
        mock_client.containers.create.return_value = MagicMock()

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
            patch(
                "app.services.provisioning.infrastructure._wait_for_librechat_openid_ready",
                side_effect=[RuntimeError("boot failed"), None],
            ) as mock_ready,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.7"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})

            # The original failure still propagates -- rollback is best
            # effort, it never swallows the error that triggered it.
            with pytest.raises(RuntimeError, match=r"^boot failed$"):
                _start_librechat_container(
                    "acme",
                    "/opt/klai/librechat-data/acme/.env",
                    rollback_on_failure=True,
                )

        # Old container recorded + force-removed; the broken replacement is
        # also force-removed before the rollback recreate attempt.
        old_container.remove.assert_called_once_with(force=True)
        broken_container.remove.assert_called_once_with(force=True)

        # create() called twice: once with the new image (failed), once with
        # the recorded old image (the rollback).
        assert mock_client.containers.create.call_count == 2
        first_image = mock_client.containers.create.call_args_list[0].kwargs["image"]
        second_image = mock_client.containers.create.call_args_list[1].kwargs["image"]
        assert first_image == "ghcr.io/danny-avila/librechat:v0.8.7"
        assert second_image == "ghcr.io/danny-avila/librechat:v0.8.6"
        assert mock_ready.call_count == 2

    def test_rollback_failure_does_not_mask_original_error(self, tmp_path):
        """Both the initial recreate AND the rollback attempt fail. The
        exception that propagates out of _start_librechat_container is the
        ORIGINAL failure, not the rollback's own failure -- and the restore
        failure is logged, not silently dropped."""
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        old_container = MagicMock()
        old_container.attrs = {"Config": {"Image": "ghcr.io/danny-avila/librechat:v0.8.6"}}
        # Reading .image would call GET /images/{id}/json, which
        # docker-socket-proxy denies (403). Make any access explode so this
        # test fails if the lazy property is reintroduced.
        type(old_container).image = property(
            lambda _self: (_ for _ in ()).throw(
                AssertionError("must not touch container.image: socket-proxy denies IMAGES")
            )
        )
        broken_container = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = [old_container, broken_container]
        mock_client.containers.create.return_value = MagicMock()

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
            patch(
                "app.services.provisioning.infrastructure._wait_for_librechat_openid_ready",
                side_effect=[RuntimeError("boot failed"), RuntimeError("rollback also failed")],
            ),
            patch("app.services.provisioning.infrastructure.logger") as mock_logger,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.7"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})

            with pytest.raises(RuntimeError, match=r"^boot failed$"):
                _start_librechat_container(
                    "acme",
                    "/opt/klai/librechat-data/acme/.env",
                    rollback_on_failure=True,
                )

        assert mock_client.containers.create.call_count == 2
        error_calls = [
            call
            for call in mock_logger.exception.call_args_list
            if call.args and call.args[0] == "librechat_container_restore_failed"
        ]
        assert error_calls, mock_logger.exception.call_args_list

    def test_no_rollback_when_flag_is_off(self, tmp_path):
        """Default callers (initial provisioning, MCP-config-apply) keep the
        pre-existing behaviour: no old-image capture, no restore attempt."""
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)

        old_container = MagicMock()
        old_container.attrs = {"Config": {"Image": "ghcr.io/danny-avila/librechat:v0.8.6"}}
        # Reading .image would call GET /images/{id}/json, which
        # docker-socket-proxy denies (403). Make any access explode so this
        # test fails if the lazy property is reintroduced.
        type(old_container).image = property(
            lambda _self: (_ for _ in ()).throw(
                AssertionError("must not touch container.image: socket-proxy denies IMAGES")
            )
        )

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = [old_container]
        mock_client.containers.create.return_value = MagicMock()

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
            patch(
                "app.services.provisioning.infrastructure._wait_for_librechat_openid_ready",
                side_effect=RuntimeError("boot failed"),
            ),
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.7"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})

            with pytest.raises(RuntimeError, match="boot failed"):
                _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

        # No rollback attempt -- create() called exactly once (the failed
        # attempt), and containers.get() only the one time to find/remove
        # the pre-existing container.
        assert mock_client.containers.create.call_count == 1
        assert mock_client.containers.get.call_count == 1


class TestCharacterizeLibrechatOpenidReadiness:
    """Characterization tests for the post-start OpenID readiness gate."""

    def test_ready_when_openid_returns_redirect(self):
        from app.services.provisioning.infrastructure import _wait_for_librechat_openid_ready

        mock_client = MagicMock()
        with patch("app.services.provisioning.infrastructure._probe_librechat_openid", return_value=(302, "")):
            _wait_for_librechat_openid_ready(mock_client, "librechat-acme")

        mock_client.containers.get.assert_not_called()

    def test_restarts_once_after_openid_server_error_then_succeeds(self):
        from app.services.provisioning.infrastructure import _wait_for_librechat_openid_ready

        mock_client = MagicMock()
        with patch(
            "app.services.provisioning.infrastructure._probe_librechat_openid",
            side_effect=[(500, "An unknown error occurred."), (302, "")],
        ):
            _wait_for_librechat_openid_ready(mock_client, "librechat-acme")

        mock_client.containers.get.assert_called_once_with("librechat-acme")
        mock_client.containers.get.return_value.restart.assert_called_once_with(timeout=10)

    def test_raises_after_openid_never_becomes_ready(self):
        from app.services.provisioning.infrastructure import _wait_for_librechat_openid_ready

        mock_client = MagicMock()
        with patch(
            "app.services.provisioning.infrastructure._probe_librechat_openid",
            return_value=(500, "An unknown error occurred."),
        ):
            with pytest.raises(RuntimeError, match="LibreChat OpenID did not become ready"):
                _wait_for_librechat_openid_ready(mock_client, "librechat-acme")

        assert mock_client.containers.get.call_count == 2
        assert mock_client.containers.get.return_value.restart.call_count == 2


class TestPatchMountSourcesMustBeFiles:
    """A bind-mount source that is a DIRECTORY is the shape of the outage.

    Incident 2026-08-14: the config sync deleted a patch file from the host
    while 42 containers still declared it as a bind mount. Docker resolves a
    bind mount at container *start*; a missing source is silently replaced by
    an empty directory. LibreChat then booted against a directory where it
    expected a file and 36 of 42 tenants exited 127.

    The pre-existing guard used ``Path.exists()``, which is True for that
    auto-created directory -- it waved the broken state straight through.
    Only ``is_file()`` distinguishes them.
    """

    @pytest.fixture(autouse=True)
    def _mock_openid_ready(self):
        with patch("app.services.provisioning.infrastructure._wait_for_librechat_openid_ready") as mock:
            yield mock

    def _write_lc_files(self, tmp_path: Path, slug: str = "acme") -> None:
        (tmp_path / "librechat.yaml").write_text("version: 1.0\n")
        tenant_dir = tmp_path / slug
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / ".env").write_text("MONGO_URI=mongodb://example\n")
        patch_dir = tmp_path / "patches"
        patch_dir.mkdir(exist_ok=True)
        for name in ("format.cjs", "share.js", "stream.cjs", "search.cjs"):
            (patch_dir / name).write_text("// patch\n")
        (tmp_path / "klai-entrypoint.sh").write_text('#!/bin/sh\nexec docker-entrypoint.sh "$@"\n')

    def test_create_refuses_when_a_patch_source_is_a_directory(self, tmp_path):
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)
        # Reproduce exactly what Docker leaves behind after the source is gone.
        stream = tmp_path / "patches" / "stream.cjs"
        stream.unlink()
        stream.mkdir()

        mock_client = MagicMock()
        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.7"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            with pytest.raises(RuntimeError, match=r"stream\.cjs"):
                _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

        mock_client.containers.create.assert_not_called()

    def test_create_refuses_when_the_entrypoint_wrapper_is_a_directory(self, tmp_path):
        from app.services.provisioning import _start_librechat_container

        self._write_lc_files(tmp_path)
        wrapper = tmp_path / "klai-entrypoint.sh"
        wrapper.unlink()
        wrapper.mkdir()

        mock_client = MagicMock()
        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:v0.8.7"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            with pytest.raises(RuntimeError, match=r"klai-entrypoint\.sh"):
                _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

        mock_client.containers.create.assert_not_called()


class TestAssertSharedLibrechatMountSourcesIntact:
    """The fleet-shared preflight used by BOTH the create and restart paths."""

    def _write_shared(self, tmp_path: Path) -> None:
        patch_dir = tmp_path / "patches"
        patch_dir.mkdir(exist_ok=True)
        for name in ("format.cjs", "share.js", "stream.cjs", "search.cjs"):
            (patch_dir / name).write_text("// patch\n")
        (tmp_path / "klai-entrypoint.sh").write_text("#!/bin/sh\n")

    def test_passes_when_every_shared_source_is_a_file(self, tmp_path):
        from app.services.provisioning.infrastructure import assert_shared_librechat_mount_sources_intact

        self._write_shared(tmp_path)
        with patch("app.services.provisioning.infrastructure.settings") as mock_settings:
            mock_settings.librechat_container_data_path = str(tmp_path)
            assert_shared_librechat_mount_sources_intact()

    def test_names_every_broken_source_and_its_shape(self, tmp_path):
        from app.services.provisioning.infrastructure import assert_shared_librechat_mount_sources_intact

        self._write_shared(tmp_path)
        (tmp_path / "patches" / "format.cjs").unlink()
        stream = tmp_path / "patches" / "stream.cjs"
        stream.unlink()
        stream.mkdir()

        with patch("app.services.provisioning.infrastructure.settings") as mock_settings:
            mock_settings.librechat_container_data_path = str(tmp_path)
            with pytest.raises(RuntimeError) as exc:
                assert_shared_librechat_mount_sources_intact()

        message = str(exc.value)
        # Both broken sources reported in one go -- an operator should not have
        # to re-run to discover the second one.
        assert "format.cjs" in message
        assert "stream.cjs" in message
        assert "missing" in message
        assert "directory" in message
        # The intact ones are not noise in the error.
        assert "share.js" not in message
