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

import asyncio
import time
import urllib.error
import urllib.request
from pathlib import Path

import docker
import pymongo
import redis
import structlog
from pymongo.errors import OperationFailure

from app.core.config import settings
from app.core.provisioning_names import validate_slug_for_provisioning
from app.services.provisioning._slug_guard import _assert_safe_slug
from app.services.provisioning.generators import _generate_librechat_yaml
from app.services.secrets import decrypt_mcp_secret, is_secret_var

logger = structlog.get_logger()

# MongoDB error code for "user not found" (raised by dropUser when the target
# user does not exist). Non-fatal for idempotent drop.
_MONGO_USER_NOT_FOUND = 11

_LIBRECHAT_REQUIRED_ENV_FLAGS = {
    "ALLOW_SHARED_LINKS": "true",
    "ALLOW_SHARED_LINKS_PUBLIC": "true",
}

_LIBRECHAT_PATCH_MOUNTS = {
    "patches/format.cjs": "/app/node_modules/@librechat/agents/dist/cjs/messages/format.cjs",
    "patches/share.js": "/app/api/server/routes/share.js",
    "patches/stream.cjs": "/app/node_modules/@librechat/agents/dist/cjs/stream.cjs",
    "patches/search.cjs": "/app/node_modules/@librechat/agents/dist/cjs/tools/search/search.cjs",
    "patches/createStreamServices.ts": "/app/packages/api/src/stream/createStreamServices.ts",
}

_LIBRECHAT_OPENID_READY_BOOT_ATTEMPTS = 3
_LIBRECHAT_OPENID_READY_BOOT_TIMEOUT_SECONDS = 45
_LIBRECHAT_OPENID_PROBE_INTERVAL_SECONDS = 2
_LIBRECHAT_OPENID_PROBE_TIMEOUT_SECONDS = 5

# Process-wide lock that serialises Caddy file writes + container restarts.
# Both `provision_tenant` (orchestrator.py) and `deprovision_tenant`
# (deprovisioning_orchestrator.py + deprovisioning_steps.py) acquire this
# lock around `_write_tenant_caddyfile` / file-unlink + `_reload_caddy`.
# Defined here (next to _reload_caddy) so a single import path works for
# both code paths and accidental "two locks, both serialise nothing" is
# impossible. See SPEC-INFRA-TENANT-DELETE-001 R11.
_caddy_lock: asyncio.Lock = asyncio.Lock()


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


def _sync_drop_mongodb_tenant_database(slug: str) -> None:
    """Drop the MongoDB database for a tenant (sync, for use with run_in_executor).

    Idempotent: dropping a non-existent database is a no-op in MongoDB —
    the server returns ok:1 even when the database does not exist.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC-INFRA-TENANT-DELETE-001 R3.
    """
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
    names = validate_slug_for_provisioning(slug, domain=settings.domain)
    db_name = names.mongodb_database
    with _mongo_admin_client() as client:
        # MongoDB dropDatabase on a missing DB returns ok:1, no error raised.
        client.drop_database(db_name)
        logger.info("mongodb_tenant_database_dropped", slug=slug, db=db_name)


def _sync_drop_mongodb_tenant_user(slug: str) -> None:
    """Drop the MongoDB user for a tenant (sync, for use with run_in_executor).

    Idempotent: a missing user is not an error — tenant offboarding can be
    re-run safely if a previous attempt was interrupted.
    """
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
    names = validate_slug_for_provisioning(slug, domain=settings.domain)
    db_name = names.mongodb_database
    user = names.mongodb_user
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
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
    names = validate_slug_for_provisioning(slug, domain=settings.domain)
    db_name = names.mongodb_database
    user = names.mongodb_user
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


def _read_dotenv_file(path: Path) -> dict[str, str]:
    """Parse the generated tenant .env into Docker process env values."""
    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key:
            raise RuntimeError(f"Invalid LibreChat env line in {path} at line {lineno}")
        values[key] = value
    return values


def _ensure_librechat_env_flags(path: Path) -> dict[str, str]:
    """Persist required non-secret flags and return process env for Docker."""
    env = _read_dotenv_file(path)
    changed = False
    for key, value in _LIBRECHAT_REQUIRED_ENV_FLAGS.items():
        if env.get(key) != value:
            env[key] = value
            changed = True

    if changed:
        lines = path.read_text().splitlines()
        seen: set[str] = set()
        updated: list[str] = []
        for line in lines:
            key, sep, _value = line.partition("=")
            if sep and key in _LIBRECHAT_REQUIRED_ENV_FLAGS:
                updated.append(f"{key}={_LIBRECHAT_REQUIRED_ENV_FLAGS[key]}")
                seen.add(key)
            else:
                updated.append(line)

        missing = [key for key in _LIBRECHAT_REQUIRED_ENV_FLAGS if key not in seen]
        insert_at = next(
            (idx + 1 for idx, line in enumerate(updated) if line.startswith("ALLOW_IFRAME=")),
            len(updated),
        )
        for offset, key in enumerate(missing):
            updated.insert(insert_at + offset, f"{key}={_LIBRECHAT_REQUIRED_ENV_FLAGS[key]}")
        path.write_text("\n".join(updated).rstrip() + "\n")

    return env


def _mcp_env_lines(server_id: str, server_cfg: dict) -> list[str]:
    """Render one enabled MCP server's env block for an existing tenant .env."""
    if not server_cfg.get("enabled", False):
        return []
    env_vars = server_cfg.get("env", {})
    if not env_vars:
        return []

    lines = [f"# MCP server: {server_id}"]
    for var_name, encrypted_or_plain in env_vars.items():
        if is_secret_var(var_name):
            value = decrypt_mcp_secret(encrypted_or_plain)
        else:
            value = encrypted_or_plain
        lines.append(f"{var_name}={value}")
    return lines


def _sync_librechat_mcp_env(path: Path, mcp_servers: dict | None) -> None:
    """Replace generated MCP env blocks in an existing tenant .env file."""
    configured_servers = mcp_servers or {}
    configured_ids = set(configured_servers)
    existing_lines = path.read_text().splitlines()
    kept: list[str] = []
    idx = 0

    while idx < len(existing_lines):
        line = existing_lines[idx]
        stripped = line.strip()
        if stripped.startswith("# MCP server:"):
            server_id = stripped.removeprefix("# MCP server:").strip()
            if server_id in configured_ids:
                idx += 1
                while idx < len(existing_lines):
                    next_stripped = existing_lines[idx].strip()
                    if next_stripped.startswith("# MCP server:"):
                        break
                    if next_stripped == "":
                        idx += 1
                        break
                    idx += 1
                continue

        kept.append(line)
        idx += 1

    rendered_blocks: list[str] = []
    for server_id, server_cfg in configured_servers.items():
        block = _mcp_env_lines(server_id, server_cfg)
        if block:
            if rendered_blocks:
                rendered_blocks.append("")
            rendered_blocks.extend(block)

    output = "\n".join(kept).rstrip()
    if rendered_blocks:
        rendered = "\n".join(rendered_blocks).rstrip()
        output = f"{output}\n\n{rendered}" if output else rendered
    path.write_text(output.rstrip() + "\n")


def _sync_librechat_tenant_config_files(slug: str, mcp_servers: dict | None = None) -> None:
    """Materialize the tenant YAML and MCP env blocks before restarting LibreChat."""
    _assert_safe_slug(slug)
    base_yaml_path = Path(settings.librechat_container_data_path) / "librechat.yaml"
    tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, mcp_servers)
    tenant_yaml_dir = Path(settings.librechat_container_data_path) / slug
    tenant_yaml_dir.mkdir(parents=True, exist_ok=True)
    (tenant_yaml_dir / "images").mkdir(exist_ok=True)
    (tenant_yaml_dir / "librechat.yaml").write_text(tenant_yaml_content)

    tenant_env_path = tenant_yaml_dir / ".env"
    if not tenant_env_path.exists():
        raise RuntimeError(f"LibreChat tenant env file missing for {slug}: {tenant_env_path}")
    _sync_librechat_mcp_env(tenant_env_path, mcp_servers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe_librechat_openid(container_name: str) -> tuple[int, str]:
    """Return the in-container HTTP status for LibreChat OpenID login."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    request = urllib.request.Request(
        f"http://{container_name}:3080/oauth/openid",
        headers={"User-Agent": "klai-provisioning-openid-healthcheck"},
    )

    try:
        with opener.open(request, timeout=_LIBRECHAT_OPENID_PROBE_TIMEOUT_SECONDS) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        detail = exc.headers.get("Location", "") or exc.read(256).decode("utf-8", "replace")
        return exc.code, detail
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return 0, str(exc)


def _wait_for_librechat_openid_ready(client, container_name: str) -> None:
    """Wait until LibreChat has registered its OpenID strategy.

    LibreChat configures Passport strategies once during boot. If its OpenID
    discovery fetch fails, the process still starts but `/oauth/openid` stays a
    permanent 500 until restart. Provisioning must catch that before marking the
    tenant ready.
    """
    last_status = 0
    last_detail = ""

    for boot_attempt in range(1, _LIBRECHAT_OPENID_READY_BOOT_ATTEMPTS + 1):
        deadline = time.monotonic() + _LIBRECHAT_OPENID_READY_BOOT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            last_status, last_detail = _probe_librechat_openid(container_name)
            if 300 <= last_status < 400:
                logger.info(
                    "librechat_openid_ready",
                    container=container_name,
                    boot_attempt=boot_attempt,
                    status=last_status,
                )
                return

            if last_status >= 500:
                logger.warning(
                    "librechat_openid_probe_server_error",
                    container=container_name,
                    boot_attempt=boot_attempt,
                    status=last_status,
                    detail=last_detail[:200],
                )
                break

            time.sleep(_LIBRECHAT_OPENID_PROBE_INTERVAL_SECONDS)

        if boot_attempt < _LIBRECHAT_OPENID_READY_BOOT_ATTEMPTS:
            logger.warning(
                "librechat_openid_not_ready_restarting",
                container=container_name,
                boot_attempt=boot_attempt,
                last_status=last_status,
                last_detail=last_detail[:200],
            )
            client.containers.get(container_name).restart(timeout=10)
            continue

    raise RuntimeError(
        f"LibreChat OpenID did not become ready for {container_name}: status={last_status}, detail={last_detail[:200]}"
    )


def _invalidate_librechat_config_cache(slug: str) -> None:
    """Invalidate LibreChat's cached yaml config for tenant config rollouts.

    LibreChat caches librechat.yaml in Redis with no TTL (see
    platform/librechat.md -- Redis config caching). Invalidation must run
    before restart/recreate so the process reads updated config from disk.

    SPEC-SEC-INTERNAL-001 REQ-2: this previously called FLUSHALL, which
    cleared every key in Redis -- rate-limit buckets, SSO cache, partner-API
    state for every tenant. We now SCAN MATCH the configured pattern
    (``configs:*`` by default per REQ-2.3) and UNLINK each match, which
    leaves unrelated keys untouched.
    """
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
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


def _flush_redis_and_restart_librechat(slug: str) -> None:
    """Invalidate the LibreChat config cache and restart the tenant container.

    Fail-loud: both the cache invalidation and the post-restart health check
    are hard requirements. A failed invalidation means LibreChat keeps
    serving stale yaml and the operator thinks their change landed; a failed
    health check means the tenant's LibreChat is down and provisioning
    silently succeeded. Both were previously logged as warnings and ignored.
    Now they raise.
    """
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
    _invalidate_librechat_config_cache(slug)

    # Restart the tenant's LibreChat container. /containers/{id}/restart is
    # allowed by docker-socket-proxy (CONTAINERS=1 + POST=1).
    docker_client = docker.from_env()
    container_name = validate_slug_for_provisioning(slug, domain=settings.domain).librechat_container
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
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
    domain = settings.domain
    names = validate_slug_for_provisioning(slug, domain=domain)
    tenants_path = Path(settings.caddy_tenants_path)
    tenants_path.mkdir(parents=True, exist_ok=True)
    content = f"""# Tenant: {slug}
# Auto-generated by portal-api at provisioning time. Do not edit manually.
{names.chat_host} {{
    header {{
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
        Content-Security-Policy "frame-ancestors https://*.{domain}"
        -Server
    }}
    @chat_generation {{
        method POST
        path /api/agents/chat/* /api/ask/*
    }}
    rate_limit @chat_generation {{
        zone {names.caddy_rate_limit_zone} {{
            key {{remote_host}}
            events 120
            window 1m
        }}
    }}
    reverse_proxy {names.librechat_container}:3080
}}
"""
    tenant_file = tenants_path / names.caddyfile_name
    tenant_file.write_text(content)


def _reload_caddy() -> None:
    """Restart Caddy to pick up new tenant config.

    admin off disables the Admin API so caddy reload cannot work.
    Restart is the correct approach — ~1s TLS interruption, acceptable at current scale.
    """
    client = docker.from_env()
    caddy = client.containers.get(settings.caddy_container_name)
    caddy.restart(timeout=10)


def _create_and_start_librechat_container(
    client,
    container_name: str,
    slug: str,
    env_file_host_path: str,
    mcp_servers: dict | None,
    image: str,
) -> None:
    """Create, network-attach, boot and health-gate a tenant's LibreChat container.

    Pure "create a working container" logic, factored out of
    `_start_librechat_container` so the rollback path in that function can
    replay it with a different (previous-known-good) `image`. Raises on any
    failure, including a failed OpenID readiness gate -- callers decide what
    to do about a failure (roll back, let it propagate, etc).
    """
    librechat_host_base = settings.librechat_host_data_path

    # Generate per-tenant librechat.yaml by merging base config with tenant MCP servers
    base_yaml_path = Path(settings.librechat_container_data_path) / "librechat.yaml"
    tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, mcp_servers)
    tenant_yaml_dir = Path(settings.librechat_container_data_path) / slug
    tenant_yaml_dir.mkdir(parents=True, exist_ok=True)
    (tenant_yaml_dir / "images").mkdir(exist_ok=True)
    (tenant_yaml_dir / "librechat.yaml").write_text(tenant_yaml_content)
    tenant_env_path = tenant_yaml_dir / ".env"
    if not tenant_env_path.exists():
        raise RuntimeError(f"LibreChat tenant env file missing for {slug}: {tenant_env_path}")
    container_environment = _ensure_librechat_env_flags(tenant_env_path)

    volumes = {
        env_file_host_path: {"bind": "/app/.env", "mode": "ro"},
        f"{librechat_host_base}/{slug}/librechat.yaml": {"bind": "/app/librechat.yaml", "mode": "ro"},
        f"{librechat_host_base}/{slug}/images": {"bind": "/app/client/public/images", "mode": "rw"},
    }
    for source_rel_path, destination in _LIBRECHAT_PATCH_MOUNTS.items():
        patch_container_path = Path(settings.librechat_container_data_path) / source_rel_path
        if not patch_container_path.exists():
            raise RuntimeError(f"LibreChat patch file missing: {patch_container_path}")
        volumes[f"{librechat_host_base}/{source_rel_path}"] = {"bind": destination, "mode": "ro"}

    # Klai entrypoint wrapper that forces light theme on every tenant (LibreChat
    # has no server-side theme config — see deploy/librechat/klai-entrypoint.sh).
    # Mounted read-only; the container `entrypoint` below runs it before boot.
    # Fail-loud if missing: a missing bind source would make Docker create an
    # empty directory at /klai-entrypoint.sh and the container would crash on
    # start. The file is synced to the host by deploy-compose.yml.
    entrypoint_container_path = Path(settings.librechat_container_data_path) / "klai-entrypoint.sh"
    if not entrypoint_container_path.exists():
        raise RuntimeError(f"LibreChat entrypoint wrapper missing: {entrypoint_container_path}")
    volumes[f"{librechat_host_base}/klai-entrypoint.sh"] = {
        "bind": "/klai-entrypoint.sh",
        "mode": "ro",
    }

    # @MX:ANCHOR provisioning-labels — SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2.
    # These three labels mark the container as klasse-B (provisioning-managed)
    # so that hooks (.claude/hooks/klai/container-hygiene-preflight.sh) and
    # the weekly orphan-audit recognise it as a legitimate prod container,
    # NOT as a wees-container without compose-label. Removing or renaming
    # these breaks the hygiene-detection layer; treat as part of the
    # tenant-provisioning contract. See container-hygiene.md.
    container_labels = {
        "klai.managed_by": "portal-api-provisioning",
        "klai.tenant_slug": slug,
        "klai.kind": "librechat",
    }

    # @MX:WARN: [AUTO] CREATE (not run) → connect all networks while stopped →
    #   START. The container MUST boot exactly once with every network already
    #   attached and settled.
    # @MX:REASON: LibreChat configures its OpenID (OIDC) passport strategy ONCE
    #   at boot by fetching the discovery doc from OPENID_ISSUER
    #   (https://auth.getklai.com). Connecting a network to a *running*
    #   container briefly reconfigures its networking; a discovery fetch during
    #   that window fails ("[openidStrategy] fetch failed -> strategy not
    #   registered"), leaving chat login dead ("Unknown authentication strategy
    #   'openid'") until a manual restart. The earlier "run then restart after
    #   connect" attempt made it worse — the restart landed inside that very
    #   window and broke an otherwise-successful first boot. Booting once with
    #   all networks pre-attached avoids the disruption entirely.
    #   (2026-05-22 onboarding/chat incident.)
    container = client.containers.create(  # type: ignore[call-overload]  # nosemgrep: docker-arbitrary-container-run
        image=image,
        name=container_name,
        restart_policy={"Name": "unless-stopped"},  # type: ignore[arg-type]
        labels=container_labels,
        environment=container_environment,
        volumes=volumes,
        network="klai-net",
        # Force light theme via the Klai entrypoint wrapper. Setting entrypoint
        # requires passing the original command through explicitly so LibreChat
        # still boots with `npm run backend`; the wrapper forwards it via "$@".
        entrypoint=["/bin/sh", "/klai-entrypoint.sh"],
        command=["npm", "run", "backend"],
    )

    # Connect the extra networks while the container is still STOPPED, so the
    # single boot below sees a stable multi-network config. Fail-loud: LibreChat
    # can't reach MongoDB / Meilisearch / Redis without these; let the exception
    # bubble to the orchestrator's outer handler which rolls back provisioning.
    for net_name in ["klai-net-mongodb", "klai-net-meilisearch", "klai-net-redis"]:
        net = client.networks.get(net_name)
        net.connect(container_name)

    container.start()
    _wait_for_librechat_openid_ready(client, container_name)


def _restore_librechat_container(
    client,
    container_name: str,
    slug: str,
    env_file_host_path: str,
    mcp_servers: dict | None,
    old_image_ref: str,
) -> None:
    """Best-effort restore of a tenant's LibreChat container to its previous image.

    Called only after a recreate attempt has already failed its
    create/boot/readiness gate (see `_start_librechat_container`,
    `rollback_on_failure=True`). Never raises: a failed restore must not mask
    the original failure that triggered it -- the caller re-raises that
    original exception regardless of what happens here. The outcome is
    logged either way so an operator can see whether the tenant was left
    running the old image or with no container at all.
    """
    logger.warning(
        "librechat_container_restore_attempt",
        slug=slug,
        container=container_name,
        old_image=old_image_ref,
    )
    try:
        # Remove whatever half-booted container the failed attempt left behind.
        try:
            broken = client.containers.get(container_name)
            broken.remove(force=True)
        except docker.errors.NotFound:  # type: ignore[attr-defined]
            pass

        _create_and_start_librechat_container(
            client,
            container_name,
            slug,
            env_file_host_path,
            mcp_servers,
            image=old_image_ref,
        )
    except Exception as restore_exc:
        logger.exception(
            "librechat_container_restore_failed",
            slug=slug,
            container=container_name,
            old_image=old_image_ref,
            error=str(restore_exc),
        )
        return

    logger.info(
        "librechat_container_restore_succeeded",
        slug=slug,
        container=container_name,
        old_image=old_image_ref,
    )


def _start_librechat_container(
    slug: str,
    env_file_host_path: str,
    mcp_servers: dict | None = None,
    *,
    rollback_on_failure: bool = False,
) -> None:
    """Start the LibreChat Docker container for a tenant (synchronous, run in executor).

    `rollback_on_failure` (finding 3A, adversarial review 2026-08-13): when
    set, the currently-running container's image is recorded BEFORE it is
    force-removed. If the replacement container then fails to create, boot,
    or pass its OpenID readiness gate, a best-effort attempt is made to
    restore the tenant on the previous image so it isn't left with nothing.
    The restore outcome is logged; the original failure always propagates,
    restore or no restore. Defaults to off so the initial-provisioning path
    (no prior container to roll back to) and the MCP-config-apply recreate
    path keep their existing behaviour unchanged.
    """
    _assert_safe_slug(slug)  # REQ-18 (Finding C-3)
    names = validate_slug_for_provisioning(slug, domain=settings.domain)
    client = docker.from_env()
    container_name = names.librechat_container

    # Remove stale container if it exists (e.g. failed previous provisioning).
    # Record its image first so a failed replacement can be rolled back.
    old_image_ref: str | None = None
    try:
        old = client.containers.get(container_name)
        if rollback_on_failure:
            # Read the image ref from the container's own inspect payload
            # (already fetched by containers.get -> GET /containers/{id}/json).
            # Do NOT touch `old.image`: that property lazily calls
            # GET /images/{id}/json, which docker-socket-proxy denies by design
            # (IMAGES is deliberately not enabled — see
            # .claude/rules/klai/platform/docker-socket-proxy.md). Reading it
            # raised 403 and aborted the whole fleet rollout on 2026-08-14.
            old_image_ref = (old.attrs.get("Config") or {}).get("Image") or None
        old.remove(force=True)
    except docker.errors.NotFound:  # type: ignore[attr-defined]
        pass

    try:
        _create_and_start_librechat_container(
            client,
            container_name,
            slug,
            env_file_host_path,
            mcp_servers,
            image=settings.librechat_image,
        )
    except Exception:
        if rollback_on_failure and old_image_ref:
            _restore_librechat_container(
                client,
                container_name,
                slug,
                env_file_host_path,
                mcp_servers,
                old_image_ref,
            )
        raise
