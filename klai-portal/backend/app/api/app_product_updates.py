"""App-facing product update endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.product_updates.schemas import ProductUpdatesResponse, product_update_out
from app.product_updates.service import (
    ProductUpdateNotFoundError,
    list_product_updates_for_user,
    mark_all_product_updates_read,
    mark_product_update_read,
)

router = APIRouter(prefix="/api/app/product-updates", tags=["app-product-updates"])


class ProductUpdateReadResponse(BaseModel):
    ok: bool = True
    product_update_id: int
    read_at: datetime


class ProductUpdateReadAllResponse(BaseModel):
    ok: bool = True
    read_count: int
    read_at: datetime


@router.get("", response_model=ProductUpdatesResponse)
async def get_product_updates(
    limit: int = 50,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> ProductUpdatesResponse:
    rows = await list_product_updates_for_user(db, org_id=perms.org_id, user_id=perms.user_id, limit=limit)
    items = [product_update_out(update, read_at) for update, read_at in rows]
    return ProductUpdatesResponse(items=items, unread_count=sum(1 for item in items if item.unread))


@router.post("/{product_update_id}/read", response_model=ProductUpdateReadResponse)
async def mark_product_update_read_endpoint(
    product_update_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> ProductUpdateReadResponse:
    try:
        read = await mark_product_update_read(
            db,
            product_update_id=product_update_id,
            org_id=perms.org_id,
            user_id=perms.user_id,
        )
    except ProductUpdateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product update not found") from exc
    await db.commit()
    return ProductUpdateReadResponse(product_update_id=product_update_id, read_at=read.read_at)


@router.post("/read-all", response_model=ProductUpdateReadAllResponse)
async def mark_all_product_updates_read_endpoint(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> ProductUpdateReadAllResponse:
    read_count, read_at = await mark_all_product_updates_read(db, org_id=perms.org_id, user_id=perms.user_id)
    if read_count:
        await db.commit()
    return ProductUpdateReadAllResponse(read_count=read_count, read_at=read_at)
