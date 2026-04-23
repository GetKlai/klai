"""System group definitions and creation helper."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.models.groups import PortalGroup, PortalGroupProduct

# Five system groups every org gets, regardless of plan.
# system_key identifies purpose; products are granted by the group.
SYSTEM_GROUPS = [
    {"name": "Admin", "system_key": "admin", "products": []},
    {"name": "Group Management", "system_key": "group_management", "products": []},
    {"name": "Chat", "system_key": "chat", "products": ["chat"]},
    {"name": "+ Scribe", "system_key": "scribe", "products": ["chat", "scribe"]},
    {"name": "+ Knowledge + Docs", "system_key": "knowledge", "products": ["chat", "scribe", "knowledge"]},
]


async def create_system_groups(org_id: int, db: AsyncSession) -> None:
    """Create all 5 system groups for an org. Idempotent — skips existing ones.

    Requires a pinned DB connection on the session (caller must have awaited
    pin_session() or session.connection()); otherwise set_tenant() below may
    land on a different pooled connection than the subsequent INSERTs and RLS
    will block them.
    """
    # Provisioning runs with the admin's org_id in the session; override it so
    # RLS WITH CHECK (derived from USING) accepts inserts for the new tenant.
    await set_tenant(db, org_id)
    # Find which system_keys already exist for this org
    existing = await db.execute(
        select(PortalGroup.system_key).where(
            PortalGroup.org_id == org_id,
            PortalGroup.is_system.is_(True),
        )
    )
    existing_keys = {row[0] for row in existing.fetchall()}

    groups_to_create: list[tuple[PortalGroup, list[str]]] = []
    for sg in SYSTEM_GROUPS:
        if sg["system_key"] in existing_keys:
            continue
        group = PortalGroup(
            org_id=org_id,
            name=sg["name"],
            is_system=True,
            system_key=sg["system_key"],
            created_by="system",
        )
        db.add(group)
        groups_to_create.append((group, sg["products"]))

    if not groups_to_create:
        return

    await db.flush()  # get IDs

    for group, products in groups_to_create:
        for product in products:
            db.add(
                PortalGroupProduct(
                    group_id=group.id,
                    org_id=org_id,
                    product=product,
                    enabled_by="system",
                )
            )

    await db.commit()
