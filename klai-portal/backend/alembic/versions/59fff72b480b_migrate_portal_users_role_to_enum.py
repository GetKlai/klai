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

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "59fff72b480b"
down_revision: str = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

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

    # 3. Drop the old VARCHAR server_default before changing type. Postgres
    #    won't auto-cast a VARCHAR default to the new ENUM type and will
    #    raise DatatypeMismatchError on ALTER TYPE if a default is still set.
    op.execute("ALTER TABLE portal_users ALTER COLUMN role DROP DEFAULT")  # nosemgrep

    # 4. Alter column to use the ENUM type (raw DDL required; SQLAlchemy has no
    #    ALTER COLUMN ... USING syntax).
    op.execute(  # nosemgrep
        "ALTER TABLE portal_users ALTER COLUMN role TYPE portal_user_role USING role::portal_user_role"
    )

    # 5. Set server_default with the typed cast now that the column is ENUM
    op.execute(  # nosemgrep
        "ALTER TABLE portal_users ALTER COLUMN role SET DEFAULT 'company'::portal_user_role"
    )


def downgrade() -> None:
    # 1. Drop the typed default before changing type back
    op.execute("ALTER TABLE portal_users ALTER COLUMN role DROP DEFAULT")  # nosemgrep

    # 2. Change column back to VARCHAR(20) with text cast
    op.execute(  # nosemgrep
        "ALTER TABLE portal_users ALTER COLUMN role TYPE VARCHAR(20) USING role::text"
    )

    # 3. Restore server_default as plain string
    op.execute(  # nosemgrep
        "ALTER TABLE portal_users ALTER COLUMN role SET DEFAULT 'company'"
    )

    # 4. Restore the CHECK constraint from a1b2c3d4e5f6
    op.create_check_constraint(
        _CHECK_NAME,
        "portal_users",
        "role IN ('personal', 'company', 'kb_manager', 'group_manager', 'admin')",
    )

    # 5. Drop the ENUM type
    op.execute("DROP TYPE portal_user_role")  # nosemgrep
