"""
Provisioning orchestrator: tenant provisioning workflow and rollback.

Contains the main provision_tenant entry point, _provision workflow,
_rollback compensating actions, and _ProvisionState tracking.
"""

import asyncio
import logging
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.system_groups import create_system_groups
from app.models.portal import PortalOrg
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
            mcp_servers=org.mcp_servers,
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
