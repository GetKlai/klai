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
from app.models.groups import PortalGroup, PortalGroupProduct
from app.models.portal import PortalOrg

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PRODUCT_GROUP_NAMES: dict[str, str] = {
    "chat": "Chat users",
    "scribe": "Scribe users",
    "knowledge": "Knowledge users",
}


async def run() -> None:
    async with AsyncSessionLocal() as db:
        orgs_result = await db.execute(select(PortalOrg))
        orgs = orgs_result.scalars().all()
        log.info("Found %d orgs", len(orgs))

        for org in orgs:
            plan_products = get_plan_products(org.plan)
            if not plan_products:
                log.info("Org %s (plan=%s): no products, skipping", org.slug, org.plan)
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
                    log.info("Org %s: group '%s' already exists, skipping", org.slug, group_name)
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
                log.info("Org %s: created group '%s' with product '%s'", org.slug, group_name, product)

            await db.commit()
            log.info("Org %s: done (%d groups created)", org.slug, created)

        log.info("Migration complete")


if __name__ == "__main__":
    asyncio.run(run())
