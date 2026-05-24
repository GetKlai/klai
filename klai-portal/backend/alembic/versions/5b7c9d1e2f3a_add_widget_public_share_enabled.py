"""add widget public share flag column

Revision ID: 5b7c9d1e2f3a
Revises: 34d8f876ffbf
Create Date: 2026-05-24

Move the public share-link toggle out of ``widgets.widget_config`` JSONB
and into a first-class column. The flag controls unauthenticated public
bot-link access, so it should be explicit in schema, defaults, migrations,
and audit/review surfaces instead of being hidden in flexible JSON.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "5b7c9d1e2f3a"
down_revision: str | None = "34d8f876ffbf"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "widgets",
        sa.Column(
            "public_share_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE widgets
        SET public_share_enabled = COALESCE((widget_config ->> 'public_share_enabled')::boolean, false)
        WHERE widget_config ? 'public_share_enabled'
        """
    )
    op.execute(
        """
        UPDATE widgets
        SET widget_config = widget_config - 'public_share_enabled'
        WHERE widget_config ? 'public_share_enabled'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE widgets
        SET widget_config = jsonb_set(
            COALESCE(widget_config, '{}'::jsonb),
            '{public_share_enabled}',
            to_jsonb(public_share_enabled),
            true
        )
        """
    )
    op.drop_column("widgets", "public_share_enabled")
