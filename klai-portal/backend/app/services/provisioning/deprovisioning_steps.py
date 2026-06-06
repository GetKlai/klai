"""
Tenant deprovisioning steps — SPEC-INFRA-TENANT-DELETE-001.

21 idempotent step-functions that are called in order by the deprovisioning
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
from app.core.provisioning_names import validate_slug_for_provisioning
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
    names = validate_slug_for_provisioning(state.slug, domain=settings.domain)
    tenant_file = Path(settings.caddy_tenants_path) / names.caddyfile_name
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
    container_name = validate_slug_for_provisioning(state.slug, domain=settings.domain).librechat_container
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
    """DELETE tenant-scoped LibreChat Meilisearch indexes and runtime keys.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    meili_url = "http://meilisearch:7700"
    meili_key = settings.meili_master_key
    index_names = (f"{state.slug}_messages", f"{state.slug}_convos")
    key_name = f"librechat-{state.slug}-meili"
    errors: list[Exception] = []
    async with httpx.AsyncClient(
        base_url=meili_url,
        headers={"Authorization": f"Bearer {meili_key}"},
        timeout=15.0,
    ) as client:
        for index_name in index_names:
            try:
                resp = await client.delete(f"/indexes/{index_name}")
            except httpx.HTTPError as exc:
                errors.append(exc)
                logger.warning("meilisearch_index_delete_failed", slug=state.slug, index=index_name, error=str(exc))
                continue
            if resp.status_code == 404:
                logger.info("meilisearch_index_already_absent", slug=state.slug, index=index_name)
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                errors.append(exc)
                logger.warning(
                    "meilisearch_index_delete_failed", slug=state.slug, index=index_name, status=resp.status_code
                )
                continue
            else:
                logger.info("meilisearch_index_deleted", slug=state.slug, index=index_name)

        try:
            keys_resp = await client.get("/keys", params={"limit": 1000})
            keys_resp.raise_for_status()
            deleted_keys = 0
            for key in keys_resp.json().get("results", []):
                if key.get("name") != key_name:
                    continue
                uid = key.get("uid")
                if not uid:
                    continue
                try:
                    delete_resp = await client.delete(f"/keys/{uid}")
                    if delete_resp.status_code == 404:
                        continue
                    delete_resp.raise_for_status()
                except httpx.HTTPError as exc:
                    errors.append(exc)
                    logger.warning(
                        "meilisearch_tenant_key_delete_failed", slug=state.slug, key_name=key_name, key_uid=uid
                    )
                    continue
                else:
                    deleted_keys += 1
            logger.info("meilisearch_tenant_keys_deleted", slug=state.slug, key_name=key_name, deleted=deleted_keys)
        except httpx.HTTPError as exc:
            errors.append(exc)
            logger.warning("meilisearch_tenant_key_delete_failed", slug=state.slug, key_name=key_name)

    if errors:
        raise errors[0]


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
    """Delete all Qdrant points matching org_id={zitadel_org_id} from klai_knowledge.

    klai_knowledge stores the Zitadel resourceowner ID (string like
    "100000000000000001") in the ``org_id`` payload key, NOT the portal_orgs
    integer PK.

    Pre-fix every deprovisioning since SPEC-INFRA-TENANT-DELETE-001 landed
    silently filtered with the int PK, matching zero points (a HIGH-severity
    GDPR purge gap surfaced by audit 2026-05-05). Now uses
    state.zitadel_org_id consistently with the writer-side IDs.

    SPEC-DECOMM-FOCUS-001: klai_focus collection removed from this list.
    Focus is decommissioned and the collection is dropped during the
    decommission runbook.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=30,
    )
    collection = "klai_knowledge"
    filter_key = "org_id"
    try:
        try:
            await client.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key=filter_key,
                            match=MatchValue(value=state.zitadel_org_id),
                        )
                    ]
                ),
            )
            logger.info(
                "qdrant_points_deleted",
                slug=state.slug,
                org_id=state.org_id,
                zitadel_org_id=state.zitadel_org_id,
                collection=collection,
                filter_key=filter_key,
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
        raise RuntimeError("knowledge_ingest_url is required for tenant deprovisioning FalkorDB wipe")

    # CRIT fix (audit 2026-05-05): FalkorDB writer (knowledge-ingest's Graphiti
    # adapter) stores group_id as the Zitadel resourceowner ID. Passing
    # state.org_id (portal_orgs int PK) here matched zero nodes — pre-existing
    # bug since SPEC-INFRA-TENANT-DELETE-001 landed.
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post(f"/internal/v1/orgs/{state.zitadel_org_id}/wipe-graph")
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "falkordb_graph_wiped",
            org_id=state.org_id,
            zitadel_org_id=state.zitadel_org_id,
            slug=state.slug,
            nodes_deleted=data.get("nodes_deleted", 0),
        )


# ---------------------------------------------------------------------------
# Step 9a — wipe_knowledge_postgres (SPEC-INFRA-TENANT-DELETE-002 G3)
# ---------------------------------------------------------------------------


async def _wipe_knowledge_postgres(state: _DeprovisionState) -> None:
    """POST to knowledge-ingest /internal/v1/orgs/{org_id}/wipe-postgres.

    Closes G3 of audit Cluster F. The endpoint hard-deletes every row
    carrying ``org_id`` from the 8 tenant-scoped ``knowledge.*`` tables
    (page_links, crawled_pages, crawl_jobs, crawl_domains, kb_config,
    org_config, entities, artifacts) inside a single transaction.
    Cascade-children (artifact_entities, artifact_images, derivations)
    are picked up automatically.

    Without this step, knowledge.* tenant rows are orphaned after
    portal_orgs DELETE: those tables have no FK to portal_orgs (they
    live in a different schema) so cascade-delete cannot reach them.

    # @MX:NOTE: idempotent — second call returns rows_deleted={...:0}. SPEC R3.
    """
    from app.trace import get_trace_headers

    if not settings.knowledge_ingest_url:
        raise RuntimeError("knowledge_ingest_url is required for tenant deprovisioning Postgres wipe")

    # CRIT fix (audit 2026-05-05): knowledge.* tables store the Zitadel
    # resourceowner ID in their org_id columns (verified live: values like
    # "100000000000000001" not portal_orgs.id integer "1"). Pass
    # state.zitadel_org_id so the WHERE clauses match.
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post(f"/internal/v1/orgs/{state.zitadel_org_id}/wipe-postgres")
        if resp.status_code >= 400:
            # Surface body before raise_for_status so retry-failure root
            # cause (auth misconfig, schema drift) is one VictoriaLogs query
            # away, not buried in retry-exhausted exception.
            logger.error(
                "knowledge_postgres_wipe_endpoint_error",
                org_id=state.org_id,
                zitadel_org_id=state.zitadel_org_id,
                slug=state.slug,
                status=resp.status_code,
                body=resp.text[:500],
            )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "knowledge_postgres_wiped",
            org_id=state.org_id,
            zitadel_org_id=state.zitadel_org_id,
            slug=state.slug,
            rows_deleted=data.get("rows_deleted", {}),
        )


# ---------------------------------------------------------------------------
# Step 9b — wipe_klai_connector_state (SPEC-INFRA-TENANT-DELETE-002 G6)
# ---------------------------------------------------------------------------


async def _wipe_klai_connector_state(state: _DeprovisionState) -> None:
    """POST to klai-connector /internal/v1/orgs/{org_id}/wipe-state.

    Closes G6 of audit Cluster F. The endpoint hard-deletes BOTH
    tenant-scoped tables in the ``connector`` schema in one transaction:
    ``connector.sync_runs`` (sync history) AND ``connector.connectors``
    (per-tenant adapter config + encrypted OAuth/API credentials in
    ``portal_secret_id``). Deleting connectors is the GDPR-critical part —
    without it the deprovisioned tenant's credentials remain at rest. The
    response ``rows_deleted`` is the total across both tables. The
    klai-connector schema lives in the connector container's DB and does NOT
    cascade with portal_orgs DELETE — this is the only purge path.

    Authenticates via ``klai_connector_secret`` (matches klai-connector's
    ``portal_caller_secret``). NULL-org legacy rows are intentionally
    preserved by the endpoint — those pre-date tenant tracking.

    # @MX:NOTE: idempotent — second call returns rows_deleted=0. SPEC R3.
    """
    from app.trace import get_trace_headers

    if not settings.klai_connector_url:
        raise RuntimeError("klai_connector_url is required for tenant deprovisioning connector wipe")

    # CRIT fix (audit 2026-05-05): connector.sync_runs.org_id stores the Zitadel
    # resourceowner ID (VARCHAR(255) like "100000000000000002"), NOT the
    # portal_orgs int PK. Same fix-shape as the qdrant + falkordb steps above.
    async with httpx.AsyncClient(
        base_url=settings.klai_connector_url,
        headers={
            # klai-connector auths via Authorization Bearer (X-Internal-Secret
            # was the historical pattern). Use the same Bearer the portal sends
            # on every other connector call site (services/klai_connector_client.py).
            "Authorization": f"Bearer {settings.klai_connector_secret}",
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post(f"/internal/v1/orgs/{state.zitadel_org_id}/wipe-state")
        if resp.status_code >= 400:
            logger.error(
                "klai_connector_state_wipe_endpoint_error",
                org_id=state.org_id,
                zitadel_org_id=state.zitadel_org_id,
                slug=state.slug,
                status=resp.status_code,
                body=resp.text[:500],
            )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "klai_connector_state_wiped",
            org_id=state.org_id,
            zitadel_org_id=state.zitadel_org_id,
            slug=state.slug,
            rows_deleted=data.get("rows_deleted", 0),
        )


# ---------------------------------------------------------------------------
# Step 10 — wipe_scribe_state
# ---------------------------------------------------------------------------


async def _wipe_scribe_state(state: _DeprovisionState) -> None:
    """POST to scribe-api /internal/v1/orgs/{org_id}/wipe-state."""
    from app.trace import get_trace_headers

    if not settings.scribe_api_url:
        raise RuntimeError("scribe_api_url is required for tenant deprovisioning Scribe wipe")

    async with httpx.AsyncClient(
        base_url=settings.scribe_api_url,
        headers={
            "X-Internal-Secret": settings.internal_secret,
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post(f"/internal/v1/orgs/{state.zitadel_org_id}/wipe-state")
        if resp.status_code >= 400:
            logger.error(
                "scribe_state_wipe_endpoint_error",
                org_id=state.org_id,
                zitadel_org_id=state.zitadel_org_id,
                slug=state.slug,
                status=resp.status_code,
                body=resp.text[:500],
            )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "scribe_state_wiped",
            org_id=state.org_id,
            zitadel_org_id=state.zitadel_org_id,
            slug=state.slug,
            rows_deleted=data.get("rows_deleted", 0),
            audio_files_deleted=data.get("audio_files_deleted", 0),
        )


# ---------------------------------------------------------------------------
# Step 11 — delete_scribe_artifacts
# ---------------------------------------------------------------------------


async def _delete_scribe_artifacts(state: _DeprovisionState) -> None:
    """S3 batch delete under s3://klai-scribe/{slug}/.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    import boto3  # type: ignore[import-untyped]
    import botocore.exceptions  # type: ignore[import-untyped]

    s3_endpoint = settings.garage_s3_endpoint
    if not s3_endpoint:
        raise RuntimeError("garage_s3_endpoint is required for tenant deprovisioning Scribe artifact wipe")

    # Production `GARAGE_S3_ENDPOINT` is the schemeless form `garage:3900`
    # because the canonical reader (`app/api/kb_images.py::_make_minio_client`)
    # uses the Minio SDK which takes a schemeless `host:port` + `secure` flag.
    # boto3 (this callsite) needs an `http(s)://` URL or it raises
    # `ValueError: Invalid endpoint`. Prepend `http://` defensively so the same
    # env var works for both consumers without forcing operators to
    # double-track variants. SPEC-INFRA-TENANT-DELETE-003 follow-up.
    if "://" not in s3_endpoint:
        s3_endpoint = f"http://{s3_endpoint}"

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
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=s3_bucket, Prefix=prefix):
                objects = page.get("Contents", [])
                if not objects:
                    continue
                delete_payload = {"Objects": [{"Key": obj["Key"]} for obj in objects]}
                s3.delete_objects(Bucket=s3_bucket, Delete=delete_payload)
                deleted_count += len(objects)
        except s3.exceptions.NoSuchBucket:
            # SPEC R3 — al-weg = geen exception. Idempotent: if the bucket
            # itself does not exist, there are no artifacts to delete and the
            # step has nothing to do. Common on tenants that never used
            # Scribe (no audio uploaded → bucket never auto-created), and on
            # deployments where the Scribe S3 backend hasn't been
            # provisioned yet. SPEC-INFRA-TENANT-DELETE-003 Bug D.
            logger.info(
                "scribe_artifacts_bucket_absent",
                slug=state.slug,
                bucket=s3_bucket,
            )
            return
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
        raise RuntimeError("moneybird_api_token is required when a tenant has Moneybird IDs to archive")

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
# Step 15 — delete_zitadel_users
# ---------------------------------------------------------------------------


async def _delete_zitadel_users(state: _DeprovisionState) -> None:
    """Delete tenant users from Zitadel before deleting the org.

    Portal-created users live in the portal org in Zitadel, even when they are
    members of a tenant org in the Klai portal DB. Deleting the tenant org does
    not remove those platform-owned human users. The orchestrator captures only
    users whose final portal membership is this tenant, so multi-tenant users
    keep their global identity.
    """
    if not state.zitadel_user_ids:
        logger.info("zitadel_users_delete_skipped_no_users", slug=state.slug)
        return

    from app.services.zitadel import zitadel

    org_ids = tuple(
        dict.fromkeys(
            org_id
            for org_id in (
                settings.zitadel_portal_org_id,
                settings.zitadel_org_id,
                state.zitadel_org_id,
            )
            if org_id
        )
    )
    if not org_ids:
        raise RuntimeError("No Zitadel org id configured for user deletion")

    # H3 fix (SPEC-INFRA-TENANT-DELETE): ``zitadel.remove_user`` swallows
    # 403/404 internally and returns None — it NEVER raises for "already
    # absent". The previous implementation broke on the first remove_user
    # call and counted it as deleted, so the per-org fallback and the
    # "still exists" safety check were dead code and a wrong-org 404 was
    # reported as a successful delete. We now (1) call remove_user for every
    # candidate org context (idempotent best-effort delete), then (2) verify
    # with get_user_by_id — the authoritative check — and fail loud if the
    # account is still resolvable, so an orphaned identity lands the tenant in
    # failed_deprovisioning instead of being silently reported deleted.
    removed = 0
    for user_id in state.zitadel_user_ids:
        for org_id in org_ids:
            await zitadel.remove_user(org_id, user_id)

        try:
            await zitadel.get_user_by_id(user_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                removed += 1
                logger.info("zitadel_user_removed", slug=state.slug, zitadel_user_id=user_id)
                continue
            raise

        # Still resolvable after deleting from every org context — none of the
        # contexts owned the user, or Zitadel rejected the delete. Fail loud.
        raise RuntimeError(f"Zitadel user still exists after deletion attempts: {user_id}")

    logger.info(
        "zitadel_users_deleted",
        slug=state.slug,
        removed=removed,
        total=len(state.zitadel_user_ids),
    )


# ---------------------------------------------------------------------------
# Step 16 — delete_zitadel_org
# ---------------------------------------------------------------------------


async def _delete_zitadel_org(state: _DeprovisionState) -> None:
    """DELETE /management/v1/orgs.

    # @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.
    """
    from app.services.zitadel import zitadel

    await zitadel.delete_org(state.zitadel_org_id)
    logger.info("zitadel_org_deleted", slug=state.slug, zitadel_org_id=state.zitadel_org_id)


# ---------------------------------------------------------------------------
# Step 17 — finalize_postgres_delete
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
    from app.core.database import set_tenant
    from app.services.audit.tenant_lifecycle import emit_lifecycle_event

    db = state.db

    # 0. Set tenant context for RLS Category-D tables. Without this, the
    # explicit DELETEs below (portal_knowledge_bases, portal_groups,
    # portal_kb_tombstones, vexa_meetings — all in RLS_DML_TABLES per
    # rls_guard.py) raise IntegrityError 42501 because the orchestrator
    # opens its own AsyncSessionLocal which RESETS app.current_org_id.
    # See portal-backend.md "Pool-GUC pollution" pitfall.
    await set_tenant(db, state.org_id)

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
            "zitadel_org_id": state.zitadel_org_id,
            "zitadel_oidc_app_id": state.zitadel_oidc_app_id,
            "litellm_team_id": state.litellm_team_id,
            "moneybird_subscription_id": state.moneybird_subscription_id,
            "moneybird_contact_id": state.moneybird_contact_id,
            # M2 (SPEC-INFRA-TENANT-DELETE): record which Zitadel identities were
            # removed so post-hard-delete historical verification is possible
            # once portal_users is gone. Empty tuple → [] (e.g. all members were
            # multi-tenant and kept their global identity).
            "zitadel_user_ids": list(state.zitadel_user_ids),
        },
    )

    # 2. Explicit DELETEs on non-cascading child tables.
    # Order matters: KB tables first (they have child-CASCADE chains), then
    # group tables, then per-user entitlement tables (FK to portal_users),
    # then portal_users last (other tables may FK to it).
    #
    # SPEC-INFRA-TENANT-DELETE-003 Bug F/G/H/I — the original list was
    # written for the PROFILES-001 era (`portal_products`). RBAC-001
    # superseded that with `portal_user_products` / `portal_group_products`
    # and PRICING-PER-USER-001 added `portal_user_seat_history`. All three
    # new tables have non-cascading FKs to portal_orgs and would block the
    # final portal_orgs DELETE with FK-violation errors. The legacy
    # `portal_products` table no longer exists (dropped in
    # rbac001_drop_legacy_rbac_data) — DELETE on it raises
    # UndefinedTableError and aborts the transaction.
    #
    # Canonical FK audit on production (2026-05-13 via pg_constraint):
    #   non-cascading: portal_group_products, portal_groups,
    #     portal_kb_tombstones, portal_knowledge_bases, portal_templates,
    #     portal_user_products, portal_user_seat_history, portal_users,
    #     vexa_meetings
    #
    # portal_knowledge_bases — cascades portal_user_kb_access + portal_group_kb_access
    # (both have ondelete=CASCADE on their kb_id FK).
    await db.execute(text("DELETE FROM portal_knowledge_bases WHERE org_id = :id"), {"id": state.org_id})
    # legacy docs libraries — direct org_id FK without cascade in z2a3b4c5d6e7.
    await db.execute(text("DELETE FROM portal_docs_libraries WHERE org_id = :id"), {"id": state.org_id})
    # portal_kb_tombstones — independent table tracking deleted KBs per org.
    await db.execute(text("DELETE FROM portal_kb_tombstones WHERE org_id = :id"), {"id": state.org_id})
    # vexa_meetings — meetings owned by org users; FK has no ondelete so blocks portal_orgs DELETE.
    await db.execute(text("DELETE FROM vexa_meetings WHERE org_id = :id"), {"id": state.org_id})
    # portal_group_products — RBAC-001 per-group entitlement. Has org_id FK
    # without ondelete; delete BEFORE portal_groups (group_id FK CASCADE
    # would also clean it up, but we delete by org_id explicitly to keep
    # the deletion order independent of group-table state).
    await db.execute(text("DELETE FROM portal_group_products WHERE org_id = :id"), {"id": state.org_id})
    # portal_groups — cascades portal_group_memberships via group_id CASCADE.
    await db.execute(text("DELETE FROM portal_groups WHERE org_id = :id"), {"id": state.org_id})
    # portal_templates
    await db.execute(text("DELETE FROM portal_templates WHERE org_id = :id"), {"id": state.org_id})
    # portal_user_products — RBAC-001 per-user entitlement. FK to both
    # portal_orgs and portal_users — delete BEFORE portal_users.
    await db.execute(text("DELETE FROM portal_user_products WHERE org_id = :id"), {"id": state.org_id})
    # portal_user_seat_history — PRICING-PER-USER-001 audit log of
    # seat_type changes. FK to portal_orgs (no cascade) and to
    # portal_users (no cascade) — delete BEFORE portal_users.
    await db.execute(text("DELETE FROM portal_user_seat_history WHERE org_id = :id"), {"id": state.org_id})
    # portal_users — last of the non-cascading children that other tables may FK to.
    await db.execute(text("DELETE FROM portal_users WHERE org_id = :id"), {"id": state.org_id})
    # SPEC-INFRA-TENANT-DELETE-002 G1 — portal_join_requests has a single FK
    # `portal_join_requests_org_id_fkey FOREIGN KEY (org_id) REFERENCES
    # portal_orgs(id) ON DELETE SET NULL` (verified 2026-05-05 against prod).
    # WITHOUT this explicit DELETE, the portal_orgs DELETE below would simply
    # NULL the org_id on every join_request row — leaving the email/name PII
    # in place as orphaned data. With this DELETE, the rows are gone before
    # the parent. Ordering is independent of portal_users (no FK between them
    # — verified). Idempotent: zero rows for a tenant with no in-flight join
    # requests is a no-op. SPEC-AUTH-009 R2 will drop portal_org_allowed_domains
    # entirely, so G2 is moot post-AUTH-009 and that table is intentionally
    # NOT in this list.
    await db.execute(text("DELETE FROM portal_join_requests WHERE org_id = :id"), {"id": state.org_id})

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
    # SPEC-INFRA-TENANT-DELETE-002 G3 + G6 — sibling wipes-via-internal-endpoint,
    # placed adjacent to the FalkorDB wipe so all "external service tells me to
    # purge tenant rows" steps live as a contiguous block in the SPEC R5 order.
    _wipe_knowledge_postgres,
    _wipe_klai_connector_state,
    _wipe_scribe_state,
    _delete_scribe_artifacts,
    _delete_litellm_team,
    _archive_moneybird_subscription,
    _delete_personal_kb,
    _delete_zitadel_oidc_app,
    _delete_zitadel_users,
    _delete_zitadel_org,
    _finalize_postgres_delete,
]
