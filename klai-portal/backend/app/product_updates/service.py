from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_updates import ProductUpdate, ProductUpdateRead

_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_DEDUPE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ProductUpdateNotFoundError(Exception):
    pass


class ProductUpdateValidationError(ValueError):
    pass


def normalize_commit_shas(commit_shas: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in commit_shas or []:
        sha = value.strip().lower()
        if not sha:
            continue
        if not _COMMIT_SHA_RE.fullmatch(sha):
            raise ProductUpdateValidationError("Commit SHAs must be 7-40 hexadecimal characters")
        if sha not in seen:
            seen.add(sha)
            normalized.append(sha)
    return normalized


def normalize_dedupe_key(value: str | None) -> str | None:
    if value is None:
        return None
    dedupe_key = value.strip()
    if not dedupe_key:
        return None
    if not _DEDUPE_KEY_RE.fullmatch(dedupe_key):
        raise ProductUpdateValidationError(
            "Dedupe key must be 1-128 characters and use letters, numbers, '.', '_', ':' or '-'"
        )
    return dedupe_key


def generated_dedupe_key(*, title: str, body: str, commit_shas: list[str]) -> str:
    payload = "\n".join([title.strip(), body.strip(), ",".join(commit_shas)])
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


async def create_product_update(
    db: AsyncSession,
    *,
    title: str,
    body: str,
    commit_shas: list[str] | None = None,
    created_at: datetime | None = None,
    created_by_user_id: str | None = None,
    dedupe_key: str | None = None,
    published_via: str = "operator_script",
) -> ProductUpdate:
    normalized_commit_shas = normalize_commit_shas(commit_shas)
    normalized_dedupe_key = normalize_dedupe_key(dedupe_key) or generated_dedupe_key(
        title=title,
        body=body,
        commit_shas=normalized_commit_shas,
    )
    existing = await db.scalar(select(ProductUpdate).where(ProductUpdate.dedupe_key == normalized_dedupe_key))
    if existing is not None:
        return existing

    update = ProductUpdate(
        title=title.strip(),
        body=body.strip(),
        commit_shas=normalized_commit_shas,
        created_by_user_id=created_by_user_id,
        dedupe_key=normalized_dedupe_key,
        published_via=published_via,
    )
    if created_at is not None:
        update.created_at = created_at
    db.add(update)
    await db.flush()
    return update


async def list_product_updates_for_user(
    db: AsyncSession,
    *,
    org_id: int,
    user_id: str,
    limit: int,
) -> list[tuple[ProductUpdate, datetime | None]]:
    safe_limit = max(1, min(limit, 100))
    read_match = and_(
        ProductUpdateRead.product_update_id == ProductUpdate.id,
        ProductUpdateRead.org_id == org_id,
        ProductUpdateRead.user_id == user_id,
    )
    rows = (
        await db.execute(
            select(ProductUpdate, ProductUpdateRead.read_at)
            .outerjoin(ProductUpdateRead, read_match)
            .order_by(ProductUpdate.created_at.desc(), ProductUpdate.id.desc())
            .limit(safe_limit)
        )
    ).all()
    return [(update, read_at) for update, read_at in rows]


async def mark_product_update_read(
    db: AsyncSession,
    *,
    product_update_id: int,
    org_id: int,
    user_id: str,
) -> ProductUpdateRead:
    update_exists = await db.scalar(select(ProductUpdate.id).where(ProductUpdate.id == product_update_id))
    if update_exists is None:
        raise ProductUpdateNotFoundError()

    await db.execute(
        insert(ProductUpdateRead)
        .values(
            product_update_id=product_update_id,
            org_id=org_id,
            user_id=user_id,
            read_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(
            index_elements=[
                ProductUpdateRead.product_update_id,
                ProductUpdateRead.org_id,
                ProductUpdateRead.user_id,
            ]
        )
    )
    read = (
        await db.execute(
            select(ProductUpdateRead).where(
                ProductUpdateRead.product_update_id == product_update_id,
                ProductUpdateRead.org_id == org_id,
                ProductUpdateRead.user_id == user_id,
            )
        )
    ).scalar_one()
    return read


async def mark_all_product_updates_read(
    db: AsyncSession,
    *,
    org_id: int,
    user_id: str,
) -> tuple[int, datetime]:
    read_at = datetime.now(UTC)
    rows = await list_product_updates_for_user(db, org_id=org_id, user_id=user_id, limit=100)
    unread_updates = [update for update, existing_read_at in rows if existing_read_at is None]
    read_count = 0
    for update in unread_updates:
        result = await db.execute(
            insert(ProductUpdateRead)
            .values(
                product_update_id=update.id,
                org_id=org_id,
                user_id=user_id,
                read_at=read_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ProductUpdateRead.product_update_id,
                    ProductUpdateRead.org_id,
                    ProductUpdateRead.user_id,
                ]
            )
        )
        read_count += max(getattr(result, "rowcount", 0) or 0, 0)
    return read_count, read_at
