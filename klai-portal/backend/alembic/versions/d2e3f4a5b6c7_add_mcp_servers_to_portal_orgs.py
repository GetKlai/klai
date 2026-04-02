"""add mcp_servers to portal_orgs

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-04-02

"""

from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column("mcp_servers", sa.JSON(), nullable=True),
    )
    # Data migration: seed Twenty CRM config for the getklai tenant.
    # Env var references (${VAR}) are resolved at LibreChat container startup,
    # not stored as actual secrets.
    op.execute(
        """
        UPDATE portal_orgs
        SET mcp_servers = '{"twenty-crm": {"type": "stdio", "command": "npx", "args": ["-y", "twenty-mcp-server", "start"], "timeout": 60000, "initTimeout": 30000, "env": {"TWENTY_API_KEY": "${TWENTY_API_KEY}", "TWENTY_BASE_URL": "${TWENTY_BASE_URL}"}}}'::jsonb
        WHERE slug = 'getklai'
        """
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "mcp_servers")
