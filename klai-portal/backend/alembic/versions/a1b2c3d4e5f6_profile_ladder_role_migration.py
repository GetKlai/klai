"""SPEC-PORTAL-PROFILES-001 REQ-11: migrate role values to five-rung ladder

Revision ID: a1b2c3d4e5f6
Revises: z3a4b5c6d7e8
Create Date: 2026-05-03

Changes:
- Drop old CHECK constraint (admin, group-admin, member)
- UPDATE existing data: group-admin -> group_manager, member -> personal
- Add new CHECK constraint: (personal, company, kb_manager, group_manager, admin)
- Change server_default from admin to company
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None

_OLD_VALUES = ("admin", "group-admin", "member")
_NEW_VALUES = ("personal", "company", "kb_manager", "group_manager", "admin")
_OLD_DEFAULT = "admin"
_NEW_DEFAULT = "company"

_OLD_CHECK = "ck_portal_users_role"
_NEW_CHECK = "ck_portal_users_role_v2"


def upgrade() -> None:
    # 1. Drop the old CHECK constraint
    op.drop_constraint(_OLD_CHECK, "portal_users", type_="check")

    # 2. Widen the column to accommodate new values (group_manager is 13 chars)
    op.alter_column(
        "portal_users",
        "role",
        existing_type=sa.VARCHAR(20),
        type_=sa.VARCHAR(20),
        existing_nullable=False,
    )

    # 3. Migrate data: old -> new role values
    op.execute("UPDATE portal_users SET role = 'group_manager' WHERE role = 'group-admin'")
    op.execute("UPDATE portal_users SET role = 'personal' WHERE role = 'member'")
    # admin stays admin; no update needed for admin

    # 4. Add new CHECK constraint
    op.create_check_constraint(
        _NEW_CHECK,
        "portal_users",
        "role IN ('personal', 'company', 'kb_manager', 'group_manager', 'admin')",
    )

    # 5. Change server_default from admin to company
    op.alter_column(
        "portal_users",
        "role",
        server_default=_NEW_DEFAULT,
        existing_type=sa.VARCHAR(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 1. Drop new CHECK constraint
    op.drop_constraint(_NEW_CHECK, "portal_users", type_="check")

    # 2. Revert data migration
    op.execute("UPDATE portal_users SET role = 'group-admin' WHERE role = 'group_manager'")
    op.execute("UPDATE portal_users SET role = 'member' WHERE role = 'personal'")
    op.execute("UPDATE portal_users SET role = 'member' WHERE role IN ('company', 'kb_manager')")

    # 3. Restore old CHECK constraint
    op.create_check_constraint(
        _OLD_CHECK,
        "portal_users",
        "role IN ('admin', 'group-admin', 'member')",
    )

    # 4. Restore server_default
    op.alter_column(
        "portal_users",
        "role",
        server_default=_OLD_DEFAULT,
        existing_type=sa.VARCHAR(20),
        existing_nullable=False,
    )
