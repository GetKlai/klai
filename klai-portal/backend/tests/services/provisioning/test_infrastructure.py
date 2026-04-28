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
        mock.librechat_image = "ghcr.io/danny-avila/librechat:latest"
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
        assert "rate_limit" in content

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
    client.flushall = MagicMock(side_effect=AssertionError(
        "FLUSHALL must never be called -- SPEC-SEC-INTERNAL-001 REQ-2",
    ))
    client.close = MagicMock()
    client._unlinked = unlinked  # noqa: SLF001 -- test-only attribute
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

    def test_starts_container_with_correct_config(self, tmp_path):
        from app.services.provisioning import _start_librechat_container

        # Create base yaml file
        base_yaml = Path(tmp_path) / "librechat.yaml"
        base_yaml.write_text("version: 1.0\n")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = type("NotFound", (Exception,), {})("not found")

        with (
            patch("app.services.provisioning.infrastructure.docker") as mock_docker,
            patch("app.services.provisioning.infrastructure.settings") as mock_settings,
        ):
            mock_settings.librechat_host_data_path = "/opt/klai/librechat-data"
            mock_settings.librechat_container_data_path = str(tmp_path)
            mock_settings.librechat_image = "ghcr.io/danny-avila/librechat:latest"
            mock_docker.from_env.return_value = mock_client
            mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
            mock_client.containers.get.side_effect = mock_docker.errors.NotFound("not found")

            _start_librechat_container("acme", "/opt/klai/librechat-data/acme/.env")

            mock_client.containers.run.assert_called_once()
            call_kwargs = mock_client.containers.run.call_args
            assert call_kwargs[1]["name"] == "librechat-acme"
            assert call_kwargs[1]["detach"] is True
            assert call_kwargs[1]["network"] == "klai-net"
