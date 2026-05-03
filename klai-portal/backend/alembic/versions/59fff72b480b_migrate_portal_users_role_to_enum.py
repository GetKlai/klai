"""Migrate portal_users.role from VARCHAR+CHECK to Postgres ENUM type.

Revision ID: 59fff72b480b
Revises: a1b2c3d4e5f6
Create Date: 2026-05-03

SPEC-PORTAL-PROFILES-001 Phase 1.6 C3.

Replaces the VARCHAR(20) + CHECK constraint approach introduced in a1b2c3d4e5f6
with a real Postgres ENUM type (portal_user_role). Gives DB-level type-safety:
only the five valid role strings are accepted by Postgres, without needing a
CHECK constraint.

Down migration reverts to VARCHAR(20) + the v2 CHECK constraint from a1b2c3d4e5f6.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "59fff72b480b"
down_revision: str = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

_ENUM_NAME = "portal_user_role"
_ENUM_VALUES = ("personal", "company", "kb_manager", "group_manager", "admin")
_CHECK_NAME = "ck_portal_users_role_v2"


def upgrade() -> None:
    # 1. Create the ENUM type
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    # Rationale: _ENUM_NAME is a module-level constant, not user input.
    op.execute(  # nosemgrep
        "CREATE TYPE portal_user_role AS ENUM ('personal', 'company', 'kb_manager', 'group_manager', 'admin')"
    )

    # 2. Drop the old CHECK constraint (a1b2c3d4e5f6 added ck_portal_users_role_v2)
    op.drop_constraint(_CHECK_NAME, "portal_users", type_="check")

    # 3. Alter column to use the ENUM type (raw DDL required; SQLAlchemy has no
    #    ALTER COLUMN ... USING syntax).
    op.execute(  # nosemgrep
        "ALTER TABLE portal_users ALTER COLUMN role TYPE portal_user_role USING role::portal_user_role"
    )

    # 4. Set server_default using the typed cast
    op.alter_column(
        "portal_users",
        "role",
        server_default="'company'::portal_user_role",
        existing_type=sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME, create_type=False),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 1. Change column back to VARCHAR(20) with text cast
    op.execute(  # nosemgrep
        "ALTER TABLE portal_users ALTER COLUMN role TYPE VARCHAR(20) USING role::text"
    )

    # 2. Restore server_default as plain string
    op.alter_column(
        "portal_users",
        "role",
        server_default="company",
        existing_type=sa.VARCHAR(20),
        existing_nullable=False,
    )

    # 3. Restore the CHECK constraint from a1b2c3d4e5f6
    op.create_check_constraint(
        _CHECK_NAME,
        "portal_users",
        "role IN ('personal', 'company', 'kb_manager', 'group_manager', 'admin')",
    )

    # 4. Drop the ENUM type
    op.execute("DROP TYPE portal_user_role")  # nosemgrep
