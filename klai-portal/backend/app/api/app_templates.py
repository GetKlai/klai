"""App-facing CRUD API for prompt templates.

    GET    /api/app/templates          list templates (personal + org-scope)
    POST   /api/app/templates          create (admin required for scope="org")
    GET    /api/app/templates/{slug}   fetch one
    PATCH  /api/app/templates/{slug}   update (owner or admin)
    DELETE /api/app/templates/{slug}   delete (owner or admin)

Per SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CRUD:
- Admin-gate on scope="org" POST (NL message on 403).
- Rate-limit 10 req/s per org via partner_rate_limit Redis sliding-window.
- Cache-invalidation on every write: SCAN+DEL for org-scope, single DEL
  for personal-scope. Fire-and-forget.
- Slug derived server-side from name via shared slugify().
- Personal-scope visibility: creator + org-admins only.

# @MX:NOTE: Rate-limit uses partner_rate_limit's 1-minute sliding window at
# limit=600 (≈10/s averaged). Catches sustained abuse, not true per-second
# bursts — good enough for CRUD. Bump if abuse patterns require tighter control.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.templates import PortalTemplate
from app.services.default_templates import ensure_default_templates
from app.services.litellm_cache import invalidate_templates
from app.services.partner_rate_limit import check_rate_limit
from app.services.redis_client import get_redis_pool
from app.utils.slug import slugify

logger = structlog.get_logger()

router = APIRouter(prefix="/api/app/templates", tags=["app-templates"])


# SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CRUD-U3: 10 req/s averaged over a
# 1-minute window = 600 writes/min. Bursty-allowed, sustained-throttled.
_RATE_LIMIT_PER_MINUTE = 600


# -- Pydantic schemas ---------------------------------------------------------


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=500)
    prompt_text: str = Field(..., min_length=1, max_length=8000)
    scope: str = Field("org", pattern="^(org|personal)$")


class TemplatePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=500)
    prompt_text: str | None = Field(None, min_length=1, max_length=8000)
    scope: str | None = Field(None, pattern="^(org|personal)$")
    is_active: bool | None = None


class TemplateOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    prompt_text: str
    scope: str
    created_by: str
    is_active: bool
    created_at: str
    updated_at: str


# -- Helpers ------------------------------------------------------------------


def _template_out(t: PortalTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        name=t.name,
        slug=t.slug,
        description=t.description,
        prompt_text=t.prompt_text,
        scope=t.scope,
        created_by=t.created_by,
        is_active=t.is_active,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
    )


async def _enforce_rate_limit(org_id: int) -> None:
    """Raise 429 if the org exceeds the CRUD write rate-limit.

    Fail-open: Redis unavailable → allow the request (matches partner_rate_limit
    philosophy: availability beats strict enforcement for non-critical paths).
    """
    pool = await get_redis_pool()
    if pool is None:
        return
    try:
        allowed, retry_after = await check_rate_limit(pool, f"templates_rl:{org_id}", _RATE_LIMIT_PER_MINUTE)
    except Exception:
        logger.warning("templates_rate_limit_redis_error", org_id=org_id, exc_info=True)
        return
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Te veel templates-wijzigingen in korte tijd. Probeer het zo opnieuw.",
            headers={"Retry-After": str(retry_after)},
        )


async def _get_template_or_404(slug: str, org_id: int, db: AsyncSession) -> PortalTemplate:
    result = await db.execute(
        select(PortalTemplate).where(
            PortalTemplate.org_id == org_id,
            PortalTemplate.slug == slug,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template niet gevonden")
    return template


def _librechat_user_id_or_none(user) -> str | None:
    """PortalUser.librechat_user_id may be None until the user's first chat call."""
    return getattr(user, "librechat_user_id", None)


# -- Endpoints ----------------------------------------------------------------


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[TemplateOut]:
    """List templates visible to the caller: all org + own personal.

    Admins additionally see every personal-scope template in their org.
    Lazy-seeds the 4 defaults if the org has zero templates
    (REQ-TEMPLATES-CRUD-E7).
    """
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)

    # Lazy-seed fallback for orgs provisioned before this feature landed
    # or whose provisioning step raised. Idempotent via row-count check.
    seeded = await ensure_default_templates(org.id, "system", db)
    if seeded:
        await db.commit()

    is_admin = caller.role == "admin"

    if is_admin:
        # Admins see everything in their org.
        stmt = select(PortalTemplate).where(PortalTemplate.org_id == org.id)
    else:
        stmt = select(PortalTemplate).where(
            PortalTemplate.org_id == org.id,
            or_(
                PortalTemplate.scope == "org",
                PortalTemplate.created_by == zitadel_user_id,
            ),
        )

    result = await db.execute(stmt)
    return [_template_out(t) for t in result.scalars().all()]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Create a new prompt template."""
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    await _enforce_rate_limit(org.id)

    # REQ-TEMPLATES-CRUD-E1: admin-gate on scope="org".
    if body.scope == "org" and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alleen beheerders mogen organisatie-templates aanmaken",
        )

    slug = slugify(body.name)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Naam moet een geldige slug opleveren",
        )

    template = PortalTemplate(
        org_id=org.id,
        name=body.name,
        slug=slug,
        description=body.description,
        prompt_text=body.prompt_text,
        scope=body.scope,
        created_by=zitadel_user_id,
    )
    # CREATE pattern (see .claude/rules/klai/projects/portal-security.md —
    # "Post-commit db.refresh on RLS tables"): flush + refresh BEFORE commit
    # so the server-default timestamps/is_active fields are loaded while the
    # tenant GUC is still active on the transaction. After commit, attributes
    # persist in memory because AsyncSessionLocal uses expire_on_commit=False.
    db.add(template)
    try:
        await db.flush()
        await db.refresh(template)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Er bestaat al een template met slug '{slug}' in deze organisatie",
        ) from exc

    logger.info(
        "template_created",
        template_id=template.id,
        slug=slug,
        scope=template.scope,
        org_id=org.id,
    )

    # Cache invalidation.
    if template.scope == "org":
        await invalidate_templates(org.id)
    else:
        # Personal: only the creator's cache needs dropping.
        lc_uid = _librechat_user_id_or_none(caller)
        if lc_uid:
            await invalidate_templates(org.id, lc_uid)

    return _template_out(template)


@router.get("/{slug}", response_model=TemplateOut)
async def get_template(
    slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    template = await _get_template_or_404(slug, org.id, db)

    # Personal templates are only visible to the creator + admins.
    if template.scope == "personal" and template.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template niet gevonden")

    return _template_out(template)


@router.patch("/{slug}", response_model=TemplateOut)
async def update_template(
    slug: str,
    body: TemplatePatch,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Update a template. Only the creator or an org-admin may update."""
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    await _enforce_rate_limit(org.id)
    template = await _get_template_or_404(slug, org.id, db)

    if template.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alleen de maker of een beheerder mag deze template aanpassen",
        )

    # Admin-gate still applies when promoting a personal template to org-scope.
    if body.scope == "org" and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alleen beheerders mogen organisatie-templates aanmaken",
        )

    previous_scope = template.scope

    if body.name is not None:
        template.name = body.name
        new_slug = slugify(body.name)
        if not new_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Naam moet een geldige slug opleveren",
            )
        template.slug = new_slug

    if "description" in body.model_fields_set:
        template.description = body.description

    if body.prompt_text is not None:
        template.prompt_text = body.prompt_text

    if body.scope is not None:
        template.scope = body.scope

    if body.is_active is not None:
        template.is_active = body.is_active

    # UPDATE pattern (see .claude/rules/klai/projects/portal-security.md —
    # "Post-commit db.refresh on RLS tables"). Flush + refresh BEFORE commit,
    # same as the CREATE path. The generic rule ("drop refresh entirely for
    # UPDATE") assumes every mutated column stays Python-assigned, but
    # `PortalTemplate.updated_at` has `onupdate=func.now()` — SQLAlchemy
    # generates the new timestamp server-side and marks the attribute as
    # expired after flush. Without a refresh, `_template_out` touches
    # `template.updated_at` and triggers a lazy SELECT that fires outside the
    # async greenlet context → `sqlalchemy.exc.MissingGreenlet` → HTTP 500.
    # The refresh runs inside the tenant-scoped transaction so RLS still sees
    # the row.
    try:
        await db.flush()
        await db.refresh(template)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Er bestaat al een template met deze slug in deze organisatie",
        ) from exc

    logger.info(
        "template_updated",
        template_id=template.id,
        slug=template.slug,
        scope=template.scope,
        org_id=org.id,
    )

    # Any scope change, or an org-scope write, affects the whole org.
    if template.scope == "org" or previous_scope == "org":
        await invalidate_templates(org.id)
    else:
        lc_uid = _librechat_user_id_or_none(caller)
        if lc_uid:
            await invalidate_templates(org.id, lc_uid)

    return _template_out(template)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    await _enforce_rate_limit(org.id)
    template = await _get_template_or_404(slug, org.id, db)

    if template.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alleen de maker of een beheerder mag deze template verwijderen",
        )

    scope = template.scope
    template_id = template.id
    await db.delete(template)
    await db.commit()

    logger.info(
        "template_deleted",
        template_id=template_id,
        slug=slug,
        scope=scope,
        org_id=org.id,
    )

    if scope == "org":
        await invalidate_templates(org.id)
    else:
        lc_uid = _librechat_user_id_or_none(caller)
        if lc_uid:
            await invalidate_templates(org.id, lc_uid)
