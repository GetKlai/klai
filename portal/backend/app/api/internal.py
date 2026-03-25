"""
Internal service-to-service endpoints.

Only accessible within the Docker network (klai-net) — never exposed publicly.
Protected by a shared Bearer secret (INTERNAL_SECRET env var).

Used by klai-mailer to look up a user's preferred language so it can append
?lang= to email action URLs (verify, password-reset, etc.).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalUser
from app.services.entitlements import get_effective_products
from app.services.zitadel import zitadel

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
    if not user_id:
        return UserLanguageResponse(preferred_language="nl")

    result = await db.execute(select(PortalUser.preferred_language).where(PortalUser.zitadel_user_id == user_id))
    lang = result.scalar_one_or_none()
    return UserLanguageResponse(preferred_language=lang or "nl")


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

    products = await get_effective_products(zitadel_user_id, db)
    return UserProductsResponse(products=products)


class ConnectorConfigResponse(BaseModel):
    connector_id: str
    kb_id: int
    kb_slug: str
    org_id: int
    connector_type: str
    config: dict
    schedule: str | None
    is_enabled: bool


@router.get("/connectors/{connector_id}", response_model=ConnectorConfigResponse)
async def get_connector_config(
    connector_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ConnectorConfigResponse:
    """Return connector config for klai-connector service."""
    _require_internal_token(request)
    result = await db.execute(
        select(PortalConnector, PortalKnowledgeBase)
        .join(PortalKnowledgeBase, PortalConnector.kb_id == PortalKnowledgeBase.id)
        .where(PortalConnector.id == connector_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    connector, kb = row
    return ConnectorConfigResponse(
        connector_id=str(connector.id),
        kb_id=connector.kb_id,
        kb_slug=kb.slug,
        org_id=connector.org_id,
        connector_type=connector.connector_type,
        config=connector.config,
        schedule=connector.schedule,
        is_enabled=connector.is_enabled,
    )
