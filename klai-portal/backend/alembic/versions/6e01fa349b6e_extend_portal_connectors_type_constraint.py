"""SPEC-KB-CONNECTORS-001 R6.3 — add airtable, confluence, google_docs, google_sheets, google_slides to CHECK constraint

Revision ID: 6e01fa349b6e
Revises: t3a4b5c6d7e8
Create Date: 2026-04-23 12:01:17.161529

Postgres does not support ALTER CHECK in-place. The migration uses the
DROP + ADD pattern: drop the existing check constraint and recreate it with
the five additional connector_type values.

The downgrade restores the original five-value constraint. Any rows with the
new connector_type values inserted between upgrade and downgrade will violate
the restored constraint and prevent the downgrade from completing.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e01fa349b6e"
down_revision: Union[str, Sequence[str], None] = "t3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_portal_connectors_type", "portal_connectors", type_="check")
    op.create_check_constraint(
        "ck_portal_connectors_type",
        "portal_connectors",
        "connector_type IN ("
        "'github', 'notion', 'web_crawler', 'google_drive', 'ms_docs', "
        "'airtable', 'confluence', "
        "'google_docs', 'google_sheets', 'google_slides'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_portal_connectors_type", "portal_connectors", type_="check")
    op.create_check_constraint(
        "ck_portal_connectors_type",
        "portal_connectors",
        "connector_type IN ('github', 'notion', 'web_crawler', 'google_drive', 'ms_docs')",
    )
