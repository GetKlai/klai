"""add portal_support_cases + support-gap columns on portal_retrieval_gaps

SPEC-RAG-SUPPORT-GAP. Application-role-safe DDL only (portal_api):

- ``portal_support_cases`` is created here as pure DDL. Its RLS enablement
  (``OWNER TO klai`` + Cat-D ``tenant_isolation`` policy) and the
  ``portal_retrieval_gaps.support_case_id`` FK (ON DELETE CASCADE — deleting a
  case removes its derived findings) live in
  ``post_deploy_s1p2c3a4s5e6_support_cases_rls.sql``, applied as the klai
  superuser (portal_api is not the table owner and has no REFERENCES privilege
  on the new klai-owned table).
- ``portal_retrieval_gaps`` is portal_api-owned, so its new nullable columns
  (metadata-only ADD COLUMN) and CHECK changes are safe to run here. The
  ``support_case_id`` column is added here (plain BigInteger, no FK); the FK is
  attached in the post-deploy SQL.
- The ``portal_connectors`` ``connector_type`` CHECK is widened to accept
  ``hubspot_support``. DROP ... IF EXISTS + recreate the full list makes it
  independent of the exact prior constraint state across parallel branches.

Revision ID: s1p2c3a4s5e6
Revises: 6a0a2f1c33d6
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "s1p2c3a4s5e6"
down_revision = "6a0a2f1c33d6"
branch_labels = None
depends_on = None

_CONNECTOR_TYPES = (
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
    "json_feed",
    "hubspot_support",
)

_INBOX_DIAGNOSES = ("missing", "incomplete", "outdated", "contradictory", "findability", "audience")


def upgrade() -> None:
    op.create_table(
        "portal_support_cases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kb_slug", sa.String(64), nullable=False),
        sa.Column("connector_id", UUID(as_uuid=False), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("analysis_version", sa.String(64), nullable=True),
        sa.Column("analysis", JSONB(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "org_id", "kb_slug", "source", "account_id", "external_id", name="uq_portal_support_cases_identity"
        ),
        sa.CheckConstraint("source IN ('hubspot', 'audio')", name="ck_portal_support_cases_source"),
        sa.CheckConstraint(
            "status IN ('incomplete', 'pending', 'analyzed', 'failed')", name="ck_portal_support_cases_status"
        ),
    )
    op.create_index("ix_portal_support_cases_org_kb", "portal_support_cases", ["org_id", "kb_slug"])
    op.create_index("ix_portal_support_cases_connector", "portal_support_cases", ["connector_id"])

    # portal_retrieval_gaps: new nullable support-gap columns (metadata-only).
    op.add_column("portal_retrieval_gaps", sa.Column("support_case_id", sa.BigInteger(), nullable=True))
    op.add_column("portal_retrieval_gaps", sa.Column("diagnosis", sa.String(24), nullable=True))
    op.add_column("portal_retrieval_gaps", sa.Column("question_key", sa.String(), nullable=True))
    op.add_column("portal_retrieval_gaps", sa.Column("audience", sa.String(16), nullable=True))
    op.add_column("portal_retrieval_gaps", sa.Column("evidence", JSONB(), nullable=True))
    op.create_index("ix_retrieval_gaps_support_case", "portal_retrieval_gaps", ["support_case_id"])

    # Widen gap_type to add the fixed 'content' value for case-backed findings.
    op.drop_constraint("ck_retrieval_gaps_gap_type", "portal_retrieval_gaps", type_="check")
    op.create_check_constraint(
        "ck_retrieval_gaps_gap_type",
        "portal_retrieval_gaps",
        "gap_type IN ('hard', 'soft', 'content')",
    )
    # Diagnosis is one of the six inbox diagnoses, or NULL for legacy telemetry.
    diag_values = ", ".join(f"'{d}'" for d in _INBOX_DIAGNOSES)
    op.create_check_constraint(
        "ck_retrieval_gaps_diagnosis",
        "portal_retrieval_gaps",
        f"diagnosis IS NULL OR diagnosis IN ({diag_values})",
    )

    # Widen the connector_type CHECK to accept hubspot_support, independent of
    # the exact prior constraint state.
    conn_values = ", ".join(f"'{t}'" for t in _CONNECTOR_TYPES)
    op.execute("ALTER TABLE portal_connectors DROP CONSTRAINT IF EXISTS ck_portal_connectors_type")
    op.create_check_constraint(
        "ck_portal_connectors_type",
        "portal_connectors",
        f"connector_type IN ({conn_values})",
    )
    # At most one ACTIVE hubspot_support connector per (org, kb, account), so
    # two connectors cannot share the same imported cases and then let one
    # reconcile-delete the other's evidence. Race-proof guarantee behind the
    # app-level 409 check in create/update_connector.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_hubspot_support_unique_account "
        "ON portal_connectors (org_id, kb_id, (config->>'account_id')) "
        "WHERE connector_type = 'hubspot_support' AND state = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_hubspot_support_unique_account")
    conn_values = ", ".join(f"'{t}'" for t in _CONNECTOR_TYPES if t != "hubspot_support")
    op.execute("ALTER TABLE portal_connectors DROP CONSTRAINT IF EXISTS ck_portal_connectors_type")
    op.create_check_constraint(
        "ck_portal_connectors_type",
        "portal_connectors",
        f"connector_type IN ({conn_values})",
    )

    op.drop_constraint("ck_retrieval_gaps_diagnosis", "portal_retrieval_gaps", type_="check")
    op.drop_constraint("ck_retrieval_gaps_gap_type", "portal_retrieval_gaps", type_="check")
    op.create_check_constraint(
        "ck_retrieval_gaps_gap_type",
        "portal_retrieval_gaps",
        "gap_type IN ('hard', 'soft')",
    )
    op.drop_index("ix_retrieval_gaps_support_case", table_name="portal_retrieval_gaps")
    for col in ("evidence", "audience", "question_key", "diagnosis", "support_case_id"):
        op.drop_column("portal_retrieval_gaps", col)

    op.drop_index("ix_portal_support_cases_connector", table_name="portal_support_cases")
    op.drop_index("ix_portal_support_cases_org_kb", table_name="portal_support_cases")
    op.drop_table("portal_support_cases")
