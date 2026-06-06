"""Platform-admin product update publishing endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.database import cross_org_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.product_updates.schemas import ProductUpdateOut, product_update_out
from app.product_updates.service import (
    ProductUpdateValidationError,
    create_product_update,
    normalize_commit_shas,
    normalize_dedupe_key,
)

router = APIRouter(prefix="/platform/product-updates", tags=["platform-admin-product-updates"])


class ProductUpdateCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., min_length=1, max_length=240)
    body: str = Field(..., min_length=1, max_length=4000)
    commit_shas: list[str] = Field(default_factory=list, max_length=50)
    created_at: datetime | None = None
    dedupe_key: str | None = Field(default=None, max_length=128)

    @field_validator("commit_shas")
    @classmethod
    def _validate_commit_shas(cls, value: list[str]) -> list[str]:
        try:
            return normalize_commit_shas(value)
        except ProductUpdateValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("dedupe_key")
    @classmethod
    def _validate_dedupe_key(cls, value: str | None) -> str | None:
        try:
            return normalize_dedupe_key(value)
        except ProductUpdateValidationError as exc:
            raise ValueError(str(exc)) from exc


@router.post("", response_model=ProductUpdateOut, status_code=status.HTTP_201_CREATED)
async def create_product_update_endpoint(
    body: ProductUpdateCreateIn,
    _perms: UserPermissions = Depends(require_platform_admin()),
) -> ProductUpdateOut:
    async with cross_org_session() as db:
        try:
            update = await create_product_update(
                db,
                title=body.title,
                body=body.body,
                commit_shas=body.commit_shas,
                created_at=body.created_at,
                created_by_user_id=_perms.user_id,
                dedupe_key=body.dedupe_key,
                published_via="admin_api",
            )
        except ProductUpdateValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        result = product_update_out(update, read_at=None)
        await db.commit()
        return result
