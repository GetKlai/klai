"""
Provisioning infrastructure: Docker, MongoDB, Caddy, and Redis operations.

All functions in this module interact with external systems (Docker containers,
MongoDB, Caddy, Redis). They are synchronous where indicated (for use with
run_in_executor).

Protocol-first rule (SEC-021, see platform/docker-socket-proxy.md):
we talk MongoDB and Redis over their native wire protocols, never through
`container.exec_run([...])`. The docker-socket-proxy in front of portal-api
denies `/exec/*/start` by design, and even if we flipped the allow-bit it
would hand any tenant-provisioning bug a shell on the host.
"""

import time
from pathlib import Path

import docker
import pymongo
import redis
import structlog
from pymongo.errors import OperationFailure

from app.core.config import settings
from app.services.provisioning.generators import _generate_librechat_yaml

logger = structlog.get_logger()

# MongoDB error code for "user not found" (raised by dropUser when the target
# user does not exist). Non-fatal for idempotent drop.
_MONGO_USER_NOT_FOUND = 11


def _redis_sync_client() -> redis.Redis:
    """Connect to the shared Redis over the klai-net Docker network.

    Sync client — callers live in `run_in_executor`, so we cannot use
    `redis.asyncio` here. Use as a context manager so the TCP connection is
    closed even on exception.
    """
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        decode_responses=True,
    )


def _mongo_admin_client() -> pymongo.MongoClient:
    """Connect to MongoDB as the root user for user-lifecycle operations.

    Only used by provisioning flows (createUser / dropUser). Tenant runtime
    traffic uses the per-tenant MongoDB user, never this client.
    """
    return pymongo.MongoClient(
        host=settings.mongodb_container_name,
        port=27017,
        username=settings.mongo_root_username,
        password=settings.mongo_root_password,
        authSource="admin",
    )


def _sync_remove_container(name: str) -> None:
    """Remove a Docker container by name (sync, for use with run_in_executor)."""
    client = docker.from_env()
    try:
        c = client.containers.get(name)
        c.remove(force=True)
    except docker.errors.NotFound:  # type: ignore[attr-defined]
        pass


def _sync_drop_mongodb_tenant_user(slug: str) -> None:
    """Drop the MongoDB user for a tenant (sync, for use with run_in_executor).

    Idempotent: a missing user is not an error — tenant offboarding can be
    re-run safely if a previous attempt was interrupted.
    """
    db_name = f"librechat-{slug}"
    user = f"librechat-{slug}"
    with _mongo_admin_client() as client:
        try:
            client[db_name].command("dropUser", user)
            logger.info("mongodb_tenant_user_dropped", slug=slug, db=db_name)
        except OperationFailure as exc:
            if exc.code == _MONGO_USER_NOT_FOUND:
                logger.info("mongodb_tenant_user_already_absent", slug=slug, db=db_name)
                return
            raise


def _create_mongodb_tenant_user(slug: str, tenant_password: str) -> None:
    """Create a per-tenant MongoDB user with readWrite on the tenant's DB only."""
    db_name = f"librechat-{slug}"
    user = f"librechat-{slug}"
    try:
        with _mongo_admin_client() as client:
            client[db_name].command(
                "createUser",
                user,
                pwd=tenant_password,
                roles=[{"role": "readWrite", "db": db_name}],
            )
        logger.info("mongodb_tenant_user_created", slug=slug, db=db_name)
    except OperationFailure as exc:
        raise RuntimeError(f"MongoDB tenant user creation failed for {slug} (code {exc.code}): {exc.details}") from exc


def _flush_redis_and_restart_librechat(slug: str) -> None:
    """Invalidate the LibreChat config cache and restart the tenant container.

    LibreChat caches librechat.yaml in Redis with no TTL (see
    platform/librechat.md -- Redis config caching). The cache invalidation
    must run before the restart so the container reads the updated config
    from disk.

    SPEC-SEC-INTERNAL-001 REQ-2: this previously called FLUSHALL, which
    cleared every key in Redis -- rate-limit buckets, SSO cache, partner-API
    state for every tenant. We now SCAN MATCH the configured pattern
    (``configs:*`` by default per REQ-2.3) and UNLINK each match, which
    leaves unrelated keys untouched.

    Fail-loud: both the cache invalidation and the post-restart health check
    are hard requirements. A failed invalidation means LibreChat keeps
    serving stale yaml and the operator thinks their change landed; a failed
    health check means the tenant's LibreChat is down and provisioning
    silently succeeded. Both were previously logged as warnings and ignored.
    Now they raise.
    """
    with _redis_sync_client() as client:
        cache_pattern = settings.librechat_cache_key_pattern
        deleted = 0
        batch: list[str] = []
        for key in client.scan_iter(match=cache_pattern, count=100):
            batch.append(key)
            if len(batch) >= 100:
                # The sync redis client returns int from UNLINK; the upstream
                # type hint widens to ResponseT (Awaitable on the async client).
                deleted += int(client.unlink(*batch))  # type: ignore[arg-type]
                batch.clear()
        if batch:
            deleted += int(client.unlink(*batch))  # type: ignore[arg-type]
    logger.info(
        "librechat_cache_invalidated",
        slug=slug,
        pattern=cache_pattern,
        deleted=deleted,
    )

    # Restart the tenant's LibreChat container. /containers/{id}/restart is
    # allowed by docker-socket-proxy (CONTAINERS=1 + POST=1).
    docker_client = docker.from_env()
    container_name = f"librechat-{slug}"
    container = docker_client.containers.get(container_name)
    container.restart(timeout=10)
    logger.info("librechat_container_restarted", container=container_name)

    # Health check: wait up to 30s for the container to reach running state.
    # @MX:NOTE: sync sleep intentional — this function is invoked only via
    # loop.run_in_executor() from async callers (app/api/mcp_servers.py + this
    # module's provisioning orchestrator). Inside the executor thread there is
    # no running event loop, so asyncio.sleep would raise RuntimeError.
    deadline = time.monotonic() + 30
    last_status: str | None = None
    while time.monotonic() < deadline:
        try:
            container.reload()
            last_status = container.status
            if last_status == "running":
                logger.info("librechat_container_running", container=container_name)
                return
        except Exception as exc:
            logger.debug("container_health_check_reload_failed", error=str(exc))
        time.sleep(3)  # nosemgrep: arbitrary-sleep

    # Timed out. Previously a warning; now fatal so provisioning / config
    # regeneration explicitly fails and the operator sees it.
    raise RuntimeError(
        f"LibreChat container {container_name!r} did not reach running state "
        f"within 30s after restart (last status: {last_status})"
    )


def _write_tenant_caddyfile(slug: str) -> None:
    """Write a per-tenant Caddyfile to the tenants directory.

    Each tenant gets chat-{slug}.{domain} as a separate site block.
    The main Caddyfile imports /etc/caddy/tenants/*.caddyfile, which maps
    to the caddy-tenants Docker volume (also mounted in portal-api at /caddy/tenants).
    """
    domain = settings.domain
    tenants_path = Path(settings.caddy_tenants_path)
    tenants_path.mkdir(parents=True, exist_ok=True)
    content = f"""# Tenant: {slug}
# Auto-generated by portal-api at provisioning time. Do not edit manually.
chat-{slug}.{domain} {{
    header {{
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
        Content-Security-Policy "frame-ancestors https://*.{domain}"
        -Server
    }}
    rate_limit {{
        zone chat_{slug}_per_ip {{
            key {{remote_host}}
            events 120
            window 1m
        }}
    }}
    reverse_proxy librechat-{slug}:3080
}}
"""
    tenant_file = tenants_path / f"{slug}.caddyfile"
    tenant_file.write_text(content)


def _reload_caddy() -> None:
    """Restart Caddy to pick up new tenant config.

    admin off disables the Admin API so caddy reload cannot work.
    Restart is the correct approach — ~1s TLS interruption, acceptable at current scale.
    """
    client = docker.from_env()
    caddy = client.containers.get(settings.caddy_container_name)
    caddy.restart(timeout=10)


def _start_librechat_container(
    slug: str,
    env_file_host_path: str,
    mcp_servers: dict | None = None,
) -> None:
    """Start the LibreChat Docker container for a tenant (synchronous, run in executor)."""
    client = docker.from_env()
    container_name = f"librechat-{slug}"

    # Remove stale container if it exists (e.g. failed previous provisioning)
    try:
        old = client.containers.get(container_name)
        old.remove(force=True)
    except docker.errors.NotFound:  # type: ignore[attr-defined]
        pass

    librechat_host_base = settings.librechat_host_data_path

    # Generate per-tenant librechat.yaml by merging base config with tenant MCP servers
    base_yaml_path = Path(settings.librechat_container_data_path) / "librechat.yaml"
    tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, mcp_servers)
    tenant_yaml_dir = Path(settings.librechat_container_data_path) / slug
    tenant_yaml_dir.mkdir(parents=True, exist_ok=True)
    (tenant_yaml_dir / "librechat.yaml").write_text(tenant_yaml_content)

    client.containers.run(  # type: ignore[call-overload]  # nosemgrep: docker-arbitrary-container-run
        image=settings.librechat_image,
        name=container_name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},  # type: ignore[arg-type]
        volumes={
            env_file_host_path: {"bind": "/app/.env", "mode": "ro"},
            f"{librechat_host_base}/{slug}/librechat.yaml": {"bind": "/app/librechat.yaml", "mode": "ro"},
            f"{librechat_host_base}/{slug}/images": {"bind": "/app/client/public/images", "mode": "rw"},
        },
        network="klai-net",
    )

    # Connect to additional networks. Fail-loud: LibreChat can't reach MongoDB /
    # Meilisearch / Redis without these, so a silent skip leaves the tenant with
    # a broken container. Let the exception bubble to the orchestrator's outer
    # handler which rolls back provisioning.
    for net_name in ["klai-net-mongodb", "klai-net-meilisearch", "klai-net-redis"]:
        net = client.networks.get(net_name)
        net.connect(container_name)
