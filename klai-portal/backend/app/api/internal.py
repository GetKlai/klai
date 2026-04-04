"""
Internal service-to-service endpoints.

Only accessible within the Docker network (klai-net) — never exposed publicly.
Protected by a shared Bearer secret (INTERNAL_SECRET env var).

Used by klai-mailer to look up a user's preferred language so it can append
?lang= to email action URLs (verify, password-reset, etc.).

Used by the LiteLLM knowledge hook (KB-010) to check knowledge product entitlement
and perform lazy LibreChat MongoDB ObjectId → Zitadel user ID mapping.
"""

import logging
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.services.entitlements import get_effective_products
from app.services.gap_rescorer import schedule_rescore
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
    return ConnectorConfigResponse(
        connector_id=str(connector.id),
        kb_id=connector.kb_id,
        kb_slug=kb.slug,
        zitadel_org_id=org.zitadel_org_id,
        connector_type=connector.connector_type,
        config=connector.config,
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


class GapEventIn(BaseModel):
    org_id: str  # Zitadel org ID from LiteLLM team key metadata
    user_id: str
    query_text: str
    gap_type: str
    top_score: float | None = None
    nearest_kb_slug: str | None = None
    chunks_retrieved: int = 0
    retrieval_ms: int = 0


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
    )
    db.add(gap)
    await db.commit()
    return {"ok": True}
