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

# Rules = guardrails (block / redact). Prompt instructions go in Templates.
DEFAULT_RULES: list[dict[str, str]] = [
    {
        "name": "E-mailadressen redacten",
        "slug": "e-mailadressen-redacten",
        "description": "Vervang e-mailadressen in gebruikersinput door [EMAIL]",
        "rule_text": "",
        "rule_type": "pii_redact",
    },
    {
        "name": "BSN redacten",
        "slug": "bsn-redacten",
        "description": "Vervang Nederlandse BSN-nummers door [BSN]",
        "rule_text": "",
        "rule_type": "pii_redact",
    },
    {
        "name": "IBAN-nummers redacten",
        "slug": "iban-redacten",
        "description": "Vervang IBAN-nummers door [IBAN]",
        "rule_text": "",
        "rule_type": "pii_redact",
    },
    {
        "name": "Creditcardnummers blokkeren",
        "slug": "creditcardnummers-blokkeren",
        "description": "Blokkeer berichten die creditcardnummers bevatten",
        "rule_text": "",
        "rule_type": "pii_block",
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
            select(func.count())
            .select_from(PortalRule)
            .where(
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
                    rule_type=rule["rule_type"],
                    scope="global",
                    created_by=created_by,
                )
            )

        await db.flush()
        logger.info("default_rules_seeded", org_id=org_id, count=len(DEFAULT_RULES))
    except Exception:
        await db.rollback()
        logger.warning("default_rules_seeding_failed", org_id=org_id, exc_info=True)
