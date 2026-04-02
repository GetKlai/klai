"""fix mcp_servers seed data: vervang stdio formaat door streamable-http

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-04-02

Corrigeert de verouderde stdio seed data in portal_orgs.mcp_servers voor de
getklai tenant. Vervangt door het correcte streamable-http formaat zoals
vereist door REQ-N-003 en AC-M2-03.

TWENTY_API_KEY wordt gelezen uit de TWENTY_API_KEY environment variable op
migratietijd en geencrypt met encrypt_mcp_secret(). Als de env var ontbreekt,
wordt mcp_servers op None gezet zodat handmatige configuratie via Portal UI
mogelijk blijft (fallback conform plan.md R-003).
"""

import json
import logging
import os

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    twenty_api_key = os.environ.get("TWENTY_API_KEY", "").strip()
    twenty_base_url = os.environ.get("TWENTY_BASE_URL", "https://crm.getklai.com").strip()

    if not twenty_api_key:
        # Fallback: mcp_servers op None -- handmatige configuratie via Portal UI
        logger.warning(
            "TWENTY_API_KEY niet gevonden in environment. "
            "mcp_servers voor getklai wordt op None gezet. "
            "Configureer via Portal UI na de migratie."
        )
        op.execute(
            sa.text(
                "UPDATE portal_orgs SET mcp_servers = NULL WHERE slug = 'getklai'"
            )
        )
        return

    # Encrypt de API key met de portal secrets service
    # Import hier (niet op module-niveau) zodat de alembic env setup
    # al geladen is voordat wij de app-modules aanspreken.
    from app.services.secrets import encrypt_mcp_secret

    encrypted_key = encrypt_mcp_secret(twenty_api_key)

    new_mcp_servers = {
        "twenty-crm": {
            "enabled": True,
            "env": {
                "TWENTY_API_KEY": encrypted_key,
                "TWENTY_BASE_URL": twenty_base_url,
            },
        }
    }

    op.execute(
        sa.text(
            "UPDATE portal_orgs "
            "SET mcp_servers = :mcp_servers ::jsonb "
            "WHERE slug = 'getklai'"
        ).bindparams(mcp_servers=json.dumps(new_mcp_servers))
    )

    logger.info(
        "mcp_servers voor getklai bijgewerkt naar streamable-http formaat "
        "(TWENTY_API_KEY encrypted, TWENTY_BASE_URL=%s)",
        twenty_base_url,
    )


def downgrade() -> None:
    # Zet mcp_servers terug naar None (de staat vóór handmatige seed correctie).
    # De originele stdio seed data uit d2e3f4a5b6c7 is vervangen door deze migratie;
    # het terugzetten naar None is de veiligste downgrade zonder het oorspronkelijke
    # plaintext token te reconstrueren.
    op.execute(
        sa.text(
            "UPDATE portal_orgs SET mcp_servers = NULL WHERE slug = 'getklai'"
        )
    )
