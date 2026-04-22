"""App-facing API for policy/guardrail rules.

GET    /api/app/rules          -- list rules (personal + org-scope)
POST   /api/app/rules          -- create a rule
GET    /api/app/rules/{slug}   -- get single rule
PATCH  /api/app/rules/{slug}   -- update (owner or admin)
DELETE /api/app/rules/{slug}   -- delete (owner or admin)
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
from app.models.rules import PortalRule
from app.services.default_rules import ensure_default_rules

logger = structlog.get_logger()

router = APIRouter(prefix="/api/app/rules", tags=["app-rules"])

# Allowed rule_type values. Rules are strictly guardrails — block or redact
# sensitive content. Prompt instructions live in Templates, not Rules.
ALLOWED_RULE_TYPES: frozenset[str] = frozenset(
    {
        "pii_block",
        "pii_redact",
        "keyword_block",
        "keyword_redact",
    }
)


# -- Pydantic schemas ---------------------------------------------------------


class RuleCreate(BaseModel):
    name: str = Field(..., max_length=128)
    description: str | None = None
    rule_text: str
    scope: str = "global"
    rule_type: str = "pii_redact"


class RulePatch(BaseModel):
    name: str | None = Field(None, max_length=128)
    description: str | None = None
    rule_text: str | None = None
    scope: str | None = None
    is_active: bool | None = None
    rule_type: str | None = None


class RuleOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    rule_text: str
    rule_type: str
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


def _rule_out(r: PortalRule) -> RuleOut:
    return RuleOut(
        id=r.id,
        name=r.name,
        slug=r.slug,
        description=r.description,
        rule_text=r.rule_text,
        rule_type=r.rule_type,
        scope=r.scope,
        created_by=r.created_by,
        is_active=r.is_active,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )


def _validate_rule_type(rule_type: str) -> None:
    """Raise 400 if the rule_type is not one of the allowed values."""
    if rule_type not in ALLOWED_RULE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"rule_type must be one of: {sorted(ALLOWED_RULE_TYPES)}",
        )


async def _get_rule_or_404(slug: str, org_id: int, db: AsyncSession) -> PortalRule:
    """Fetch a rule within the org, or raise 404."""
    result = await db.execute(
        select(PortalRule).where(
            PortalRule.org_id == org_id,
            PortalRule.slug == slug,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


# -- Endpoints ----------------------------------------------------------------


@router.get("", response_model=list[RuleOut])
async def list_rules(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[RuleOut]:
    """List rules visible to the caller: all global + own personal."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)

    # Lazy-seed defaults for existing orgs that have no rules yet
    await ensure_default_rules(org.id, zitadel_user_id, db)
    await db.commit()

    result = await db.execute(
        select(PortalRule).where(
            PortalRule.org_id == org.id,
            # Show global rules + caller's own personal rules
            (PortalRule.scope == "global") | (PortalRule.created_by == zitadel_user_id),
        )
    )
    rules = result.scalars().all()
    return [_rule_out(r) for r in rules]


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    """Create a new policy/guardrail rule."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)

    if body.scope not in ("global", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'global' or 'personal'",
        )

    _validate_rule_type(body.rule_type)

    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name must produce a valid slug",
        )

    rule = PortalRule(
        org_id=org.id,
        name=body.name,
        slug=slug,
        description=body.description,
        rule_text=body.rule_text,
        rule_type=body.rule_type,
        scope=body.scope,
        created_by=zitadel_user_id,
    )
    db.add(rule)
    try:
        await db.commit()
        await db.refresh(rule)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A rule with slug '{slug}' already exists in this organisation",
        ) from exc

    logger.info("rule_created", rule_id=rule.id, slug=slug, org_id=org.id)
    return _rule_out(rule)


@router.get("/{slug}", response_model=RuleOut)
async def get_rule(
    slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    """Get a single rule by slug."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)
    rule = await _get_rule_or_404(slug, org.id, db)

    # Personal rules are only visible to the creator
    if rule.scope == "personal" and rule.created_by != zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    return _rule_out(rule)


@router.patch("/{slug}", response_model=RuleOut)
async def update_rule(
    slug: str,
    body: RulePatch,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    """Update a rule. Only the creator or an admin can update."""
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    rule = await _get_rule_or_404(slug, org.id, db)

    # Only owner or admin can update
    if rule.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the creator or an admin can update this rule"
        )

    if body.name is not None:
        rule.name = body.name
        # Update slug when name changes
        new_slug = _slugify(body.name)
        if new_slug and new_slug != rule.slug:
            rule.slug = new_slug

    if "description" in body.model_fields_set:
        rule.description = body.description

    if body.rule_text is not None:
        rule.rule_text = body.rule_text

    if body.scope is not None:
        if body.scope not in ("global", "personal"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scope must be 'global' or 'personal'",
            )
        rule.scope = body.scope

    if body.is_active is not None:
        rule.is_active = body.is_active

    if body.rule_type is not None:
        _validate_rule_type(body.rule_type)
        rule.rule_type = body.rule_type

    try:
        await db.commit()
        await db.refresh(rule)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rule with that slug already exists in this organisation",
        ) from exc

    logger.info("rule_updated", rule_id=rule.id, slug=rule.slug, org_id=org.id)
    return _rule_out(rule)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a rule. Only the creator or an admin can delete."""
    zitadel_user_id, org, caller = await _get_caller_org(credentials, db)
    rule = await _get_rule_or_404(slug, org.id, db)

    # Only owner or admin can delete
    if rule.created_by != zitadel_user_id and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the creator or an admin can delete this rule"
        )

    await db.delete(rule)
    await db.commit()
    logger.info("rule_deleted", slug=slug, org_id=org.id)
