"""Effective prompt templates of one employee (SPEC-CHAT-TEMPLATES-001).

One resolution for both readers: ``GET /internal/templates/effective`` (the
LiteLLM hook, until slice 9 removes it) and the internal chat in portal-api.
The caller must have set the tenant for ``org_id``: ``portal_templates`` has a
strict tenant_isolation policy.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portal import PortalOrg, PortalUser
from app.models.templates import PortalTemplate


async def effective_template_instructions(
    db: AsyncSession, org_id: int, template_ids: list[int] | None
) -> list[dict[str, str]]:
    """``[{"source": "template", "name", "text"}]`` in the order the user chose.

    Inactive, deleted and other-org templates are skipped silently.
    """
    if not template_ids:
        return []
    rows = await db.execute(
        select(PortalTemplate).where(
            PortalTemplate.org_id == org_id,
            PortalTemplate.id.in_(template_ids),
            PortalTemplate.is_active.is_(True),
        )
    )
    by_id = {template.id: template for template in rows.scalars().all()}
    return [
        {"source": "template", "name": by_id[tid].name, "text": by_id[tid].prompt_text}
        for tid in template_ids
        if tid in by_id
    ]


async def internal_turn_settings(
    db: AsyncSession, org_id: int, zitadel_user_id: str
) -> tuple[list[dict[str, str]], str]:
    """The employee's effective templates and the org's telemetry level."""
    row = (
        await db.execute(
            select(PortalUser.active_template_ids, PortalOrg.telemetry_level)
            .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
            .where(PortalUser.org_id == org_id, PortalUser.zitadel_user_id == zitadel_user_id)
        )
    ).one()
    template_ids, telemetry_level = row
    return await effective_template_instructions(db, org_id, template_ids), telemetry_level
