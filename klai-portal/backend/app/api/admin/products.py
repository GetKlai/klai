"""Admin product entitlement endpoints."""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.plans import get_plan_products
from app.models.groups import PortalGroup, PortalGroupMembership, PortalGroupProduct
from app.models.portal import PortalUser
from app.models.products import PortalUserProduct
from app.services.audit import log_event

from . import _get_caller_org, _require_admin, bearer

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    message: str


class ProductAssignRequest(BaseModel):
    product: str


class ProductOut(BaseModel):
    product: str
    enabled_at: datetime
    enabled_by: str


class ProductsResponse(BaseModel):
    products: list[str]


class UserProductsResponse(BaseModel):
    products: list[ProductOut]


class EffectiveProductOut(BaseModel):
    product: str
    source: Literal["direct", "group"]
    source_name: str | None = None


class EffectiveProductsResponse(BaseModel):
    products: list[EffectiveProductOut]


class ProductSummaryItem(BaseModel):
    product: str
    user_count: int


class ProductSummaryResponse(BaseModel):
    items: list[ProductSummaryItem]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/products", response_model=ProductsResponse)
async def list_available_products(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProductsResponse:
    """Return products available under the org's current plan."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    return ProductsResponse(products=get_plan_products(org.plan))


@router.post("/users/{zitadel_user_id}/products", status_code=status.HTTP_201_CREATED)
async def assign_product(
    zitadel_user_id: str,
    body: ProductAssignRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Assign a product to a user within plan ceiling."""
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Plan ceiling check
    if body.product not in get_plan_products(org.plan):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Product '{body.product}' exceeds plan ceiling",
        )

    # Check user belongs to this org
    user = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check for duplicate
    existing = await db.scalar(
        select(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.product == body.product,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product already assigned")

    db.add(
        PortalUserProduct(
            zitadel_user_id=zitadel_user_id,
            org_id=org.id,
            product=body.product,
            enabled_by=admin_user_id,
        )
    )
    await db.flush()
    await log_event(
        db,
        org_id=org.id,
        actor=admin_user_id,
        action="product.assigned",
        resource_type="product",
        resource_id=f"{zitadel_user_id}:{body.product}",
        details={"product": body.product, "user_id": zitadel_user_id},
    )
    await db.commit()
    return MessageResponse(message="Product assigned")


@router.delete("/users/{zitadel_user_id}/products/{product}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_product(
    zitadel_user_id: str,
    product: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a product from a user."""
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.product == product,
            PortalUserProduct.org_id == org.id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product assignment not found")

    await db.delete(row)
    await log_event(
        db,
        org_id=org.id,
        actor=admin_user_id,
        action="product.revoked",
        resource_type="product",
        resource_id=f"{zitadel_user_id}:{product}",
        details={"product": product, "user_id": zitadel_user_id},
    )
    await db.commit()


@router.get("/products/summary", response_model=ProductSummaryResponse)
async def product_summary(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProductSummaryResponse:
    """Return per-product user counts for the org."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    rows = await db.execute(
        select(PortalUserProduct.product, func.count().label("user_count"))
        .where(PortalUserProduct.org_id == org.id)
        .group_by(PortalUserProduct.product)
    )
    return ProductSummaryResponse(items=[ProductSummaryItem(product=r.product, user_count=r.user_count) for r in rows])


@router.get("/users/{zitadel_user_id}/products", response_model=UserProductsResponse)
async def get_user_products(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserProductsResponse:
    """Return products assigned to a specific user."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Verify user belongs to org
    user = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.execute(
        select(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.org_id == org.id,
        )
    )
    products = result.scalars().all()
    return UserProductsResponse(
        products=[ProductOut(product=p.product, enabled_at=p.enabled_at, enabled_by=p.enabled_by) for p in products]
    )


@router.get("/users/{zitadel_user_id}/effective-products", response_model=EffectiveProductsResponse)
async def get_user_effective_products(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> EffectiveProductsResponse:
    """Return effective products for a user with source attribution (direct or group name)."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Verify user belongs to org
    user = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    products: list[EffectiveProductOut] = []
    seen: set[str] = set()

    # Direct assignments
    direct_result = await db.execute(
        select(PortalUserProduct).where(PortalUserProduct.zitadel_user_id == zitadel_user_id)
    )
    for row in direct_result.scalars().all():
        if row.product not in seen:
            products.append(EffectiveProductOut(product=row.product, source="direct"))
            seen.add(row.product)

    # Group-inherited assignments
    group_result = await db.execute(
        select(PortalGroupProduct, PortalGroup.name.label("group_name"))
        .join(PortalGroupMembership, PortalGroupProduct.group_id == PortalGroupMembership.group_id)
        .join(PortalGroup, PortalGroupProduct.group_id == PortalGroup.id)
        .where(PortalGroupMembership.zitadel_user_id == zitadel_user_id)
    )
    for row in group_result.all():
        group_product, group_name = row[0], row[1]
        if group_product.product not in seen:
            products.append(
                EffectiveProductOut(
                    product=group_product.product,
                    source="group",
                    source_name=group_name,
                )
            )
            seen.add(group_product.product)

    return EffectiveProductsResponse(products=products)
