"""Helpers for seeding default policy/guardrail rules per tenant.

Every new tenant gets a set of starter rules so the Rules page is
immediately useful. Rules are org-scoped (scope='global') and
created_by the provisioning user (first admin).

Idempotent: if the tenant already has rules, this is a no-op.
"""

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rules import PortalRule

logger = structlog.get_logger()

DEFAULT_RULES: list[dict[str, str]] = [
    {
        "name": "Altijd in het Nederlands",
        "slug": "altijd-in-het-nederlands",
        "description": "Antwoord standaard in het Nederlands",
        "rule_text": "Antwoord altijd in het Nederlands, tenzij de gebruiker expliciet om een andere taal vraagt. Bij twijfel: Nederlands.",
    },
    {
        "name": "Geen persoonsgegevens delen",
        "slug": "geen-persoonsgegevens-delen",
        "description": "Bescherm persoonsgegevens (GDPR/AVG)",
        "rule_text": "Deel geen persoonsgegevens (namen, e-mailadressen, telefoonnummers, BSN) uit de kennisbank met externe partijen. Maskeer ze in antwoorden tenzij de gebruiker expliciet bevoegd is.",
    },
    {
        "name": "Geen juridisch of medisch advies",
        "slug": "geen-juridisch-of-medisch-advies",
        "description": "Geen definitief advies op gereguleerde terreinen",
        "rule_text": "Geef geen definitief juridisch, medisch of fiscaal advies. Verwijs bij dit soort vragen altijd door naar een gekwalificeerde professional.",
    },
    {
        "name": "Onderbouw met bronnen",
        "slug": "onderbouw-met-bronnen",
        "description": "Citeer de kennisbank bij feitelijke claims",
        "rule_text": "Wanneer je feitelijke claims doet op basis van de kennisbank, verwijs naar de specifieke bron. Als je iets niet uit de bronnen kunt halen, zeg dat eerlijk.",
    },
    {
        "name": "Houd antwoorden kort en bondig",
        "slug": "houd-antwoorden-kort-en-bondig",
        "description": "Geen onnodige uitweidingen",
        "rule_text": "Houd antwoorden zo kort mogelijk zonder kerninformatie weg te laten. Geen inleidende zinnen zoals 'Natuurlijk!' of 'Goede vraag!'. Ga direct naar het antwoord.",
    },
]


async def ensure_default_rules(
    org_id: int,
    created_by: str,
    db: AsyncSession,
) -> None:
    """Seed default rules for a tenant if none exist yet.

    Called from tenant provisioning and lazily from the list endpoint.
    Non-fatal: logs warning on failure.
    """
    try:
        count_result = await db.execute(
            select(func.count()).select_from(PortalRule).where(
                PortalRule.org_id == org_id,
            )
        )
        existing_count = count_result.scalar() or 0

        if existing_count > 0:
            return

        for rule in DEFAULT_RULES:
            db.add(
                PortalRule(
                    org_id=org_id,
                    name=rule["name"],
                    slug=rule["slug"],
                    description=rule["description"],
                    rule_text=rule["rule_text"],
                    scope="global",
                    created_by=created_by,
                )
            )

        await db.flush()
        logger.info("default_rules_seeded", org_id=org_id, count=len(DEFAULT_RULES))
    except Exception:
        await db.rollback()
        logger.warning("default_rules_seeding_failed", org_id=org_id, exc_info=True)
