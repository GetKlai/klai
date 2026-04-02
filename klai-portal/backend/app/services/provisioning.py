"""
Tenant provisioning service.
Called after signup DB commit to set up a new customer's LibreChat instance.
"""

import asyncio
import copy
import logging
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import docker
import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.system_groups import create_system_groups
from app.models.portal import PortalOrg
from app.services.secrets import portal_secrets
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

# File lock to prevent concurrent tenant caddyfile writes
_caddy_lock = asyncio.Lock()


@dataclass
class _ProvisionState:
    """Tracks provisioning artefacts for rollback on partial failure."""

    slug: str = ""
    zitadel_app_id: str = ""  # set after Zitadel OIDC app created
    litellm_team_id: str = ""  # set after LiteLLM team created
    env_file_path: str = ""  # set after .env written (container path)
    container_started: bool = False
    caddy_written: bool = False
    mongo_user_created: bool = False
    mongo_user_slug: str = ""


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
    mongodb_container = getattr(settings, "mongodb_container_name", "mongodb")
    container = c.containers.get(mongodb_container)
    container.exec_run(
        [
            "mongosh",
            "--quiet",
            "-u",
            "root",
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


async def _rollback(state: _ProvisionState) -> None:
    """Best-effort cleanup of partial provisioning state."""
    loop = asyncio.get_running_loop()

    if state.caddy_written:
        try:
            tenant_file = Path(settings.caddy_tenants_path) / f"{state.slug}.caddyfile"
            tenant_file.unlink(missing_ok=True)
            async with _caddy_lock:
                await loop.run_in_executor(None, _reload_caddy)
        except Exception as exc:
            logger.warning("Rollback: Caddy cleanup failed for %s: %s", state.slug, exc)

    if state.container_started:
        try:
            await loop.run_in_executor(None, _sync_remove_container, f"librechat-{state.slug}")
        except Exception as exc:
            logger.warning("Rollback: container removal failed for %s: %s", state.slug, exc)

    if state.env_file_path:
        try:
            tenant_dir = Path(state.env_file_path).parent
            shutil.rmtree(str(tenant_dir), ignore_errors=True)
        except Exception as exc:
            logger.warning("Rollback: filesystem cleanup failed for %s: %s", state.slug, exc)

    if state.mongo_user_created and state.mongo_user_slug:
        try:
            await loop.run_in_executor(None, _sync_drop_mongodb_tenant_user, state.mongo_user_slug)
        except Exception as exc:
            logger.warning("Rollback: MongoDB user deletion failed for %s: %s", state.mongo_user_slug, exc)

    if state.litellm_team_id:
        try:
            async with httpx.AsyncClient(
                base_url="http://litellm:4000",
                headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                timeout=10.0,
            ) as client:
                await client.post("/team/delete", json={"team_ids": [state.litellm_team_id]})
        except Exception as exc:
            logger.warning("Rollback: LiteLLM team deletion failed for %s: %s", state.slug, exc)

    if state.zitadel_app_id:
        try:
            await zitadel.delete_librechat_oidc_app(state.zitadel_app_id)
        except Exception as exc:
            logger.warning("Rollback: Zitadel OIDC app deletion failed for %s: %s", state.slug, exc)


def _slugify_unique(name: str, existing_slugs: set[str]) -> str:
    """Generate a unique slug from org name."""
    import re
    import unicodedata

    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9\s-]", "", n)
    n = re.sub(r"\s+", "-", n).strip("-").lower()
    n = n[:50] or "org"
    slug = n
    counter = 2
    while slug in existing_slugs:
        slug = f"{n}-{counter}"
        counter += 1
    return slug


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
    mongodb_container = getattr(settings, "mongodb_container_name", "mongodb")
    container = client.containers.get(mongodb_container)
    exit_code, output = container.exec_run(
        [
            "mongosh",
            "--quiet",
            "-u",
            "root",
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


def _generate_librechat_yaml(
    base_path: Path,
    extra_mcp_servers: dict | None,
) -> str:
    """Generate a per-tenant librechat.yaml by merging base config with extra MCP servers.

    Returns the YAML string. The base config is loaded from disk and deep-copied
    so the original is never mutated.
    """
    with open(base_path) as f:
        config = yaml.safe_load(f)

    if extra_mcp_servers:
        config = copy.deepcopy(config)
        mcp = config.setdefault("mcpServers", {})
        mcp.update(extra_mcp_servers)

        # Add extra server names to modelSpecs.list[].mcpServers
        extra_names = list(extra_mcp_servers.keys())
        for spec in config.get("modelSpecs", {}).get("list", []):
            existing = spec.get("mcpServers", [])
            spec["mcpServers"] = existing + extra_names

    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def _generate_librechat_env(
    slug: str,
    client_id: str,
    client_secret: str,
    litellm_api_key: str,
    mongo_password: str,
    zitadel_org_id: str = "",
) -> str:
    """Generate the per-tenant LibreChat .env file content."""
    domain = settings.domain
    jwt_secret = secrets.token_hex(32)
    jwt_refresh_secret = secrets.token_hex(32)
    session_secret = secrets.token_hex(32)
    creds_key = secrets.token_hex(32)
    creds_iv = secrets.token_hex(8)

    return f"""# Auto-generated by portal-api at provisioning. Do not edit manually.
# Tenant: {slug}

# MongoDB -- tenant-isolated database (per-tenant user, NOT the shared admin)
MONGO_URI=mongodb://librechat-{slug}:{mongo_password}@mongodb:27017/librechat-{slug}?authSource=librechat-{slug}

# Zitadel OIDC
OPENID_ISSUER=https://auth.{domain}
OPENID_CLIENT_ID={client_id}
OPENID_CLIENT_SECRET={client_secret}
OPENID_SCOPE=openid profile email
OPENID_CALLBACK_URL=/oauth/openid/callback
OPENID_USERNAME_CLAIM=preferred_username
OPENID_REUSE_TOKENS=false
OPENID_USE_END_SESSION_ENDPOINT=true
ALLOW_EMAIL_LOGIN=false
ALLOW_REGISTRATION=false
ALLOW_SOCIAL_LOGIN=true
ALLOW_SOCIAL_REGISTRATION=false

# Login rate limiting -- raised from the default (7) because LibreChat's
# loginLimiter counts every OAuth route hit (redirect + callback = 2 per OIDC
# cycle).  With ALLOW_EMAIL_LOGIN=false there is no brute-force vector at this
# layer; credential protection is handled by Zitadel's lockout policy and
# Caddy's per-IP rate_limit block.  250 allows ~125 OIDC cycles per window.
# NOTE: express-rate-limit v7+ blocks all requests when max=0 (not "unlimited").
LOGIN_MAX=250

# App settings
DOMAIN_CLIENT=https://chat-{slug}.{domain}
DOMAIN_SERVER=https://chat-{slug}.{domain}
APP_TITLE=Klai Chat
ALLOW_IFRAME=true

# Session secrets
JWT_SECRET={jwt_secret}
JWT_REFRESH_SECRET={jwt_refresh_secret}
OPENID_SESSION_SECRET={session_secret}
CREDS_KEY={creds_key}
CREDS_IV={creds_iv}

# Session lifetime -- access token: 7 days, refresh token: 30 days.
# Ensures users stay logged in across weekends and container restarts without
# needing to re-enter password + 2FA. Zitadel session should be configured
# to at least 30 days to match (auth.getklai.com → Login Policy → Session Lifetime).
SESSION_EXPIRY=604800000
REFRESH_TOKEN_EXPIRY=2592000000

# Search
MEILI_HOST=http://meilisearch:7700
MEILI_MASTER_KEY={settings.meili_master_key}

# Redis (session persistence across container restarts)
REDIS_URI=redis://:{settings.redis_password}@redis:6379

# AI routing via LiteLLM
LITELLM_API_KEY={litellm_api_key}

# Web search (shared services on klai-net)
SEARXNG_INSTANCE_URL=http://searxng:8080
FIRECRAWL_API_KEY={settings.firecrawl_internal_key}
FIRECRAWL_API_URL=http://firecrawl-api:3002
JINA_API_KEY=klai-internal
JINA_API_URL=http://infinity-reranker:7997/v1/rerank

# Klai Knowledge MCP identity (used by librechat.yaml ${{...}} expansion)
KLAI_ZITADEL_ORG_ID={zitadel_org_id}
KLAI_ORG_SLUG={slug}
KNOWLEDGE_INGEST_SECRET={settings.knowledge_ingest_secret}
"""


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
    """Reload Caddy config gracefully (no connection drops).

    Uses docker exec instead of container restart, which is safer and faster.
    docker-socket-proxy allows exec on CONTAINERS.
    """
    client = docker.from_env()
    caddy = client.containers.get(settings.caddy_container_name)
    exit_code, output = caddy.exec_run(
        ["caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
        stdout=True,
        stderr=True,
    )
    if exit_code != 0:
        raise RuntimeError(f"Caddy reload failed (exit {exit_code}): {output.decode()}")


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


async def provision_tenant(org_id: int) -> None:
    """
    Full tenant provisioning. Called as a BackgroundTask after signup.
    Opens its own DB session (the request session is closed by the time this runs).
    Updates the PortalOrg row with provisioning results.
    """
    async with AsyncSessionLocal() as db:
        await _provision(org_id, db)


async def _provision(org_id: int, db: AsyncSession) -> None:
    # Fetch org
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one()

    # Get existing slugs to ensure uniqueness
    slugs_result = await db.execute(select(PortalOrg.slug))
    existing_slugs = {row[0] for row in slugs_result.fetchall() if row[0]}

    slug = _slugify_unique(org.name, existing_slugs)
    logger.info("Provisioning tenant %s (org_id=%d)", slug, org_id)

    state = _ProvisionState(slug=slug)

    try:
        # Step 1: Zitadel OIDC app for LibreChat
        redirect_uri = f"https://chat-{slug}.{settings.domain}/oauth/openid/callback"
        oidc_data = await zitadel.create_librechat_oidc_app(slug, redirect_uri)
        client_id = oidc_data.get("clientId", "")
        client_secret = oidc_data.get("clientSecret", "")
        state.zitadel_app_id = oidc_data.get("appId", "")
        logger.info("Created Zitadel OIDC app for %s: %s", slug, client_id)

        # Step 2: Create LiteLLM team key for the tenant
        async with httpx.AsyncClient(
            base_url="http://litellm:4000",
            headers={
                "Authorization": f"Bearer {settings.litellm_master_key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        ) as llm_client:
            # Step 2a: Create a LiteLLM team
            team_resp = await llm_client.post("/team/new", json={"team_alias": slug})
            team_resp.raise_for_status()
            team_id = team_resp.json()["team_id"]
            state.litellm_team_id = team_id
            logger.info("Created LiteLLM team for %s: %s", slug, team_id)

            # Step 2b: Generate a team key with org_id metadata
            key_resp = await llm_client.post(
                "/key/generate",
                json={
                    "team_id": team_id,
                    "metadata": {"org_id": org.zitadel_org_id},
                    "models": ["klai-llm", "klai-fallback"],
                },
            )
            key_resp.raise_for_status()
            litellm_team_key: str = key_resp.json()["key"]
            logger.info("Created LiteLLM team key for %s", slug)

        # Step 3: Add portal redirect URI for this tenant
        try:
            await zitadel.add_portal_redirect_uri(slug)
        except Exception as exc:
            logger.warning("Could not add portal redirect URI for %s: %s", slug, exc)

        # Step 4: Create per-tenant MongoDB user (isolated credentials)
        mongo_tenant_password = secrets.token_hex(24)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _create_mongodb_tenant_user, slug, mongo_tenant_password)
        state.mongo_user_created = True
        state.mongo_user_slug = slug
        logger.info("Created MongoDB user for %s", slug)

        # Step 5: Write LibreChat .env file
        env_content = _generate_librechat_env(
            slug,
            client_id,
            client_secret,
            litellm_api_key=litellm_team_key,
            mongo_password=mongo_tenant_password,
            zitadel_org_id=org.zitadel_org_id,
        )
        container_data_base = Path(settings.librechat_container_data_path)
        tenant_dir = container_data_base / slug
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / "images").mkdir(exist_ok=True)
        env_file_container_path = tenant_dir / ".env"
        env_file_container_path.write_text(env_content)
        state.env_file_path = str(env_file_container_path)
        # Host path for Docker volume mount
        env_file_host_path = f"{settings.librechat_host_data_path}/{slug}/.env"
        logger.info("Wrote LibreChat .env for %s", slug)

        # Step 6: Create personal KB via klai-docs API
        try:
            async with httpx.AsyncClient(
                base_url="http://docs-app:3000",
                headers={
                    "X-Internal-Secret": settings.docs_internal_secret,
                    "X-User-ID": "system",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            ) as docs_client:
                kb_resp = await docs_client.post(
                    f"/api/orgs/{slug}/kbs",
                    json={"name": "Personal", "slug": "personal", "visibility": "private"},
                )
                kb_resp.raise_for_status()
                logger.info("Created personal KB for %s", slug)
        except Exception as exc:
            logger.warning("Could not create personal KB for %s: %s", slug, exc)

        # Step 7: Start Docker container
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _start_librechat_container, slug, env_file_host_path, org.mcp_servers)
        state.container_started = True
        logger.info("Started container librechat-%s", slug)

        # Step 8: Write per-tenant Caddyfile and reload Caddy
        async with _caddy_lock:
            _write_tenant_caddyfile(slug)
            await loop.run_in_executor(None, _reload_caddy)
        state.caddy_written = True
        logger.info("Caddy reloaded for %s", slug)

        # Step 9: Update DB
        org.slug = slug
        org.librechat_container = f"librechat-{slug}"
        org.zitadel_librechat_client_id = client_id
        org.zitadel_librechat_client_secret = portal_secrets.encrypt(client_secret)
        org.litellm_team_key = portal_secrets.encrypt(litellm_team_key) if litellm_team_key else None
        org.provisioning_status = "ready"
        await db.commit()
        logger.info("Tenant %s provisioning complete", slug)

        # Step 10: Create system groups
        try:
            await create_system_groups(org.id, db)
            logger.info("Created system groups for %s", slug)
        except Exception as exc:
            logger.warning("Could not create system groups for %s: %s", slug, exc)

    except Exception as exc:
        logger.exception("Provisioning failed for org_id=%d: %s", org_id, exc)
        await _rollback(state)
        try:
            org.provisioning_status = "failed"
            await db.commit()
        except Exception as db_exc:
            logger.warning("Could not persist failed status for org_id=%d: %s", org_id, db_exc)
        raise
