"""
Provisioning infrastructure: Docker, MongoDB, Caddy, and Redis operations.

All functions in this module interact with external systems (Docker containers,
MongoDB, Caddy, Redis). They are synchronous where indicated (for use with
run_in_executor).
"""

import logging
import time
from pathlib import Path

import docker

from app.core.config import settings
from app.services.provisioning.generators import _generate_librechat_yaml

logger = logging.getLogger(__name__)


def _sync_remove_container(name: str) -> None:
    """Remove a Docker container by name (sync, for use with run_in_executor)."""
    client = docker.from_env()
    try:
        c = client.containers.get(name)
        c.remove(force=True)
    except docker.errors.NotFound:  # type: ignore[attr-defined]
        pass


def _sync_drop_mongodb_tenant_user(slug: str) -> None:
    """Drop the MongoDB user for a tenant (sync, for use with run_in_executor)."""
    c = docker.from_env()
    db_name = f"librechat-{slug}"
    user = f"librechat-{slug}"
    script = f'db.getSiblingDB("{db_name}").dropUser("{user}")'
    mongodb_container = settings.mongodb_container_name
    container = c.containers.get(mongodb_container)
    container.exec_run(
        [
            "mongosh",
            "--quiet",
            "-u",
            settings.mongo_root_username,
            "-p",
            settings.mongo_root_password,
            "--authenticationDatabase",
            "admin",
            "--eval",
            script,
        ],
        stdout=True,
        stderr=True,
    )


def _create_mongodb_tenant_user(slug: str, tenant_password: str) -> None:
    """Create a per-tenant MongoDB user with readWrite access on the tenant's database only."""
    client = docker.from_env()
    db_name = f"librechat-{slug}"
    user = f"librechat-{slug}"
    script = (
        f'db.getSiblingDB("{db_name}").createUser({{'
        f'"user": "{user}", '
        f'"pwd": "{tenant_password}", '
        f'"roles": [{{"role": "readWrite", "db": "{db_name}"}}]'
        f"}})"
    )
    mongodb_container = settings.mongodb_container_name
    container = client.containers.get(mongodb_container)
    exit_code, output = container.exec_run(
        [
            "mongosh",
            "--quiet",
            "-u",
            settings.mongo_root_username,
            "-p",
            settings.mongo_root_password,
            "--authenticationDatabase",
            "admin",
            "--eval",
            script,
        ],
        stdout=True,
        stderr=True,
    )
    if exit_code != 0:
        raise RuntimeError(f"MongoDB tenant user creation failed for {slug} (exit {exit_code}): {output.decode()}")


def _flush_redis_and_restart_librechat(slug: str) -> None:
    """Flush Redis config cache and restart the LibreChat container for a tenant.

    LibreChat caches librechat.yaml in Redis with no TTL (platform-librechat-redis-config-cache).
    FLUSHALL must run before the restart so the container reads the updated config from disk.

    R-001: FLUSHALL clears all Redis keys including active sessions. Acceptable for config
    updates; document in UI that changes cause a brief interruption.
    """
    client = docker.from_env()

    # Flush Redis so LibreChat re-reads librechat.yaml on next startup
    try:
        redis_container = client.containers.get(settings.redis_container_name)
        redis_cmd = ["redis-cli"]
        if settings.redis_password:
            redis_cmd += ["-a", settings.redis_password]
        redis_cmd.append("FLUSHALL")
        exit_code, output = redis_container.exec_run(redis_cmd)
        if exit_code != 0:
            logger.warning(
                "Redis FLUSHALL mislukt voor tenant %s (exit %d): %s",
                slug,
                exit_code,
                output.decode(),
            )
        else:
            logger.info("Redis FLUSHALL voltooid voor tenant %s", slug)
    except docker.errors.NotFound:  # type: ignore[attr-defined]
        logger.warning("Redis container '%s' niet gevonden, FLUSHALL overgeslagen", settings.redis_container_name)

    # Restart the tenant's LibreChat container
    container_name = f"librechat-{slug}"
    container = client.containers.get(container_name)
    container.restart(timeout=10)
    logger.info("Container %s herstart na config update", container_name)

    # Health check: wait up to 30s for container to reach running state
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            container.reload()
            if container.status == "running":
                logger.info("Container %s is actief na herstart", container_name)
                return
        except Exception as exc:
            logger.debug("Container reload mislukt tijdens health check: %s", exc)
        time.sleep(3)

    logger.warning("Container %s niet 'running' na 30s health check", container_name)


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

    # Connect to additional networks
    for net_name in ["klai-net-mongodb", "klai-net-meilisearch", "klai-net-redis"]:
        try:
            net = client.networks.get(net_name)
            net.connect(container_name)
        except Exception as exc:
            logger.warning("Could not connect %s to %s: %s", container_name, net_name, exc)
