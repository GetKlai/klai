"""Admin product endpoints.

SPEC-PORTAL-RBAC-001 v0.2.0: per-user/per-group product assignment is removed.
Products are derived from (profile, plan, enabled_addons) -- see
`app.core.features.derive_user_products`. The legacy assignment endpoints
return 410 Gone. Two read-only endpoints remain because the frontend uses
them for the assignable-products list and the per-user effective view.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.features import derive_user_products
from app.models.portal import PortalUser
from app.services.entitlements import get_effective_products

from . import _get_caller_org, _require_admin, bearer

router = APIRouter()


_GONE_BODY = (
    "Endpoint removed by SPEC-PORTAL-RBAC-001. Products derive from "
    "/admin/settings (plan + add-ons) and /admin/users/<id>/edit (profile)."
)


# ---------------------------------------------------------------------------
# Schemas (read-only views)
# ---------------------------------------------------------------------------


class ProductsResponse(BaseModel):
    products: list[str]


class EffectiveProductOut(BaseModel):
    product: str
    source: Literal["plan", "addon"]


class EffectiveProductsResponse(BaseModel):
    products: list[EffectiveProductOut]


# ---------------------------------------------------------------------------
# Read-only endpoints (remain)
# ---------------------------------------------------------------------------


@router.get("/products", response_model=ProductsResponse)
async def list_available_products(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProductsResponse:
    """Return products available to the caller given (profile, org plan, enabled add-ons)."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    products = derive_user_products(
        role=caller_user.role,
        plan=org.plan,
        enabled_addons=list(org.enabled_addons or []),
    )
    return ProductsResponse(products=sorted(products))


@router.get("/users/{zitadel_user_id}/effective-products", response_model=EffectiveProductsResponse)
async def get_user_effective_products(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> EffectiveProductsResponse:
    """Return effective products for a user with source attribution (plan or addon)."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    target = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    effective = await get_effective_products(zitadel_user_id, db)
    enabled_addons = set(org.enabled_addons or [])
    return EffectiveProductsResponse(
        products=[
            EffectiveProductOut(
                product=p,
                source="addon" if p in enabled_addons else "plan",
            )
            for p in effective
        ]
    )


# ---------------------------------------------------------------------------
# Removed endpoints -- 410 Gone
# ---------------------------------------------------------------------------


@router.post("/users/{zitadel_user_id}/products", status_code=status.HTTP_410_GONE)
async def assign_product_gone(zitadel_user_id: str) -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)


@router.delete("/users/{zitadel_user_id}/products/{product}", status_code=status.HTTP_410_GONE)
async def revoke_product_gone(zitadel_user_id: str, product: str) -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)


@router.get("/users/{zitadel_user_id}/products", status_code=status.HTTP_410_GONE)
async def get_user_products_gone(zitadel_user_id: str) -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)


@router.get("/products/summary", status_code=status.HTTP_410_GONE)
async def product_summary_gone() -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)
