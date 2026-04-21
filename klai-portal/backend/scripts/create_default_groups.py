"""
DEPRECATED — one-time SPEC-AUTH-004 migration, already executed on all
environments. Kept in the repo for historical reference only.

This script was written before portal_groups had RLS enabled. As-is it
would fail against the current schema because it opens an AsyncSessionLocal
without calling `await session.connection()` (pin) and without invoking
`set_tenant()` — every INSERT would be silently blocked by the
`tenant_isolation` policy on portal_groups.

If you ever need to re-run the equivalent logic, use the modern helpers:

    from app.core.database import tenant_scoped_session

    async with tenant_scoped_session(org.id) as db:
        ...  # add groups, db.commit()

Do NOT just uncomment the body below. The RLS guard event listener in
app/core/rls_guard.py will catch the silent 0-row INSERTs, but by that
point you've already lost state for half the orgs.

Run via:
    docker exec portal-api uv run python scripts/create_default_groups.py
"""

import asyncio
import logging
import sys

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
    print(
        "ERROR: create_default_groups.py is DEPRECATED and will fail against "
        "the current RLS-enabled schema. See the module docstring for the "
        "modern replacement.",
        file=sys.stderr,
    )
    sys.exit(1)
    asyncio.run(run())  # unreachable; kept so the import graph stays valid
