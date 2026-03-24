"""encrypt tenant secrets as BYTEA

Revision ID: t0u1v2w3x4y5
Revises: r8s9t0u1v2w3
Create Date: 2026-03-24

"""

from alembic import op

revision = "t0u1v2w3x4y5"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Convert plaintext TEXT columns to BYTEA for encrypted storage.
    #
    # This uses a simple ALTER COLUMN ... TYPE BYTEA USING ... which sets
    # existing values to NULL.  This is acceptable because:
    #   1. No production rows contain real secrets yet (system is pre-launch).
    #   2. Any existing dev/staging rows with plaintext will be cleared.
    #
    # For a production system with existing data, run a data migration script
    # BEFORE this migration that reads each plaintext value, encrypts it with
    # PortalSecretsService, and writes the ciphertext bytes back.
    op.execute("""
        ALTER TABLE portal_orgs
            ALTER COLUMN zitadel_librechat_client_secret DROP DEFAULT,
            ALTER COLUMN zitadel_librechat_client_secret TYPE BYTEA USING NULL,
            ALTER COLUMN litellm_team_key DROP DEFAULT,
            ALTER COLUMN litellm_team_key TYPE BYTEA USING NULL
    """)


def downgrade() -> None:
    # Revert to TEXT columns.  Encrypted data cannot be automatically converted
    # back to plaintext, so existing values are discarded.
    op.execute("""
        ALTER TABLE portal_orgs
            ALTER COLUMN zitadel_librechat_client_secret TYPE TEXT USING NULL,
            ALTER COLUMN litellm_team_key TYPE TEXT USING NULL
    """)
