"""Admin product endpoints.

SPEC-PORTAL-RBAC-001 v0.2.0: per-user/per-group product assignment is removed.
Products are derived from (profile, plan, enabled_addons) -- see
`app.core.features.derive_user_products`. The legacy assignment endpoints
return 410 Gone. Two read-only endpoints remain because the frontend uses
them for the assignable-products list and the per-user effective view.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.features import derive_user_products
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.models.portal import PortalUser
from app.services.entitlements import get_effective_products

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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ProductsResponse:
    """Return products available to the caller given (profile, org plan, enabled add-ons).

    UserPermissions already carries `effective_products` derived from the
    same (role, plan, enabled_addons) input — return that instead of
    re-deriving. Equivalent to calling `derive_user_products` from this
    handler in earlier code, just sourced one layer up.
    """
    return ProductsResponse(products=sorted(perms.effective_products))


@router.get("/users/{zitadel_user_id}/effective-products", response_model=EffectiveProductsResponse)
async def get_user_effective_products(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> EffectiveProductsResponse:
    """Return effective products for a user with source attribution (plan or addon)."""
    target = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Derive products specifically for the TARGET user with the caller's
    # tenant config — same shape `get_effective_products` would return,
    # but routed through `derive_user_products` so we can also classify
    # each product as plan- vs addon-sourced for the response.
    effective = sorted(
        derive_user_products(
            role=target.role,
            plan=perms.plan,
            enabled_addons=list(perms.enabled_addons),
        )
    )
    enabled_addons = set(perms.enabled_addons)
    # Keep the canonical resolver as a sanity sentinel so any future drift
    # between derive_user_products and get_effective_products surfaces here
    # rather than at the user-visible response.
    canonical = await get_effective_products(zitadel_user_id, db)
    if set(canonical) != set(effective):
        # Resolver disagreement is a developer-facing bug, not user input.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="effective products derivation mismatch",
        )
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
