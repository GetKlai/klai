"""
Internal service-to-service endpoints.

Only accessible within the Docker network (klai-net) — never exposed publicly.
Protected by a shared Bearer secret (INTERNAL_SECRET env var).

Used by klai-mailer to look up a user's preferred language so it can append
?lang= to email action URLs (verify, password-reset, etc.).

Used by the LiteLLM knowledge hook (KB-010) to check knowledge product entitlement
and perform lazy LibreChat MongoDB ObjectId → Zitadel user ID mapping.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.services.connector_credentials import credential_store
from app.services.entitlements import get_effective_products
from app.services.events import emit_event
from app.services.gap_rescorer import schedule_rescore
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


def _require_internal_token(request: Request) -> None:
    """Reject requests that do not carry the correct internal shared secret."""
    if not settings.internal_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Internal API not configured")
    token = request.headers.get("Authorization", "")
    if token != f"Bearer {settings.internal_secret}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


class UserLanguageResponse(BaseModel):
    preferred_language: str


@router.get("/user-language", response_model=UserLanguageResponse)
async def get_user_language(
    email: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserLanguageResponse:
    """Return the preferred language for a user identified by email address.

    Used by klai-mailer to append ?lang=<lang> to email action URLs so that
    verify / password-reset links open the portal in the user's own language.

    Falls back to "nl" when the user is not found in the portal DB.
    """
    _require_internal_token(request)

    user_id = await zitadel.find_user_id_by_email(email)
    logger.info("Internal user lookup: email=%s, found=%s", email, user_id is not None)
    if not user_id:
        return UserLanguageResponse(preferred_language="nl")

    result = await db.execute(
        select(PortalUser.preferred_language, PortalUser.org_id).where(PortalUser.zitadel_user_id == user_id)
    )
    row = result.first()
    if not row:
        return UserLanguageResponse(preferred_language="nl")
    await set_tenant(db, row.org_id)
    return UserLanguageResponse(preferred_language=row.preferred_language or "nl")


class UserProductsResponse(BaseModel):
    products: list[str]


@router.get("/users/{zitadel_user_id}/products", response_model=UserProductsResponse)
async def get_user_products(
    zitadel_user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserProductsResponse:
    """Return enabled products for a user. Called by Zitadel Action for JWT enrichment.

    Returns empty list if user not found (fail-closed behavior for JWT).
    """
    _require_internal_token(request)

    # Set tenant context so get_effective_products can query RLS-protected tables
    result = await db.execute(select(PortalUser.org_id).where(PortalUser.zitadel_user_id == zitadel_user_id))
    org_id = result.scalar_one_or_none()
    if org_id is not None:
        await set_tenant(db, org_id)

    products = await get_effective_products(zitadel_user_id, db)
    return UserProductsResponse(products=products)


class ConnectorConfigResponse(BaseModel):
    connector_id: str
    kb_id: int
    kb_slug: str
    zitadel_org_id: str  # Zitadel org ID string — used by klai-connector for Qdrant partitioning
    connector_type: str
    config: dict
    schedule: str | None
    is_enabled: bool
    allowed_assertion_modes: list[str] | None


@router.get("/connectors/{connector_id}", response_model=ConnectorConfigResponse)
async def get_connector_config(
    connector_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ConnectorConfigResponse:
    """Return connector config for klai-connector service."""
    _require_internal_token(request)
    # portal_connectors has no RLS — use it to resolve org_id for tenant context.
    connector_stub = await db.get(PortalConnector, connector_id)
    if not connector_stub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector_stub.org_id)
    result = await db.execute(
        select(PortalConnector, PortalKnowledgeBase, PortalOrg)
        .join(PortalKnowledgeBase, PortalConnector.kb_id == PortalKnowledgeBase.id)
        .join(PortalOrg, PortalConnector.org_id == PortalOrg.id)
        .where(PortalConnector.id == connector_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    connector, kb, org = row

    # Merge decrypted credentials into config for internal consumers
    # @MX:NOTE: [AUTO] Fallback: encrypted_credentials IS NULL => read plaintext config (legacy).
    # Remove after cleanup migration.
    merged_config = dict(connector.config) if connector.config else {}
    if connector.encrypted_credentials is not None and credential_store is not None:
        decrypted = await credential_store.decrypt_credentials(
            org_id=connector.org_id,
            encrypted_credentials=connector.encrypted_credentials,
            db=db,
        )
        merged_config.update(decrypted)

    return ConnectorConfigResponse(
        connector_id=str(connector.id),
        kb_id=connector.kb_id,
        kb_slug=kb.slug,
        zitadel_org_id=org.zitadel_org_id,
        connector_type=connector.connector_type,
        config=merged_config,
        schedule=connector.schedule,
        is_enabled=connector.is_enabled,
        allowed_assertion_modes=connector.allowed_assertion_modes,
    )


class SyncStatusCallback(BaseModel):
    sync_run_id: str
    status: str
    completed_at: datetime
    documents_total: int = 0
    documents_ok: int = 0
    documents_failed: int = 0
    bytes_processed: int = 0
    error_details: list[dict] | None = None


@router.post("/connectors/{connector_id}/sync-status", status_code=status.HTTP_204_NO_CONTENT)
async def receive_sync_status(
    connector_id: str,
    body: SyncStatusCallback,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Receive sync completion callback from klai-connector.

    Updates last_sync_at and last_sync_status on the portal connector record.
    Called by klai-connector after each sync run completes (success or failure).
    """
    _require_internal_token(request)
    connector = await db.get(PortalConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector.org_id)
    connector.last_sync_at = body.completed_at
    connector.last_sync_status = body.status
    connector.last_sync_documents_ok = body.documents_ok
    await db.commit()

    if body.status == "success":
        # Load org's zitadel_org_id for re-scoring
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == connector.org_id))
        org = org_result.scalar_one_or_none()
        if org:
            await schedule_rescore(
                org_id=connector.org_id,
                zitadel_org_id=org.zitadel_org_id,
                kb_slug=None,  # connector sync covers all KBs
                db_factory=get_db,
                delay_seconds=0.0,  # no delay needed -- connector already fully synced
            )


class CredentialsUpdate(BaseModel):
    """Partial update to a connector's encrypted credentials (SPEC-KB-025).

    Called by klai-connector after refreshing an OAuth access token. Only the
    fields to be updated are provided; the rest of the encrypted credential
    blob is preserved.
    """

    access_token: str
    token_expiry: str | None = None


# @MX:ANCHOR: [AUTO] Writeback path for refreshed OAuth access tokens.
# @MX:REASON: Called by klai-connector OAuthAdapterBase.ensure_token(). SPEC-KB-025.
@router.patch("/connectors/{connector_id}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def update_connector_credentials(
    connector_id: str,
    body: CredentialsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Merge refreshed OAuth tokens into the connector's encrypted credentials.

    Flow:
    1. Authorize via internal Bearer secret.
    2. Load connector and set tenant context.
    3. Decrypt current credentials (preserves refresh_token, etc.).
    4. Merge in the new access_token + optional token_expiry.
    5. Re-encrypt and persist. The plaintext config column is overwritten
       with the redacted form (sensitive fields masked as "***").
    """
    _require_internal_token(request)
    connector = await db.get(PortalConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector.org_id)

    if credential_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential store not configured",
        )

    # Start from whatever is currently stored (encrypted or legacy plaintext).
    merged: dict = {}
    if connector.encrypted_credentials is not None:
        merged = await credential_store.decrypt_credentials(
            org_id=connector.org_id,
            encrypted_credentials=connector.encrypted_credentials,
            db=db,
        )
    else:
        merged = dict(connector.config or {})

    # Apply the patch — NEVER log access_token value.
    merged["access_token"] = body.access_token
    if body.token_expiry is not None:
        merged["token_expiry"] = body.token_expiry

    encrypted_blob, redacted_config = await credential_store.encrypt_credentials(
        org_id=connector.org_id,
        connector_type=connector.connector_type,
        config=merged,
        db=db,
    )
    connector.encrypted_credentials = encrypted_blob
    connector.config = redacted_config
    await db.commit()


class KnowledgeFeatureResponse(BaseModel):
    enabled: bool
    kb_retrieval_enabled: bool = True
    kb_personal_enabled: bool = True
    kb_slugs_filter: list[str] | None = None
    kb_narrow: bool = False
    kb_pref_version: int = 0


@router.get("/v1/users/{librechat_user_id}/feature/knowledge", response_model=KnowledgeFeatureResponse)
async def get_knowledge_feature(
    librechat_user_id: str,
    org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeFeatureResponse:
    """Check whether a user has the knowledge product entitlement.

    Called by the LiteLLM knowledge hook on every chat request. Auth-gated (fail-closed):
    any error or unknown user returns enabled=False so KB injection never leaks to
    unauthorized users.

    Lazy mapping: on first call for an unknown librechat_user_id, performs a MongoDB
    lookup in the tenant's LibreChat database to resolve the Zitadel user ID and caches
    it in portal_users.librechat_user_id for all subsequent calls (pure PostgreSQL).
    """
    _require_internal_token(request)

    # Set tenant context early using the org_id query param (Zitadel org ID).
    # This is needed so subsequent queries on RLS-protected tables work correctly.
    org_lookup = await db.execute(select(PortalOrg.id).where(PortalOrg.zitadel_org_id == org_id))
    portal_org_id = org_lookup.scalar_one_or_none()
    if portal_org_id is not None:
        await set_tenant(db, portal_org_id)

    # Step 1: fast path — librechat_user_id already mapped in PostgreSQL
    result = await db.execute(select(PortalUser).where(PortalUser.librechat_user_id == librechat_user_id))
    user = result.scalar_one_or_none()

    if user is None:
        # Step 2: lazy MongoDB lookup to resolve LibreChat ObjectId → Zitadel user ID
        if not settings.librechat_mongo_root_uri:
            logger.warning("KB authz: LIBRECHAT_MONGO_ROOT_URI not set — fail-closed for user %s", librechat_user_id)
            return KnowledgeFeatureResponse(enabled=False)

        # Look up the org to get its LibreChat container name (= MongoDB database name)
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == org_id))
        org = org_result.scalar_one_or_none()
        if org is None or not org.librechat_container:
            logger.warning("KB authz: org %s has no librechat_container — fail-closed", org_id)
            return KnowledgeFeatureResponse(enabled=False)

        try:
            oid = ObjectId(librechat_user_id)
        except InvalidId:
            logger.warning("KB authz: invalid ObjectId %s — fail-closed", librechat_user_id)
            return KnowledgeFeatureResponse(enabled=False)

        mongo_client: AsyncIOMotorClient | None = None
        try:
            mongo_client = AsyncIOMotorClient(settings.librechat_mongo_root_uri)
            mongo_user = await mongo_client[org.librechat_container]["users"].find_one({"_id": oid})
        except Exception as exc:
            logger.warning("KB authz: MongoDB lookup failed for %s — fail-closed: %s", librechat_user_id, exc)
            return KnowledgeFeatureResponse(enabled=False)
        finally:
            if mongo_client is not None:
                mongo_client.close()

        if mongo_user is None:
            logger.warning("KB authz: no LibreChat user found for ObjectId %s — fail-closed", librechat_user_id)
            return KnowledgeFeatureResponse(enabled=False)

        zitadel_user_id = mongo_user.get("openidId") or mongo_user.get("openid_id") or mongo_user.get("sub")
        if not zitadel_user_id:
            logger.warning("KB authz: LibreChat user %s has no openidId/sub — fail-closed", librechat_user_id)
            return KnowledgeFeatureResponse(enabled=False)

        # Resolve portal user and cache the mapping
        portal_result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
        user = portal_result.scalar_one_or_none()
        if user is None:
            logger.warning("KB authz: no portal user for zitadel_user_id %s — fail-closed", zitadel_user_id)
            return KnowledgeFeatureResponse(enabled=False)

        user.librechat_user_id = librechat_user_id
        await db.commit()

    # Org-admins always get knowledge access
    if user.role == "admin":
        enabled = True
    else:
        products = await get_effective_products(user.zitadel_user_id, db)
        enabled = "knowledge" in products

    return KnowledgeFeatureResponse(
        enabled=enabled,
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_narrow=user.kb_narrow,
        kb_pref_version=user.kb_pref_version,
    )


class PageSavedNotification(BaseModel):
    kb_slug: str
    zitadel_org_id: str


@router.post("/v1/orgs/{org_id}/page-saved", status_code=status.HTTP_204_NO_CONTENT)
async def notify_page_saved(
    org_id: int,
    body: PageSavedNotification,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Notify portal-api that a page was saved in a Klai-native KB.

    Called by klai-docs after processing a Gitea push webhook. Schedules a
    gap re-scoring job with a 5-second delay to allow Qdrant indexing to complete.
    """
    _require_internal_token(request)
    await schedule_rescore(
        org_id=org_id,
        zitadel_org_id=body.zitadel_org_id,
        kb_slug=body.kb_slug,
        db_factory=get_db,
        delay_seconds=5.0,
    )


class RetrievalLogIn(BaseModel):
    org_id: str  # Zitadel org ID string
    user_id: str  # LibreChat ObjectId
    chunk_ids: list[str]
    reranker_scores: list[float]
    query_resolved: str
    embedding_model_version: str
    retrieved_at: datetime


@router.post("/v1/retrieval-log", status_code=status.HTTP_201_CREATED)
async def post_retrieval_log(
    body: RetrievalLogIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a retrieval log from the LiteLLM knowledge hook (SPEC-KB-015).

    Resolves zitadel org_id string to portal int org_id, then writes to Redis.
    Silent discard on any error (REQ-KB-015-03).
    """
    _require_internal_token(request)

    try:
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == body.org_id))
        org = org_result.scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

        from app.services.retrieval_log import write_retrieval_log

        await write_retrieval_log(
            org_id=org.id,
            user_id=body.user_id,
            chunk_ids=body.chunk_ids,
            reranker_scores=body.reranker_scores,
            query_resolved=body.query_resolved,
            embedding_model_version=body.embedding_model_version,
            retrieved_at=body.retrieved_at,
        )
    except HTTPException:
        raise
    except Exception:
        # REQ-KB-015-03: silent discard on any error
        logger.warning("retrieval_log_endpoint_failed", exc_info=True)

    return {"ok": True}


class KbFeedbackIn(BaseModel):
    conversation_id: str
    message_id: str
    message_created_at: datetime
    rating: Literal["thumbsUp", "thumbsDown"]
    tag: str | None = None
    text: str | None = None
    librechat_user_id: str
    librechat_tenant_id: str
    model_alias: str | None = None


# @MX:ANCHOR: [AUTO] Public API boundary for KB feedback from LibreChat. SPEC-KB-015.
# @MX:REASON: Called by LibreChat patch (feedback.cjs) + LiteLLM hook + tests. fan_in >= 3.
@router.post("/v1/kb-feedback", status_code=status.HTTP_201_CREATED, response_model=None)
async def post_kb_feedback(
    body: KbFeedbackIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Process feedback from LibreChat (SPEC-KB-015).

    1. Resolve librechat_tenant_id -> org_id
    2. Check idempotency (Redis)
    3. Correlate with retrieval log
    4. Insert feedback event (raw SQL for RLS)
    5. Schedule Qdrant update if correlated
    6. Emit product event
    """
    _require_internal_token(request)

    # 1. Resolve tenant
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.librechat_container == body.librechat_tenant_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")

    await set_tenant(db, org.id)

    # 2. Idempotency check (REQ-KB-015-12)
    redis_pool = await get_redis_pool()
    idem_key = f"fb:{body.message_id}:{body.conversation_id}"
    if redis_pool:
        existing = await redis_pool.get(idem_key)
        if existing:
            return Response(status_code=200)

    # 3. Time-window correlation (REQ-KB-015-09)
    correlated_log = await find_correlated_log(
        org_id=org.id,
        user_id=body.librechat_user_id,
        message_created_at=body.message_created_at,
    )

    chunk_ids = correlated_log["chunk_ids"] if correlated_log else []
    correlated = correlated_log is not None

    # 4. Insert feedback event via raw SQL (RLS table -- split SELECT/INSERT policies)
    await db.execute(
        text("""
            INSERT INTO portal_feedback_events
            (org_id, conversation_id, message_id, rating, tag, feedback_text,
             chunk_ids, correlated, model_alias, occurred_at)
            VALUES (:org_id, :conversation_id, :message_id, :rating, :tag,
                    :feedback_text, :chunk_ids, :correlated, :model_alias, NOW())
        """),
        {
            "org_id": org.id,
            "conversation_id": body.conversation_id,
            "message_id": body.message_id,
            "rating": body.rating,
            "tag": body.tag,
            "feedback_text": body.text,
            "chunk_ids": chunk_ids or None,
            "correlated": correlated,
            "model_alias": body.model_alias,
        },
    )
    await db.commit()

    # 5. Set idempotency key (REQ-KB-015-12)
    if redis_pool:
        try:
            await redis_pool.set(idem_key, "1", ex=3600)
        except Exception:
            logger.warning("kb_feedback_idem_key_set_failed", exc_info=True)

    # 6. Schedule Qdrant quality update if correlated (REQ-KB-015-14)
    if correlated and chunk_ids:
        schedule_quality_update(chunk_ids, body.rating, org.id)

    # 7. Emit product event (REQ-KB-015-22)
    emit_event(
        "knowledge.feedback",
        org_id=org.id,
        properties={
            "rating": body.rating,
            "correlated": correlated,
            "chunk_count": len(chunk_ids),
        },
    )

    return {"ok": True}


class GapEventIn(BaseModel):
    org_id: str  # Zitadel org ID from LiteLLM team key metadata
    user_id: str
    query_text: str
    gap_type: str
    top_score: float | None = None
    nearest_kb_slug: str | None = None
    chunks_retrieved: int = 0
    retrieval_ms: int = 0
    taxonomy_node_ids: list[int] | None = None  # SPEC-KB-022 R6: from LiteLLM hook


@router.post("/v1/gap-events", status_code=status.HTTP_201_CREATED)
async def create_gap_event(
    payload: GapEventIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a knowledge gap event from the LiteLLM hook."""
    _require_internal_token(request)
    from app.models.retrieval_gaps import PortalRetrievalGap

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == payload.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    await set_tenant(db, org.id)

    gap = PortalRetrievalGap(
        org_id=org.id,
        user_id=payload.user_id,
        query_text=payload.query_text,
        gap_type=payload.gap_type,
        top_score=payload.top_score,
        nearest_kb_slug=payload.nearest_kb_slug,
        chunks_retrieved=payload.chunks_retrieved,
        retrieval_ms=payload.retrieval_ms,
        taxonomy_node_ids=payload.taxonomy_node_ids,
    )
    db.add(gap)
    await db.commit()

    # SPEC-KB-022 R6 + SPEC-KB-026 R4: async gap classification via knowledge-ingest
    if payload.taxonomy_node_ids is None and payload.nearest_kb_slug:

        async def _classify_gap(gap_id: int, org_zitadel_id: str, query_text: str, kb_slug: str) -> None:
            """Classify gap query against KB taxonomy via knowledge-ingest. Best-effort."""
            try:
                from app.core.database import AsyncSessionLocal
                from app.services.knowledge_ingest_client import classify_gap_taxonomy

                node_ids = await classify_gap_taxonomy(org_zitadel_id, kb_slug, query_text)
                if not node_ids:
                    return

                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(PortalRetrievalGap)
                        .where(PortalRetrievalGap.id == gap_id)
                        .values(taxonomy_node_ids=node_ids)
                    )
                    await session.commit()

                logger.info(
                    "gap_classification_complete: gap_id=%s, node_ids=%s",
                    gap_id,
                    node_ids,
                )
            except Exception as exc:
                logger.warning(
                    "gap_classification_failed: gap_id=%s, error=%s",
                    gap_id,
                    str(exc),
                )

        _task = asyncio.create_task(  # noqa: RUF006
            _classify_gap(gap.id, payload.org_id, payload.query_text, payload.nearest_kb_slug)
        )

    return {"ok": True}


class RegenerateResponse(BaseModel):
    tenants_updated: list[str]
    errors: list[str]


@router.post("/librechat/regenerate", response_model=RegenerateResponse)
async def regenerate_librechat_configs(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RegenerateResponse:
    """Regenerate per-tenant librechat.yaml from the base template for all active tenants.

    Called by CI after syncing a new base librechat.yaml to the server.
    For each tenant: re-runs _generate_librechat_yaml with the tenant's MCP servers,
    writes the result, flushes Redis, and restarts the container.
    """
    _require_internal_token(request)

    import docker

    from app.services.provisioning.generators import _generate_librechat_yaml

    base_yaml_path = Path(settings.librechat_container_data_path) / "librechat.yaml"
    if not base_yaml_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Base config not found at {base_yaml_path}",
        )

    result = await db.execute(select(PortalOrg).where(PortalOrg.provisioning_status == "ready"))
    tenants = result.scalars().all()

    updated: list[str] = []
    errors: list[str] = []
    loop = asyncio.get_running_loop()

    # Step 1: Regenerate all tenant configs from the updated base template
    slugs_to_restart: list[str] = []
    for org in tenants:
        slug = org.slug
        if not slug:
            continue
        try:
            tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, org.mcp_servers)
            tenant_yaml_dir = Path(settings.librechat_container_data_path) / slug
            tenant_yaml_dir.mkdir(parents=True, exist_ok=True)
            (tenant_yaml_dir / "librechat.yaml").write_text(tenant_yaml_content)
            slugs_to_restart.append(slug)
            updated.append(slug)
            logger.info("Regenerated config for tenant %s", slug)
        except Exception as exc:
            errors.append(f"{slug}: {exc}")
            logger.warning("Config regeneration failed for %s: %s", slug, exc)

    if not slugs_to_restart:
        return RegenerateResponse(tenants_updated=updated, errors=errors)

    # Step 2: Flush Redis once (clears cached config for all tenants)
    def _flush_and_restart_all(slugs: list[str]) -> list[str]:
        client = docker.from_env()
        restart_errors: list[str] = []

        try:
            redis_ctr = client.containers.get(settings.redis_container_name)
            redis_cmd = ["redis-cli"]
            if settings.redis_password:
                redis_cmd += ["-a", settings.redis_password]
            redis_cmd.append("FLUSHALL")
            exit_code, output = redis_ctr.exec_run(redis_cmd)
            if exit_code != 0:
                logger.warning("Redis FLUSHALL failed (exit %d): %s", exit_code, output.decode())
            else:
                logger.info("Redis FLUSHALL completed")
        except docker.errors.NotFound:  # type: ignore[attr-defined]
            logger.warning("Redis container '%s' not found", settings.redis_container_name)

        # Step 3: Restart all tenant containers
        for slug in slugs:
            container_name = f"librechat-{slug}"
            try:
                ctr = client.containers.get(container_name)
                ctr.restart(timeout=10)
                logger.info("Restarted container %s", container_name)
            except Exception as exc:
                restart_errors.append(f"{slug}: {exc}")
                logger.warning("Restart failed for %s: %s", container_name, exc)

        return restart_errors

    restart_errors = await loop.run_in_executor(None, _flush_and_restart_all, slugs_to_restart)
    errors.extend(restart_errors)

    return RegenerateResponse(tenants_updated=updated, errors=errors)
