"""System groups herinrichting: retire legacy groups, create role-bind + add-on groups.

Revision ID: 261dae89162c
Revises: a0174b86ace3
Create Date: 2026-05-03

SPEC-PORTAL-PROFILES-001 Phase 2 P2.5.
"""

import sqlalchemy as sa
from alembic import op

revision = "261dae89162c"
down_revision = "a0174b86ace3"
branch_labels = None
depends_on = None

_LEGACY_KEYS = ("admin", "group_management", "chat", "scribe", "knowledge")

_NEW_GROUPS = [
    {"name": "Personal chat",     "system_key": "role_personal"},
    {"name": "Company chat",      "system_key": "role_company"},
    {"name": "Knowledge manager", "system_key": "role_kb_manager"},
    {"name": "Group manager",     "system_key": "role_group_manager"},
    {"name": "Admin",             "system_key": "role_admin"},
    {"name": "Scribe users",      "system_key": "addon_scribe"},
    {"name": "Docs users",        "system_key": "addon_docs"},
]

_ADDON_PRODUCTS = {
    "addon_scribe": "scribe",
    "addon_docs": "docs",
}


def upgrade() -> None:
    conn = op.get_bind()

    legacy_ids_result = conn.execute(
        sa.text("SELECT id FROM portal_groups WHERE is_system = true AND system_key = ANY(:keys)"),
        {"keys": list(_LEGACY_KEYS)},
    )
    legacy_ids = [row[0] for row in legacy_ids_result.fetchall()]

    if legacy_ids:
        conn.execute(
            sa.text("DELETE FROM portal_group_products WHERE group_id = ANY(:ids)"),
            {"ids": legacy_ids},
        )
        conn.execute(
            sa.text("DELETE FROM portal_group_memberships WHERE group_id = ANY(:ids)"),
            {"ids": legacy_ids},
        )
        conn.execute(
            sa.text("DELETE FROM portal_groups WHERE id = ANY(:ids)"),
            {"ids": legacy_ids},
        )

    orgs_result = conn.execute(sa.text("SELECT id FROM portal_orgs"))
    org_ids = [row[0] for row in orgs_result.fetchall()]

    for org_id in org_ids:
        for sg in _NEW_GROUPS:
            existing = conn.execute(
                sa.text(
                    "SELECT id FROM portal_groups"
                    " WHERE org_id = :org_id AND system_key = :key AND is_system = true"
                ),
                {"org_id": org_id, "key": sg["system_key"]},
            ).fetchone()

            if existing:
                group_id = existing[0]
            else:
                result = conn.execute(
                    sa.text(
                        "INSERT INTO portal_groups (org_id, name, is_system, system_key, created_by)"
                        " VALUES (:org_id, :name, true, :key, 'migration') RETURNING id"
                    ),
                    {"org_id": org_id, "name": sg["name"], "key": sg["system_key"]},
                )
                group_id = result.fetchone()[0]

            product = _ADDON_PRODUCTS.get(sg["system_key"])
            if product:
                conn.execute(
                    sa.text(
                        "INSERT INTO portal_group_products (group_id, org_id, product, enabled_by)"
                        " VALUES (:gid, :oid, :product, 'migration')"
                        " ON CONFLICT DO NOTHING"
                    ),
                    {"gid": group_id, "oid": org_id, "product": product},
                )


def downgrade() -> None:
    conn = op.get_bind()

    new_keys = [sg["system_key"] for sg in _NEW_GROUPS]
    new_ids_result = conn.execute(
        sa.text("SELECT id FROM portal_groups WHERE is_system = true AND system_key = ANY(:keys)"),
        {"keys": new_keys},
    )
    new_ids = [row[0] for row in new_ids_result.fetchall()]

    if new_ids:
        conn.execute(
            sa.text("DELETE FROM portal_group_products WHERE group_id = ANY(:ids)"),
            {"ids": new_ids},
        )
        conn.execute(
            sa.text("DELETE FROM portal_group_memberships WHERE group_id = ANY(:ids)"),
            {"ids": new_ids},
        )
        conn.execute(
            sa.text("DELETE FROM portal_groups WHERE id = ANY(:ids)"),
            {"ids": new_ids},
        )
