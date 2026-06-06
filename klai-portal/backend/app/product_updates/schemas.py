from datetime import datetime

from pydantic import BaseModel

from app.models.product_updates import ProductUpdate


class ProductUpdateOut(BaseModel):
    id: int
    title: str
    body: str
    commit_shas: list[str]
    created_at: datetime
    read_at: datetime | None = None
    unread: bool = False


class ProductUpdatesResponse(BaseModel):
    items: list[ProductUpdateOut]
    unread_count: int = 0


def product_update_out(update: ProductUpdate, read_at: datetime | None = None) -> ProductUpdateOut:
    return ProductUpdateOut(
        id=update.id,
        title=update.title,
        body=update.body,
        commit_shas=list(update.commit_shas or []),
        created_at=update.created_at,
        read_at=read_at,
        unread=read_at is None,
    )
