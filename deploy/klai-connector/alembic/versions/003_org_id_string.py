"""Change org_id from UUID to VARCHAR to match Zitadel resourceowner ID format.

Revision ID: 003_org_id_string
Revises: 002_timestamptz
Create Date: 2026-03-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003_org_id_string"
down_revision: str | None = "002_timestamptz"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "connectors",
        "org_id",
        type_=sa.String(255),
        postgresql_using="org_id::text",
        schema="connector",
    )


def downgrade() -> None:
    op.alter_column(
        "connectors",
        "org_id",
        type_=sa.dialects.postgresql.UUID(as_uuid=True),
        postgresql_using="org_id::uuid",
        schema="connector",
    )
