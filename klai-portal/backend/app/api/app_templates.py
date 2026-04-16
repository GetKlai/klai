"""App-facing API for prompt templates.

GET    /api/app/templates          -- list templates (personal + org-scope)
POST   /api/app/templates          -- create a template
GET    /api/app/templates/{slug}   -- get single template
PATCH  /api/app/templates/{slug}   -- update (owner or admin)
DELETE /api/app/templates/{slug}   -- delete (owner or admin)
"""

import re

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.templates import PortalTemplate
from app.services.default_templates import ensure_default_templates

logger = structlog.get_logger()

router = APIRouter(prefix="/api/app/templates", tags=["app-templates"])


# -- Pydantic schemas ---------------------------------------------------------


class TemplateCreate(BaseModel):
    name: str = Field(..., max_length=128)
    description: str | None = None
    prompt_text: str
    scope: str = "global"


class TemplatePatch(BaseModel):
    name: str | None = Field(None, max_length=128)
    description: str | None = None
    prompt_text: str | None = None
    scope: str | None = None
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


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"[-\s]+", "-", slug).strip("-")[:64]


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


async def _get_template_or_404(slug: str, org_id: int, db: AsyncSession) -> PortalTemplate:
    """Fetch a template within the org, or raise 404."""
    result = await db.execute(
        select(PortalTemplate).where(
            PortalTemplate.org_id == org_id,
            PortalTemplate.slug == slug,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return template


# -- Endpoints ----------------------------------------------------------------


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[TemplateOut]:
    """List templates visible to the caller: all global + own personal."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)

    # Lazy-seed defaults for existing orgs that have no templates yet
    await ensure_default_templates(org.id, zitadel_user_id, db)
    await db.commit()

    result = await db.execute(
        select(PortalTemplate).where(
            PortalTemplate.org_id == org.id,
            # Show global templates + caller's own personal templates
            (PortalTemplate.scope == "global") | (PortalTemplate.created_by == zitadel_user_id),
        )
    )
    templates = result.scalars().all()
    return [_template_out(t) for t in templates]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Create a new prompt template."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)

    if body.scope not in ("global", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'global' or 'personal'",
        )

    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name must produce a valid slug",
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
    db.add(template)
    try:
        await db.commit()
        await db.refresh(template)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A template with slug '{slug}' already exists in this organisation",
        ) from exc

    logger.info("Template created", template_id=template.id, slug=slug, org_id=org.id)
    return _template_out(template)


@router.get("/{slug}", response_model=TemplateOut)
async def get_template(
    slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Get a single template by slug."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)
    template = await _get_template_or_404(slug, org.id, db)

    # Personal templates are only visible to the creator
    if template.scope == "personal" and template.created_by != zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    return _template_out(template)


@router.patch("/{slug}", response_model=TemplateOut)
async def update_template(
    slug: str,
    body: TemplatePatch,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Update a template. Only the creator or an admin can update."""
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    template = await _get_template_or_404(slug, org.id, db)

    # Only owner or admin can update
    if template.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the creator or an admin can update this template")

    if body.name is not None:
        template.name = body.name
        # Update slug when name changes
        new_slug = _slugify(body.name)
        if new_slug and new_slug != template.slug:
            template.slug = new_slug

    if "description" in body.model_fields_set:
        template.description = body.description

    if body.prompt_text is not None:
        template.prompt_text = body.prompt_text

    if body.scope is not None:
        if body.scope not in ("global", "personal"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scope must be 'global' or 'personal'",
            )
        template.scope = body.scope

    if body.is_active is not None:
        template.is_active = body.is_active

    try:
        await db.commit()
        await db.refresh(template)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with that slug already exists in this organisation",
        ) from exc

    logger.info("Template updated", template_id=template.id, slug=template.slug, org_id=org.id)
    return _template_out(template)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a template. Only the creator or an admin can delete."""
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    template = await _get_template_or_404(slug, org.id, db)

    # Only owner or admin can delete
    if template.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the creator or an admin can delete this template")

    await db.delete(template)
    await db.commit()
    logger.info("Template deleted", slug=slug, org_id=org.id)
