"""Tenant provisioning orchestrator — SPEC-PROV-001.

The orchestrator drives a one-level compensating transaction over ~10 forward
steps (Zitadel OIDC, LiteLLM team, MongoDB user, .env file, docs-app KB, portal
KBs, Docker container, tenant Caddyfile, Caddy reload, system groups). Each
successful forward step registers its compensator on a
``contextlib.AsyncExitStack`` and writes a DB checkpoint to
``portal_orgs.provisioning_status`` via :mod:`state_machine`.

On happy path the stack is consumed via ``stack.pop_all()`` so no compensator
runs. On any failure ``AsyncExitStack.__aexit__`` drains the stack in LIFO
order, compensators run best-effort, and the row transitions to
``failed_rollback_complete`` (soft-deleted) or ``failed_rollback_pending``
(rollback itself failed).

Industry-standard Python pattern — already used elsewhere in this codebase
(``main.py`` lifespan, ``database.py`` connection pools). No custom rollback
loop.
"""

import asyncio
import secrets
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, pin_session
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
from app.services.provisioning.state_machine import (
    ENTRY_STATES,
    mark_step_start,
    transition_state,
)
from app.services.secrets import portal_secrets
from app.services.zitadel import zitadel

logger = structlog.get_logger()

# File lock to prevent concurrent tenant caddyfile writes.
_caddy_lock = asyncio.Lock()


@dataclass
class _ProvisionState:
    """Resource handles captured during forward steps for compensator use.

    Each field is populated by the corresponding forward step. Compensators
    read these fields to know what to clean up. Compensators are only
    registered on the ``AsyncExitStack`` after the relevant field has been
    assigned — so if a field is still at its default, its compensator is never
    scheduled.
    """

    slug: str = ""
    zitadel_app_id: str = ""
    litellm_team_id: str = ""
    env_file_path: str = ""
    container_started: bool = False
    caddy_written: bool = False
    mongo_user_created: bool = False
    mongo_user_slug: str = ""


# ---------------------------------------------------------------------------
# Compensators
# ---------------------------------------------------------------------------
#
# Each compensator is idempotent (SEC-021 refactor). They are registered on
# the AsyncExitStack via stack.push_async_callback(...). Signature:
#   async def _compensate_X(state: _ProvisionState) -> None
# Exceptions inside a compensator MUST NOT bubble out — they are logged and
# swallowed so the rest of the stack keeps unwinding (SPEC R10 best-effort
# rollback).


async def _compensate_zitadel_app(state: _ProvisionState) -> None:
    if not state.zitadel_app_id:
        return
    try:
        await zitadel.delete_librechat_oidc_app(state.zitadel_app_id)
    except Exception as exc:
        logger.warning("rollback_zitadel_app_failed", slug=state.slug, error=str(exc), exc_info=True)


async def _compensate_litellm_team(state: _ProvisionState) -> None:
    if not state.litellm_team_id:
        return
    try:
        async with httpx.AsyncClient(
            base_url="http://litellm:4000",
            headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
            timeout=10.0,
        ) as client:
            await client.post("/team/delete", json={"team_ids": [state.litellm_team_id]})
    except Exception as exc:
        logger.warning("rollback_litellm_team_failed", slug=state.slug, error=str(exc), exc_info=True)


async def _compensate_mongo_user(state: _ProvisionState) -> None:
    if not (state.mongo_user_created and state.mongo_user_slug):
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _sync_drop_mongodb_tenant_user, state.mongo_user_slug)
    except Exception as exc:
        logger.warning("rollback_mongodb_user_failed", slug=state.mongo_user_slug, error=str(exc), exc_info=True)


async def _compensate_env_file(state: _ProvisionState) -> None:
    if not state.env_file_path:
        return
    try:
        tenant_dir = Path(state.env_file_path).parent
        shutil.rmtree(str(tenant_dir), ignore_errors=True)
    except Exception as exc:
        logger.warning("rollback_filesystem_failed", slug=state.slug, error=str(exc), exc_info=True)


async def _compensate_container(state: _ProvisionState) -> None:
    if not state.container_started:
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _sync_remove_container, f"librechat-{state.slug}")
    except Exception as exc:
        logger.warning("rollback_container_removal_failed", slug=state.slug, error=str(exc), exc_info=True)


async def _compensate_caddy(state: _ProvisionState) -> None:
    if not state.caddy_written:
        return
    try:
        tenant_file = Path(settings.caddy_tenants_path) / f"{state.slug}.caddyfile"
        tenant_file.unlink(missing_ok=True)
        loop = asyncio.get_running_loop()
        async with _caddy_lock:
            await loop.run_in_executor(None, _reload_caddy)
    except Exception as exc:
        logger.warning("rollback_caddy_failed", slug=state.slug, error=str(exc), exc_info=True)


async def _compensate_personal_kb(state: _ProvisionState) -> None:
    """Best-effort DELETE of the docs-app personal KB.

    If the docs-app is unreachable (network failure during rollback) we log and
    move on — the docs-app run rely on its own reconciliation job. A non-2xx
    from a reachable docs-app is also logged but not re-raised.
    """
    if not state.slug:
        return
    try:
        from app.services import docs_client as docs_api

        await docs_api.deprovision_kb(org_slug=state.slug, kb_slug="personal")
    except Exception as exc:
        logger.warning("rollback_personal_kb_failed", slug=state.slug, error=str(exc), exc_info=True)


# ---------------------------------------------------------------------------
# Orchestrator entry point
# ---------------------------------------------------------------------------


async def provision_tenant(org_id: int) -> None:
    """Full tenant provisioning. Called as a FastAPI BackgroundTask after signup.

    Opens its own DB session (the request session is closed by the time this
    runs). Updates ``portal_orgs`` via the state machine and handles rollback
    via AsyncExitStack.
    """
    async with AsyncSessionLocal() as db:
        await _provision(org_id, db)


async def _provision(org_id: int, db: AsyncSession) -> None:
    # Pin the connection so session-level set_config('app.current_org_id', ...)
    # calls from downstream helpers (ensure_default_knowledge_bases,
    # create_system_groups) stay visible to the INSERTs that follow.
    await pin_session(db)

    # Fetch org. This is a plain SELECT (no lock) — the state machine acquires
    # per-transition FOR UPDATE locks as needed.
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one()

    # Entry guard — `provision_tenant` must only run on fresh signup rows
    # (status=`pending`) or on rows the admin retry endpoint has explicitly
    # reset to `queued`. Any other status means the row is mid-flight,
    # already terminal, or soft-deleted — re-entering the forward sequence
    # would either create duplicate external resources or produce an
    # inconsistent (soft-deleted but `ready`) row. The retry endpoint is
    # the only legitimate path to resurrect a failed row; it clears
    # `deleted_at` and resets the status to `queued` before scheduling
    # this function.
    if org.provisioning_status not in ENTRY_STATES:
        logger.warning(
            "provisioning_skipped_invalid_state",
            org_id=org_id,
            slug=org.slug,
            current_state=org.provisioning_status,
            expected_states=sorted(ENTRY_STATES),
        )
        return
    if org.deleted_at is not None:
        logger.warning(
            "provisioning_skipped_soft_deleted",
            org_id=org_id,
            slug=org.slug,
            deleted_at=org.deleted_at.isoformat() if org.deleted_at else None,
        )
        return

    # Generate a slug, filtering out soft-deleted orgs so a retry after
    # `failed_rollback_complete` can reclaim the original slug (SPEC-PROV-001
    # M1 partial unique index `ix_portal_orgs_slug_active`).
    slugs_result = await db.execute(select(PortalOrg.slug).where(PortalOrg.deleted_at.is_(None)))
    existing_slugs = {row[0] for row in slugs_result.fetchall() if row[0]}
    slug = _slugify_unique(org.name, existing_slugs)

    # Capture mcp_servers now — ensure_default_knowledge_bases commits the
    # session, which expires all ORM attributes. Accessing org.mcp_servers
    # after that triggers a synchronous lazy-load in an async context →
    # MissingGreenlet crash.
    mcp_servers = org.mcp_servers
    zitadel_org_id = org.zitadel_org_id
    logger.info("provisioning_tenant_start", slug=slug, org_id=org_id)

    state = _ProvisionState(slug=slug)
    last_state: str | None = None

    try:
        async with AsyncExitStack() as stack:
            # --- queued: initial checkpoint. Accepts pending OR queued. -----
            # The entry guard above has already verified the row is in one of
            # these states; we pass ENTRY_STATES to `transition_state` so the
            # precondition is re-checked inside the row-level lock — a belt
            # and braces defence against a concurrent writer that may have
            # changed the row between the guard's unlocked SELECT and this
            # lock acquisition.
            mark_step_start(org_id, "begin")
            await transition_state(db, org_id, from_state=ENTRY_STATES, to_state="queued", step="begin")
            last_state = "queued"

            # --- step 1: Zitadel OIDC app ---------------------------------
            mark_step_start(org_id, "zitadel_oidc_app")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="creating_zitadel_app",
                step="zitadel_oidc_app",
            )
            last_state = "creating_zitadel_app"

            redirect_uri = f"https://chat-{slug}.{settings.domain}/oauth/openid/callback"
            oidc_data = await zitadel.create_librechat_oidc_app(slug, redirect_uri)
            client_id = oidc_data.get("clientId", "")
            client_secret = oidc_data.get("clientSecret", "")
            state.zitadel_app_id = oidc_data.get("appId", "")
            stack.push_async_callback(_compensate_zitadel_app, state)
            logger.info("zitadel_oidc_app_created", slug=slug, client_id=client_id)

            # --- step 2: LiteLLM team -------------------------------------
            mark_step_start(org_id, "litellm_team")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="creating_litellm_team",
                step="litellm_team",
            )
            last_state = "creating_litellm_team"

            async with httpx.AsyncClient(
                base_url="http://litellm:4000",
                headers={
                    "Authorization": f"Bearer {settings.litellm_master_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            ) as llm_client:
                team_resp = await llm_client.post("/team/new", json={"team_alias": slug})
                team_resp.raise_for_status()
                team_id = team_resp.json()["team_id"]
                state.litellm_team_id = team_id
                stack.push_async_callback(_compensate_litellm_team, state)
                logger.info("litellm_team_created", slug=slug, team_id=team_id)

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

            # --- step 3: MongoDB tenant user ------------------------------
            mark_step_start(org_id, "mongo_user")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="creating_mongo_user",
                step="mongo_user",
            )
            last_state = "creating_mongo_user"

            mongo_tenant_password = secrets.token_hex(24)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _create_mongodb_tenant_user, slug, mongo_tenant_password)
            state.mongo_user_created = True
            state.mongo_user_slug = slug
            stack.push_async_callback(_compensate_mongo_user, state)
            logger.info("mongodb_user_created", slug=slug)

            # --- step 4: .env file ----------------------------------------
            mark_step_start(org_id, "env_file")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="writing_env_file",
                step="env_file",
            )
            last_state = "writing_env_file"

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
            stack.push_async_callback(_compensate_env_file, state)
            env_file_host_path = f"{settings.librechat_host_data_path}/{slug}/.env"
            logger.info("librechat_env_written", slug=slug)

            # --- step 5: personal docs KB (soft dependency) ---------------
            mark_step_start(org_id, "personal_kb")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="creating_personal_kb",
                step="personal_kb",
            )
            last_state = "creating_personal_kb"

            from app.services import docs_client as docs_api

            personal_kb_created = False
            try:
                await docs_api.provision_gitea_repo(
                    org_slug=slug,
                    kb_name="Personal",
                    kb_slug="personal",
                    visibility="internal",
                )
                personal_kb_created = True
                logger.info("personal_kb_created", slug=slug)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
                # R13: docs-app unreachable → soft success, log and continue.
                logger.exception(
                    "docs_kb_creation_degraded_docs_app_unreachable",
                    slug=slug,
                    org_id=org_id,
                )
            if personal_kb_created:
                stack.push_async_callback(_compensate_personal_kb, state)

            # --- step 6: portal KBs (internal DB, rolls with session) -----
            mark_step_start(org_id, "portal_kbs")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="creating_portal_kbs",
                step="portal_kbs",
            )
            last_state = "creating_portal_kbs"

            from app.services.default_knowledge_bases import ensure_default_knowledge_bases

            first_user_result = await db.execute(
                select(PortalUser.zitadel_user_id).where(PortalUser.org_id == org.id).limit(1)
            )
            first_user_id = first_user_result.scalar_one_or_none() or "system"
            await ensure_default_knowledge_bases(org.id, first_user_id, db)

            # --- step 7: Docker container ---------------------------------
            mark_step_start(org_id, "librechat_container")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="starting_container",
                step="librechat_container",
            )
            last_state = "starting_container"

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _start_librechat_container, slug, env_file_host_path, mcp_servers)
            state.container_started = True
            stack.push_async_callback(_compensate_container, state)
            logger.info("librechat_container_started", slug=slug)

            # --- step 8: tenant Caddyfile ---------------------------------
            mark_step_start(org_id, "tenant_caddyfile")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="writing_caddyfile",
                step="tenant_caddyfile",
            )
            last_state = "writing_caddyfile"

            async with _caddy_lock:
                _write_tenant_caddyfile(slug)
            state.caddy_written = True
            stack.push_async_callback(_compensate_caddy, state)
            logger.info("tenant_caddyfile_written", slug=slug)

            # --- step 9: Caddy reload -------------------------------------
            mark_step_start(org_id, "caddy_reload")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="reloading_caddy",
                step="caddy_reload",
            )
            last_state = "reloading_caddy"

            async with _caddy_lock:
                await loop.run_in_executor(None, _reload_caddy)
            logger.info("caddy_reloaded", slug=slug)

            # --- step 10: system groups (internal DB) ---------------------
            mark_step_start(org_id, "system_groups")
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="creating_system_groups",
                step="system_groups",
            )
            last_state = "creating_system_groups"

            await create_system_groups(org_id, db)
            logger.info("system_groups_created", slug=slug)

            # --- finalize: persist external IDs + ready -------------------
            mark_step_start(org_id, "ready")
            # Finalize org fields — done in-row before the ready transition so the
            # state flip is the observable marker that everything is wired up.
            org.slug = slug
            org.librechat_container = f"librechat-{slug}"
            org.zitadel_librechat_client_id = client_id
            org.zitadel_librechat_client_secret = portal_secrets.encrypt(client_secret)
            org.litellm_team_key = portal_secrets.encrypt(litellm_team_key) if litellm_team_key else None
            await transition_state(
                db,
                org_id,
                from_state=last_state,
                to_state="ready",
                step="ready",
            )
            logger.info("provisioning_complete", slug=slug)

            # Happy path: drain the stack without running compensators.
            stack.pop_all()

    except Exception:
        logger.exception("provisioning_failed", org_id=org_id, last_forward_state=last_state)
        # AsyncExitStack.__aexit__ has already drained compensators in LIFO
        # order. What remains is to mark the row: either failed_rollback_complete
        # (compensators all succeeded — we have no reliable way to know that
        # individual compensators failed because they swallow their exceptions,
        # so we optimistically mark complete and leave failed_rollback_pending
        # for cases where a subsequent step itself raised during finalisation).
        try:
            await _finalize_failure(db, org_id, last_forward_state=last_state)
        except Exception:
            logger.exception(
                "failed_status_persist_error",
                org_id=org_id,
                last_forward_state=last_state,
            )
        raise


async def _finalize_failure(
    db: AsyncSession,
    org_id: int,
    *,
    last_forward_state: str | None,
) -> None:
    """Write the terminal failure state after compensators have drained.

    Transitions the row to ``failed_rollback_complete`` and sets
    ``deleted_at`` so the slug is released via the partial unique index. If
    a state-transition write itself fails (e.g. DB still down), the
    exception propagates to the caller which logs
    ``failed_status_persist_error``.

    ``last_forward_state`` is ``None`` iff the failure occurred BEFORE the
    first forward transition committed — in that case the row is still in
    one of ``ENTRY_STATES`` (``pending``/``queued``) and we accept any of
    those as the from-state.
    """
    # First checkpoint the rollback as "pending" so observers see the intent
    # even if the next step fails. Accept either the last forward state
    # (when we know it) or any entry state (when the failure happened before
    # we ever transitioned out of `pending`/`queued`).
    rollback_from: str | frozenset[str]
    rollback_from = last_forward_state if last_forward_state is not None else ENTRY_STATES
    await transition_state(
        db,
        org_id,
        from_state=rollback_from,
        to_state="failed_rollback_pending",
        step="rollback_start",
    )

    # Second checkpoint: compensators ran (best-effort), mark complete and
    # soft-delete.
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id).with_for_update())
    org = result.scalar_one_or_none()
    if org is None:
        return
    org.deleted_at = func.now()
    await db.commit()

    await transition_state(
        db,
        org_id,
        from_state="failed_rollback_pending",
        to_state="failed_rollback_complete",
        step="rollback_complete",
    )
