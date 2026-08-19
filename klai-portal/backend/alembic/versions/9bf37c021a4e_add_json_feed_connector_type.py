"""Add json_feed to the portal connector type constraint.

Revision ID: 9bf37c021a4e
Revises: 8ad64a1112d9
"""

from alembic import op

revision = "9bf37c021a4e"
down_revision = "8ad64a1112d9"
branch_labels = None
depends_on = None

CONNECTOR_TYPES_BEFORE = (
    "github",
    "notion",
    "web_crawler",
    "google_drive",
    "ms_docs",
    "airtable",
    "confluence",
    "google_docs",
    "google_sheets",
    "google_slides",
)
CONNECTOR_TYPES_AFTER = (*CONNECTOR_TYPES_BEFORE, "json_feed")


def _constraint_sql(connector_types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{connector_type}'" for connector_type in connector_types)
    return f"connector_type IN ({values})"


def upgrade() -> None:
    op.drop_constraint("ck_portal_connectors_type", "portal_connectors", type_="check")
    op.create_check_constraint(
        "ck_portal_connectors_type",
        "portal_connectors",
        _constraint_sql(CONNECTOR_TYPES_AFTER),
    )


def downgrade() -> None:
    op.drop_constraint("ck_portal_connectors_type", "portal_connectors", type_="check")
    op.create_check_constraint(
        "ck_portal_connectors_type",
        "portal_connectors",
        _constraint_sql(CONNECTOR_TYPES_BEFORE),
    )
