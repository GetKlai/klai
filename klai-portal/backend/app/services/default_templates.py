"""Helpers for seeding default prompt templates per tenant.

Every new tenant gets a set of starter templates so the Templates page
is immediately useful.  Templates are org-scoped (scope='global') and
created_by the provisioning user (first admin).

Idempotent: if the tenant already has templates, this is a no-op.
"""

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
) -> None:
    """Seed default templates for a tenant if none exist yet.

    Called from tenant provisioning and lazily from the list endpoint.
    Non-fatal: logs warning on failure.
    """
    try:
        count_result = await db.execute(
            select(func.count())
            .select_from(PortalTemplate)
            .where(
                PortalTemplate.org_id == org_id,
            )
        )
        existing_count = count_result.scalar() or 0

        if existing_count > 0:
            return

        for tmpl in DEFAULT_TEMPLATES:
            db.add(
                PortalTemplate(
                    org_id=org_id,
                    name=tmpl["name"],
                    slug=tmpl["slug"],
                    description=tmpl["description"],
                    prompt_text=tmpl["prompt_text"],
                    scope="global",
                    created_by=created_by,
                )
            )

        await db.flush()
        logger.info("default_templates_seeded", org_id=org_id, count=len(DEFAULT_TEMPLATES))
    except Exception:
        await db.rollback()
        logger.warning("default_templates_seeding_failed", org_id=org_id, exc_info=True)
