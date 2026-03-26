"""
One-time script: create default product groups for existing orgs that predate SPEC-AUTH-004.

Run via:
    docker exec portal-api uv run python scripts/create_default_groups.py
"""

import asyncio
import logging

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.plans import get_plan_products
from app.logging_setup import setup_logging
from app.models.groups import PortalGroup, PortalGroupProduct
from app.models.portal import PortalOrg

setup_logging("portal-api")
logger = logging.getLogger(__name__)

PRODUCT_GROUP_NAMES: dict[str, str] = {
    "chat": "Chat users",
    "scribe": "Scribe users",
    "knowledge": "Knowledge users",
}


async def run() -> None:
    async with AsyncSessionLocal() as db:
        orgs_result = await db.execute(select(PortalOrg))
        orgs = orgs_result.scalars().all()
        logger.info("Found %d orgs", len(orgs))

        for org in orgs:
            plan_products = get_plan_products(org.plan)
            if not plan_products:
                logger.info("Org %s (plan=%s): no products, skipping", org.slug, org.plan)
                continue

            created = 0
            for product in plan_products:
                group_name = PRODUCT_GROUP_NAMES.get(product)
                if not group_name:
                    continue

                existing = await db.execute(
                    select(PortalGroup).where(
                        PortalGroup.org_id == org.id,
                        PortalGroup.name == group_name,
                    )
                )
                if existing.scalar_one_or_none():
                    logger.info("Org %s: group '%s' already exists, skipping", org.slug, group_name)
                    continue

                group = PortalGroup(org_id=org.id, name=group_name, created_by="system")
                db.add(group)
                await db.flush()
                db.add(
                    PortalGroupProduct(
                        group_id=group.id,
                        org_id=org.id,
                        product=product,
                        enabled_by="system",
                    )
                )
                created += 1
                logger.info("Org %s: created group '%s' with product '%s'", org.slug, group_name, product)

            await db.commit()
            logger.info("Org %s: done (%d groups created)", org.slug, created)

        logger.info("Migration complete")


if __name__ == "__main__":
    asyncio.run(run())
