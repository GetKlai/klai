"""System group definitions and creation helper.

SPEC-PORTAL-PROFILES-001 Phase 2 P2.5: System-groups herinrichting.

Two categories of system groups:

1. Role-bind groups (system_key prefix: "role_"):
   Membership in these groups sets portal_users.role to the corresponding
   role value. Wired in add_member (app/api/groups.py) via
   sync_role_from_system_group().

2. Add-on groups (system_key prefix: "addon_"):
   Membership grants the named add-on product via portal_group_products.
   No separate role logic — relies on the existing entitlement mechanism.

Legacy groups (Chat+Focus, +Scribe, +Knowledge+Docs, old Admin, Group Management)
are migrated away in the a2b3c4d5e6f8 alembic migration.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.models.groups import PortalGroup, PortalGroupProduct

# ---------------------------------------------------------------------------
# System group registry
# ---------------------------------------------------------------------------

# @MX:NOTE: SPEC-PORTAL-PROFILES-001 Phase 2 P2.5 — role-bind groups
# (role_*) set portal_users.role on membership-add. Add-on groups (addon_*)
# grant a product via portal_group_products (existing entitlement mechanism).
SYSTEM_GROUPS: list[dict] = [
    # Role-bind groups — membership triggers role sync via sync_role_from_system_group()
    {"name": "Personal chat", "system_key": "role_personal", "products": [], "role": "personal"},
    {"name": "Company chat", "system_key": "role_company", "products": [], "role": "company"},
    {"name": "Knowledge manager", "system_key": "role_kb_manager", "products": [], "role": "kb_manager"},
    {"name": "Group manager", "system_key": "role_group_manager", "products": [], "role": "group_manager"},
    {"name": "Admin", "system_key": "role_admin", "products": [], "role": "admin"},
    # Add-on groups — membership grants a product via portal_group_products
    {"name": "Scribe users", "system_key": "addon_scribe", "products": ["scribe"], "role": None},
    {"name": "Docs users", "system_key": "addon_docs", "products": ["docs"], "role": None},
]

# Fast lookup: system_key -> role value (for role-bind groups only)
SYSTEM_GROUP_ROLE_MAP: dict[str, str] = {
    sg["system_key"]: sg["role"] for sg in SYSTEM_GROUPS if sg.get("role") is not None
}


async def create_system_groups(org_id: int, db: AsyncSession) -> None:
    """Create all system groups for an org. Idempotent — skips existing ones.

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
