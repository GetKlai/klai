"""add mcp_servers to portal_orgs

Revision ID: d2e3f4a5b6c7
Revises: z2a3b4c5d6e7
Create Date: 2026-04-02

"""

from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column("mcp_servers", sa.JSON(), nullable=True),
    )
    # Data migration: seed Twenty CRM config for getklai tenant.
    # Env var references (${VAR}) are resolved at LibreChat startup, not stored as secrets.
    op.execute(
        """
        UPDATE portal_orgs
        SET mcp_servers = '{"twenty-crm": {"type": "stdio", "command": "/bin/sh", "args": ["-c", "npm install --prefix /tmp/twenty twenty-mcp-server --prefer-offline --silent 2>/dev/null; node /tmp/twenty/node_modules/twenty-mcp-server/dist/index.js"], "timeout": 60000, "initTimeout": 120000, "env": {"TWENTY_API_KEY": "${TWENTY_API_KEY}", "TWENTY_BASE_URL": "${TWENTY_BASE_URL}"}}}'::jsonb
        WHERE slug = 'getklai'
        """
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "mcp_servers")
