"""
Tenant deprovisioning steps — SPEC-INFRA-TENANT-DELETE-001.

16 idempotent step-functions that are called in order by the deprovisioning
orchestrator. Each step accepts a ``_DeprovisionState`` dataclass (defined in
``deprovisioning_orchestrator.py``) and returns None on success.

All steps are idempotent: if a resource is already absent the step logs and
returns without raising. This enables safe retry from the beginning on
``failed_deprovisioning`` rows.

# @MX:NOTE: idempotent — al-weg = geen exception. SPEC-INFRA-TENANT-DELETE-001 R3.
# @MX:SPEC: SPEC-INFRA-TENANT-DELETE-001
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog
from sqlalchemy import text

from app.core.config import settings
from app.services.provisioning.infrastructure import (
    _caddy_lock,
    _reload_caddy,
    _sync_drop_mongodb_tenant_database,
    _sync_drop_mongodb_tenant_user,
    _sync_remove_container,
)

if TYPE_CHECKING:
    from app.services.provisioning.deprovisioning_orchestrator import _DeprovisionState

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Step 0 — mark_deprovisioning
# ---------------------------------------------------------------------------


async def _mark_deprovisioning(state: _DeprovisionState) -> None:
    """UPDATE provisioning_status='deprovisioning' + invalidate slug cache.

    Accepts both DEPROVISION_ENTRY_STATES and 'deprovisioning' as from_state.
    The endpoint transitions the org to 'deprovisioning' before scheduling the
    background task, so by the time this step runs the org may already be in
    that state (idempotent design for retry compatibility).

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    from app.api.auth import invalidate_tenant_slug_cache
    from app.services.provisioning.state_machine import DEPROVISION_ENTRY_STATES, transition_state

    # Include 'deprovisioning' itself: on admin retry the endpoint sets the state
    # to 'deprovisioning' before calling deprovision_tenant(), so when step 0
    # runs again the status is already 'deprovisioning'. Step is idempotent.
    allowed_from = frozenset({*DEPROVISION_ENTRY_STATES, "deprovisioning"})
    await transition_state(
        state.db,
        state.org_id,
        from_state=allowed_from,
        to_state="deprovisioning",
        step="mark_deprovisioning",
    )
    invalidate_tenant_slug_cache()
    logger.info("deprovisioning_status_set", org_id=state.org_id, slug=state.slug)


# ---------------------------------------------------------------------------
# Step 1 — delete_caddy_upstream
# ---------------------------------------------------------------------------


async def _delete_caddy_upstream(state: _DeprovisionState) -> None:
    """Remove tenant.caddyfile and reload Caddy.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    tenant_file = Path(settings.caddy_tenants_path) / f"{state.slug}.caddyfile"
    tenant_file.unlink(missing_ok=True)
    loop = asyncio.get_running_loop()
    async with _caddy_lock:
        await loop.run_in_executor(None, _reload_caddy)
    logger.info("caddy_upstream_deleted", slug=state.slug)


# ---------------------------------------------------------------------------
# Step 2 — delete_librechat_container
# ---------------------------------------------------------------------------


async def _delete_librechat_container(state: _DeprovisionState) -> None:
    """docker rm -f librechat-{slug}.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    container_name = f"librechat-{state.slug}"
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_remove_container, container_name)
    logger.info("librechat_container_removed", slug=state.slug)


# ---------------------------------------------------------------------------
# Step 3 — delete_librechat_filesystem
# ---------------------------------------------------------------------------


async def _delete_librechat_filesystem(state: _DeprovisionState) -> None:
    """rm -rf /opt/klai/librechat/{slug}/.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    tenant_dir = Path(settings.librechat_container_data_path) / state.slug
    shutil.rmtree(str(tenant_dir), ignore_errors=True)
    logger.info("librechat_filesystem_removed", slug=state.slug, path=str(tenant_dir))


# ---------------------------------------------------------------------------
# Step 4 — drop_mongodb_database
# ---------------------------------------------------------------------------


async def _drop_mongodb_database(state: _DeprovisionState) -> None:
    """db.dropDatabase() for librechat-{slug}.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_drop_mongodb_tenant_database, state.slug)


# ---------------------------------------------------------------------------
# Step 5 — drop_mongodb_user
# ---------------------------------------------------------------------------


async def _drop_mongodb_user(state: _DeprovisionState) -> None:
    """Drop the MongoDB user for the tenant.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_drop_mongodb_tenant_user, state.slug)


# ---------------------------------------------------------------------------
# Step 6 — delete_meilisearch_index
# ---------------------------------------------------------------------------


async def _delete_meilisearch_index(state: _DeprovisionState) -> None:
    """DELETE /indexes/{slug} via Meilisearch API. Idempotent on 404.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    meili_url = "http://meilisearch:7700"
    meili_key = settings.meili_master_key
    async with httpx.AsyncClient(
        base_url=meili_url,
        headers={"Authorization": f"Bearer {meili_key}"},
        timeout=15.0,
    ) as client:
        resp = await client.delete(f"/indexes/{state.slug}")
        if resp.status_code == 404:
            logger.info("meilisearch_index_already_absent", slug=state.slug)
            return
        resp.raise_for_status()
    logger.info("meilisearch_index_deleted", slug=state.slug)


# ---------------------------------------------------------------------------
# Step 7 — flush_redis_tenant_keys
# ---------------------------------------------------------------------------


async def _flush_redis_tenant_keys(state: _DeprovisionState) -> None:
    """SCAN MATCH configs:{slug}:* + UNLINK all matching keys.

    Uses the synchronous redis client wrapped in asyncio.to_thread so the
    SCAN loop does not block the event loop. The sync client is intentional —
    it mirrors infrastructure._flush_redis_and_restart_librechat which lives
    in the same module-namespace; switching to redis.asyncio here would create
    two divergent connection patterns for the same Redis instance.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    # @MX:WARN: never call the sync redis SCAN loop directly inside this async
    #   function — wrap in asyncio.to_thread to avoid event-loop stalls of
    #   >100ms for tenants with many keys.
    # @MX:REASON: BackgroundTask runs on the same event loop as foreground
    #   requests; a blocking flush would freeze unrelated /api/* responses.
    """
    import asyncio

    import redis

    pattern = f"configs:{state.slug}:*"

    def _sync_flush() -> int:
        r = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        local_deleted = 0
        batch: list[str] = []
        try:
            for key in r.scan_iter(match=pattern, count=100):
                batch.append(key)
                if len(batch) >= 100:
                    local_deleted += int(r.unlink(*batch))  # type: ignore[arg-type]
                    batch.clear()
            if batch:
                local_deleted += int(r.unlink(*batch))  # type: ignore[arg-type]
        finally:
            r.close()
        return local_deleted

    deleted = await asyncio.to_thread(_sync_flush)
    logger.info("redis_tenant_keys_flushed", slug=state.slug, pattern=pattern, deleted=deleted)


# ---------------------------------------------------------------------------
# Step 8 — delete_qdrant_points
# ---------------------------------------------------------------------------


async def _delete_qdrant_points(state: _DeprovisionState) -> None:
    """Delete all Qdrant points matching org_id={org_id} from shared collections.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=30,
    )
    collections = ["klai_knowledge", "klai_focus"]
    try:
        for collection in collections:
            try:
                await client.delete(
                    collection_name=collection,
                    points_selector=Filter(
                        must=[
                            FieldCondition(
                                key="org_id",
                                match=MatchValue(value=state.org_id),
                            )
                        ]
                    ),
                )
                logger.info(
                    "qdrant_points_deleted",
                    slug=state.slug,
                    org_id=state.org_id,
                    collection=collection,
                )
            except Exception as exc:
                # Collection might not exist (404-like) — treat as idempotent
                exc_str = str(exc)
                if "not found" in exc_str.lower() or "404" in exc_str:
                    logger.info(
                        "qdrant_collection_not_found",
                        slug=state.slug,
                        collection=collection,
                    )
                else:
                    raise
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Step 9 — delete_falkordb_graph
# ---------------------------------------------------------------------------


async def _delete_falkordb_graph(state: _DeprovisionState) -> None:
    """POST to knowledge-ingest /internal/v1/orgs/{org_id}/wipe-graph.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    from app.trace import get_trace_headers

    if not settings.knowledge_ingest_url:
        logger.warning("falkordb_wipe_skipped_no_url", org_id=state.org_id, slug=state.slug)
        return

    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post(f"/internal/v1/orgs/{state.org_id}/wipe-graph")
        if resp.status_code == 404:
            logger.info("falkordb_graph_already_absent", org_id=state.org_id, slug=state.slug)
            return
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "falkordb_graph_wiped",
            org_id=state.org_id,
            slug=state.slug,
            nodes_deleted=data.get("nodes_deleted", 0),
        )


# ---------------------------------------------------------------------------
# Step 10 — delete_scribe_artifacts
# ---------------------------------------------------------------------------


async def _delete_scribe_artifacts(state: _DeprovisionState) -> None:
    """S3 batch delete under s3://klai-scribe/{slug}/.

    Uses feature-flag pattern: if garage_s3_endpoint is empty, log and no-op.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    import boto3  # type: ignore[import-untyped]
    import botocore.exceptions  # type: ignore[import-untyped]

    s3_endpoint = settings.garage_s3_endpoint
    if not s3_endpoint:
        logger.warning(
            "scribe_artifacts_delete_skipped_no_s3_endpoint",
            slug=state.slug,
            reason="garage_s3_endpoint not configured",
        )
        return

    s3_access_key = settings.garage_s3_access_key
    s3_secret_key = settings.garage_s3_secret_key
    s3_bucket = settings.garage_s3_bucket

    def _sync_delete() -> None:
        s3 = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
        )
        prefix = f"{state.slug}/"
        deleted_count = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            delete_payload = {"Objects": [{"Key": obj["Key"]} for obj in objects]}
            s3.delete_objects(Bucket=s3_bucket, Delete=delete_payload)
            deleted_count += len(objects)
        logger.info(
            "scribe_artifacts_deleted",
            slug=state.slug,
            bucket=s3_bucket,
            prefix=prefix,
            deleted_count=deleted_count,
        )

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _sync_delete)
    except botocore.exceptions.BotoCoreError:
        logger.warning(
            "scribe_artifacts_delete_failed",
            slug=state.slug,
            exc_info=True,
        )
        raise


# ---------------------------------------------------------------------------
# Step 11 — delete_litellm_team
# ---------------------------------------------------------------------------


async def _delete_litellm_team(state: _DeprovisionState) -> None:
    """POST /team/delete with team_ids=[litellm_team_id].

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    if not state.litellm_team_id:
        logger.info("litellm_team_delete_skipped_no_id", slug=state.slug)
        return

    async with httpx.AsyncClient(
        base_url=settings.litellm_base_url,
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        timeout=10.0,
    ) as client:
        resp = await client.post("/team/delete", json={"team_ids": [state.litellm_team_id]})
        if resp.status_code == 404:
            logger.info("litellm_team_already_absent", slug=state.slug, team_id=state.litellm_team_id)
            return
        resp.raise_for_status()
    logger.info("litellm_team_deleted", slug=state.slug, team_id=state.litellm_team_id)


# ---------------------------------------------------------------------------
# Step 12 — archive_moneybird_subscription
# ---------------------------------------------------------------------------


async def _archive_moneybird_subscription(state: _DeprovisionState) -> None:
    """Stop Moneybird subscription + archive contact.

    Skips with log if moneybird_subscription_id is None or settings are empty.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    if not state.moneybird_subscription_id and not state.moneybird_contact_id:
        logger.info(
            "moneybird_skipped_no_subscription_id",
            slug=state.slug,
            org_id=state.org_id,
        )
        return

    if not settings.moneybird_api_token or not settings.moneybird_api_token.strip():
        logger.warning(
            "moneybird_skipped_not_configured",
            slug=state.slug,
            reason="moneybird_api_token empty",
        )
        return

    from app.services.moneybird_client import get_moneybird_client

    client = get_moneybird_client()
    try:
        if state.moneybird_subscription_id:
            await client.stop_subscription(state.moneybird_subscription_id)
        if state.moneybird_contact_id:
            await client.archive_contact(state.moneybird_contact_id)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Step 13 — delete_personal_kb
# ---------------------------------------------------------------------------


async def _delete_personal_kb(state: _DeprovisionState) -> None:
    """docs_api.deprovision_kb(org_slug=slug, kb_slug='personal').

    # @MX:NOTE: idempotent — al-weg (404) = geen exception. SPEC R3.
    # @MX:NOTE: connect/read errors propagate to the orchestrator's retry loop
    #   (httpx.ConnectError / ConnectTimeout / ReadTimeout are httpx.HTTPError
    #   subclasses, which are listed in _RETRYABLE_EXCEPTIONS). After 3 retries
    #   the step fails loud → status failed_deprovisioning. We do NOT silently
    #   swallow connect-failures here — that would leave docs-app data behind
    #   while the orchestrator marks the tenant as deprovisioned.
    """
    from app.services import docs_client as docs_api

    try:
        await docs_api.deprovision_kb(org_slug=state.slug, kb_slug="personal")
        logger.info("personal_kb_deleted", slug=state.slug)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.info("personal_kb_already_absent", slug=state.slug)
            return
        raise


# ---------------------------------------------------------------------------
# Step 14 — delete_zitadel_oidc_app
# ---------------------------------------------------------------------------


async def _delete_zitadel_oidc_app(state: _DeprovisionState) -> None:
    """DELETE the LibreChat OIDC app from Zitadel. Skip if no app_id.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    if not state.zitadel_oidc_app_id:
        logger.info("zitadel_oidc_app_delete_skipped_no_id", slug=state.slug)
        return

    from app.services.zitadel import zitadel

    await zitadel.delete_librechat_oidc_app(state.zitadel_oidc_app_id)
    logger.info("zitadel_oidc_app_deleted", slug=state.slug, app_id=state.zitadel_oidc_app_id)


# ---------------------------------------------------------------------------
# Step 15 — delete_zitadel_org
# ---------------------------------------------------------------------------


async def _delete_zitadel_org(state: _DeprovisionState) -> None:
    """DELETE /management/v1/orgs — cascades all users + grants.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    from app.services.zitadel import zitadel

    await zitadel.delete_org(state.zitadel_org_id)
    logger.info("zitadel_org_deleted", slug=state.slug, zitadel_org_id=state.zitadel_org_id)


# ---------------------------------------------------------------------------
# Step 16 — finalize_postgres_delete
# ---------------------------------------------------------------------------


async def _finalize_postgres_delete(state: _DeprovisionState) -> None:
    """INSERT tenant_lifecycle_event + explicit child DELETEs + DELETE portal_orgs.

    All in one transaction. This is the only step that directly touches the DB
    session and performs the hard-delete. The audit event MUST be inserted before
    the org row is deleted (audit table has no FK, so it survives).

    # @MX:NOTE: idempotent — if org is already gone, the DELETE is a no-op. SPEC R3.
    # @MX:WARN: hard-deletes the portal_orgs row. The DELETE list below MUST stay in
    #   sync with every FK to portal_orgs.id that does NOT have ondelete=CASCADE.
    #   Source of truth: grep "ForeignKey.*portal_orgs" klai-portal/backend/app/models/.
    #   Tables that DO cascade automatically (no explicit DELETE needed):
    #     portal_connectors (CASCADE), portal_widgets (CASCADE),
    #     portal_feedback_events (CASCADE), portal_retrieval_gaps (CASCADE),
    #     partner_api_keys (CASCADE).
    #   Tables that SET NULL on delete (preserved with NULL org_id):
    #     product_events (SET NULL — historical analytics rows survive),
    #     portal_users.deleted_by_org? (SET NULL on portal.py:138 — review-only field).
    # @MX:REASON: any new non-cascading FK added to portal_orgs in the future MUST be
    #   added to this DELETE list, otherwise the final hard-delete throws FK violation
    #   and the tenant gets stuck in failed_deprovisioning.
    """
    from app.services.audit.tenant_lifecycle import emit_lifecycle_event

    db = state.db

    # 1. Audit event — inside this transaction so a failure rolls back the delete too.
    await emit_lifecycle_event(
        db,
        event_type="deprovisioned",
        org_id_snapshot=state.org_id,
        org_slug_snapshot=state.slug,
        org_name_snapshot=state.org_name,
        actor_user_id=state.deprovisioner_user_id,
        actor_type=state.deprovisioner_type,
        properties={
            "deprovisioner_type": state.deprovisioner_type,
        },
    )

    # 2. Explicit DELETEs on non-cascading child tables.
    # Order matters: KB tables first (they have child-CASCADE chains), then
    # group tables, then leaf tables, then portal_users last (other tables
    # may FK to it).
    #
    # portal_knowledge_bases — cascades portal_user_kb_access + portal_group_kb_access
    # (both have ondelete=CASCADE on their kb_id FK).
    await db.execute(text("DELETE FROM portal_knowledge_bases WHERE org_id = :id"), {"id": state.org_id})
    # portal_kb_tombstones — independent table tracking deleted KBs per org.
    await db.execute(text("DELETE FROM portal_kb_tombstones WHERE org_id = :id"), {"id": state.org_id})
    # vexa_meetings — meetings owned by org users; FK has no ondelete so blocks portal_orgs DELETE.
    await db.execute(text("DELETE FROM vexa_meetings WHERE org_id = :id"), {"id": state.org_id})
    # portal_groups — cascades portal_group_memberships + portal_group_products via group_id CASCADE.
    await db.execute(text("DELETE FROM portal_groups WHERE org_id = :id"), {"id": state.org_id})
    # portal_products
    await db.execute(text("DELETE FROM portal_products WHERE org_id = :id"), {"id": state.org_id})
    # portal_templates
    await db.execute(text("DELETE FROM portal_templates WHERE org_id = :id"), {"id": state.org_id})
    # portal_users — last of the non-cascading children that other tables may FK to.
    await db.execute(text("DELETE FROM portal_users WHERE org_id = :id"), {"id": state.org_id})

    # 3. Hard-delete the org row — cascades the auto-CASCADE tables (connectors,
    #    widgets, feedback_events, retrieval_gaps, partner_api_keys) and SET NULLs
    #    the SET NULL tables (product_events).
    await db.execute(text("DELETE FROM portal_orgs WHERE id = :id"), {"id": state.org_id})

    # 4. Commit the whole transaction atomically.
    await db.commit()

    # 5. SPEC R11 — second slug-cache invalidate. Step 0 already invalidated
    # at the start of deprovisioning, but the cache could have been
    # repopulated mid-run by an in-flight request that read the slug before
    # the 403 guard kicked in. Invalidate again so the slug is immediately
    # available for re-signup with the same name.
    from app.api.auth import invalidate_tenant_slug_cache

    invalidate_tenant_slug_cache()

    logger.info(
        "portal_org_hard_deleted",
        org_id=state.org_id,
        slug=state.slug,
        actor_type=state.deprovisioner_type,
    )


# ---------------------------------------------------------------------------
# Step list (ordered per SPEC R5)
# ---------------------------------------------------------------------------

STEPS = [
    _mark_deprovisioning,
    _delete_caddy_upstream,
    _delete_librechat_container,
    _delete_librechat_filesystem,
    _drop_mongodb_database,
    _drop_mongodb_user,
    _delete_meilisearch_index,
    _flush_redis_tenant_keys,
    _delete_qdrant_points,
    _delete_falkordb_graph,
    _delete_scribe_artifacts,
    _delete_litellm_team,
    _archive_moneybird_subscription,
    _delete_personal_kb,
    _delete_zitadel_oidc_app,
    _delete_zitadel_org,
    _finalize_postgres_delete,
]
