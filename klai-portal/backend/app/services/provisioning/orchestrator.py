"""
Provisioning orchestrator: tenant provisioning workflow and rollback.

Contains the main provision_tenant entry point, _provision workflow,
_rollback compensating actions, and _ProvisionState tracking.
"""

import asyncio
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.system_groups import create_system_groups
from app.models.portal import PortalOrg, PortalUser
from app.services.provisioning.generators import _generate_librechat_env, _slugify_unique
from app.services.provisioning.infrastructure import (
    _create_mongodb_tenant_user,
    _reload_caddy,
    _start_librechat_container,
    _sync_drop_mongodb_tenant_user,
    _sync_remove_container,
    _write_tenant_caddyfile,
)
from app.services.secrets import portal_secrets
from app.services.zitadel import zitadel

logger = structlog.get_logger()

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
            logger.warning("rollback_caddy_failed", slug=state.slug, error=str(exc))

    if state.container_started:
        try:
            await loop.run_in_executor(None, _sync_remove_container, f"librechat-{state.slug}")
        except Exception as exc:
            logger.warning("rollback_container_removal_failed", slug=state.slug, error=str(exc))

    if state.env_file_path:
        try:
            tenant_dir = Path(state.env_file_path).parent
            shutil.rmtree(str(tenant_dir), ignore_errors=True)
        except Exception as exc:
            logger.warning("rollback_filesystem_failed", slug=state.slug, error=str(exc))

    if state.mongo_user_created and state.mongo_user_slug:
        try:
            await loop.run_in_executor(None, _sync_drop_mongodb_tenant_user, state.mongo_user_slug)
        except Exception as exc:
            logger.warning("rollback_mongodb_user_failed", slug=state.mongo_user_slug, error=str(exc))

    if state.litellm_team_id:
        try:
            async with httpx.AsyncClient(
                base_url="http://litellm:4000",
                headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                timeout=10.0,
            ) as client:
                await client.post("/team/delete", json={"team_ids": [state.litellm_team_id]})
        except Exception as exc:
            logger.warning("rollback_litellm_team_failed", slug=state.slug, error=str(exc))

    if state.zitadel_app_id:
        try:
            await zitadel.delete_librechat_oidc_app(state.zitadel_app_id)
        except Exception as exc:
            logger.warning("rollback_zitadel_app_failed", slug=state.slug, error=str(exc))


async def provision_tenant(org_id: int) -> None:
    """
    Full tenant provisioning. Called as a BackgroundTask after signup.
    Opens its own DB session (the request session is closed by the time this runs).
    Updates the PortalOrg row with provisioning results.
    """
    async with AsyncSessionLocal() as db:
        await _provision(org_id, db)


async def _provision(org_id: int, db: AsyncSession) -> None:
    # Pin the DB connection so session-level set_config('app.current_org_id', ...)
    # calls made by downstream helpers (ensure_default_knowledge_bases,
    # create_system_groups) stay visible to the INSERTs that follow. Without
    # pinning, SQLAlchemy async lazily checks out a fresh pooled connection per
    # statement, the SET lands on one connection and the INSERT lands on
    # another, and RLS (`org_id = current_setting('app.current_org_id')`)
    # silently blocks every row. See app.core.database.get_db for the same
    # pattern applied to request sessions.
    await db.connection()

    # Fetch org
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one()

    # Get existing slugs to ensure uniqueness
    slugs_result = await db.execute(select(PortalOrg.slug))
    existing_slugs = {row[0] for row in slugs_result.fetchall() if row[0]}

    slug = _slugify_unique(org.name, existing_slugs)
    # Capture mcp_servers now — ensure_default_knowledge_bases (step 6b) commits the session,
    # which expires all ORM attributes. Accessing org.mcp_servers after that triggers a
    # synchronous lazy-load in an async context → MissingGreenlet crash.
    mcp_servers = org.mcp_servers
    zitadel_org_id = org.zitadel_org_id
    logger.info("provisioning_tenant_start", slug=slug, org_id=org_id)

    state = _ProvisionState(slug=slug)

    try:
        # Step 1: Zitadel OIDC app for LibreChat
        redirect_uri = f"https://chat-{slug}.{settings.domain}/oauth/openid/callback"
        oidc_data = await zitadel.create_librechat_oidc_app(slug, redirect_uri)
        client_id = oidc_data.get("clientId", "")
        client_secret = oidc_data.get("clientSecret", "")
        state.zitadel_app_id = oidc_data.get("appId", "")
        logger.info("zitadel_oidc_app_created", slug=slug, client_id=client_id)

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
            logger.info("litellm_team_created", slug=slug, team_id=team_id)

            # Step 2b: Generate a team key with org_id metadata
            key_resp = await llm_client.post(
                "/key/generate",
                json={
                    "team_id": team_id,
                    "metadata": {"org_id": zitadel_org_id},
                    "models": ["klai-llm", "klai-fallback"],
                },
            )
            key_resp.raise_for_status()
            litellm_team_key: str = key_resp.json()["key"]
            logger.info("litellm_team_key_created", slug=slug)

        # Step 3: (was: add per-tenant redirect URI to the old SPA portal app)
        # Removed in SPEC-AUTH-008 Phase C — the BFF confidential portal app uses
        # a single redirect_uri (my.getklai.com/api/auth/oidc/callback) for every
        # tenant, so no per-tenant OIDC bookkeeping is needed at provisioning.

        # Step 4: Create per-tenant MongoDB user (isolated credentials)
        mongo_tenant_password = secrets.token_hex(24)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _create_mongodb_tenant_user, slug, mongo_tenant_password)
        state.mongo_user_created = True
        state.mongo_user_slug = slug
        logger.info("mongodb_user_created", slug=slug)

        # Step 5: Write LibreChat .env file
        env_content = _generate_librechat_env(
            slug,
            client_id,
            client_secret,
            litellm_api_key=litellm_team_key,
            mongo_password=mongo_tenant_password,
            zitadel_org_id=zitadel_org_id,
            mcp_servers=mcp_servers,
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
        logger.info("librechat_env_written", slug=slug)

        # Step 6: Create personal KB via klai-docs API
        # Fail-loud: if docs-app is unreachable or returns non-2xx, abort provisioning.
        # Rationale: the docs KB is a first-class tenant resource; silently skipping
        # it leaves the tenant with a broken "My knowledge" view.
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
            logger.info("personal_kb_created", slug=slug)

        # Step 6b: Create default portal KB rows (org KB + admin's personal KB).
        # Fail-loud: exceptions bubble to the outer handler which rolls back external
        # resources and marks provisioning_status='failed' so the admin UI sees it.
        from app.services.default_knowledge_bases import ensure_default_knowledge_bases

        # Use the creator user_id from the first admin — looked up from signup caller
        first_user_result = await db.execute(
            select(PortalUser.zitadel_user_id).where(PortalUser.org_id == org.id).limit(1)
        )
        first_user_id = first_user_result.scalar_one_or_none() or "system"
        await ensure_default_knowledge_bases(org.id, first_user_id, db)

        # Step 7: Start Docker container
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _start_librechat_container, slug, env_file_host_path, mcp_servers)
        state.container_started = True
        logger.info("librechat_container_started", slug=slug)

        # Step 8: Write per-tenant Caddyfile and reload Caddy
        async with _caddy_lock:
            _write_tenant_caddyfile(slug)
            await loop.run_in_executor(None, _reload_caddy)
        state.caddy_written = True
        logger.info("caddy_reloaded", slug=slug)

        # Step 9: Create system groups (moved ahead of the ready-commit so a
        # failure here aborts provisioning instead of landing a tenant in
        # provisioning_status='ready' without its Admin / Chat / Scribe /
        # Knowledge groups). Fail-loud — no try/except swallow.
        await create_system_groups(org_id, db)
        logger.info("system_groups_created", slug=slug)

        # Step 10: Finalize org state
        org.slug = slug
        org.librechat_container = f"librechat-{slug}"
        org.zitadel_librechat_client_id = client_id
        org.zitadel_librechat_client_secret = portal_secrets.encrypt(client_secret)
        org.litellm_team_key = portal_secrets.encrypt(litellm_team_key) if litellm_team_key else None
        org.provisioning_status = "ready"
        await db.commit()
        logger.info("provisioning_complete", slug=slug)

    except Exception:
        logger.exception("provisioning_failed", org_id=org_id)
        await _rollback(state)
        try:
            org.provisioning_status = "failed"
            await db.commit()
        except Exception:
            # Can't persist status='failed' — log with full traceback so the
            # stuck 'provisioning' row is visible in VictoriaLogs. The original
            # provisioning exception still bubbles up via the raise below.
            logger.exception("failed_status_persist_error", org_id=org_id)
        raise
