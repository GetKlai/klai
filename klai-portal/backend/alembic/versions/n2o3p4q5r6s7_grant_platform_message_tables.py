"""grant platform message tables to portal_api

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "n2o3p4q5r6s7"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_message_threads TO portal_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_message_participants TO portal_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_messages TO portal_api")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE platform_message_threads_id_seq TO portal_api")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE platform_messages_id_seq TO portal_api")


def downgrade() -> None:
    op.execute("REVOKE USAGE, SELECT ON SEQUENCE platform_messages_id_seq FROM portal_api")
    op.execute("REVOKE USAGE, SELECT ON SEQUENCE platform_message_threads_id_seq FROM portal_api")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON platform_messages FROM portal_api")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON platform_message_participants FROM portal_api")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON platform_message_threads FROM portal_api")
