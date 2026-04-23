"""Seed default prompt templates per tenant.

Every new tenant gets 4 starter templates (Klantenservice / Formeel /
Creatief / Samenvatter) so the Templates page is immediately useful.
Templates are org-scoped (``scope="org"``) and ``created_by="system"``.

Idempotent via a row-count check: if the tenant already has one or
more templates, this is a no-op. Called from two places:

1. ``app.services.provisioning.orchestrator`` (step ``defaults_templates``,
   non-fatal on failure).
2. ``app.api.app_templates`` GET list endpoint (lazy-seed fallback for
   orgs that existed before this feature landed or whose provisioning
   step failed).

# @MX:NOTE: Template slugs and prompt_text are product content. Changes
# here change the default seed for EVERY new org. The 4 starter
# templates were chosen by the product team — don't edit the Dutch
# prompts without approval.
"""

from __future__ import annotations

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.templates import PortalTemplate

logger = structlog.get_logger()


DEFAULT_TEMPLATES: list[dict[str, str]] = [
    {
        "name": "Klantenservice",
        "slug": "klantenservice",
        "description": "Vriendelijke, behulpzame toon voor klantcontact",
        "prompt_text": (
            "Je bent een behulpzame klantenservicemedewerker. "
            "Antwoord altijd in het Nederlands. Gebruik een vriendelijke en professionele toon. "
            "Houd antwoorden kort en bondig. Bied proactief oplossingen aan. "
            "Als je het antwoord niet weet, zeg dat eerlijk en verwijs door naar de juiste afdeling."
        ),
    },
    {
        "name": "Formeel",
        "slug": "formeel",
        "description": "Zakelijke, professionele schrijfstijl",
        "prompt_text": (
            "Schrijf in een formele, professionele toon. "
            "Gebruik volledige zinnen en vermijd informeel taalgebruik. "
            "Structureer je antwoord duidelijk met alinea's. "
            "Geschikt voor zakelijke communicatie, rapporten en officiële documenten."
        ),
    },
    {
        "name": "Creatief",
        "slug": "creatief",
        "description": "Originele, inspirerende schrijfstijl",
        "prompt_text": (
            "Schrijf op een creatieve en inspirerende manier. "
            "Gebruik beeldspraak, variatie in zinslengte en een vlotte stijl. "
            "Denk buiten de gebaande paden en bied verrassende invalshoeken. "
            "Geschikt voor blogposts, social media en marketingteksten."
        ),
    },
    {
        "name": "Samenvatter",
        "slug": "samenvatter",
        "description": "Vat lange teksten bondig samen",
        "prompt_text": (
            "Vat de aangeleverde tekst samen in heldere, beknopte punten. "
            "Gebruik een bullet-list voor de belangrijkste inzichten. "
            "Bewaar de kernboodschap en laat details weg. "
            "Sluit af met een conclusie van maximaal twee zinnen."
        ),
    },
]


async def ensure_default_templates(
    org_id: int,
    created_by: str,
    db: AsyncSession,
) -> int:
    """Seed default templates for a tenant if none exist yet.

    Returns the number of templates inserted (0 if the tenant already had any).
    Non-fatal: any exception is logged and swallowed — callers MUST NOT
    depend on this for correctness.

    Call sites MUST have called ``set_tenant(org_id)`` on the session
    beforehand so RLS admits the COUNT and the inserts.
    """
    try:
        count_result = await db.execute(
            select(func.count())
            .select_from(PortalTemplate)
            .where(PortalTemplate.org_id == org_id)
        )
        existing_count = count_result.scalar() or 0

        if existing_count > 0:
            return 0

        for tmpl in DEFAULT_TEMPLATES:
            db.add(
                PortalTemplate(
                    org_id=org_id,
                    name=tmpl["name"],
                    slug=tmpl["slug"],
                    description=tmpl["description"],
                    prompt_text=tmpl["prompt_text"],
                    scope="org",
                    created_by=created_by,
                )
            )

        await db.flush()
        logger.info(
            "default_templates_seeded", org_id=org_id, count=len(DEFAULT_TEMPLATES)
        )
        return len(DEFAULT_TEMPLATES)
    except Exception:
        await db.rollback()
        logger.warning(
            "default_templates_seeding_failed", org_id=org_id, exc_info=True
        )
        return 0
