"""Add portal_templates table (SPEC-CHAT-TEMPLATES-001)

Revision ID: t2a3b4c5d6e7
Revises: t1e2m3p4l5s1
Create Date: 2026-04-23

Creates the `portal_templates` table for per-tenant prompt templates.
RLS is strict (no `OR IS NULL` fallback): all queries MUST run with
`app.current_org_id` set via `set_tenant()`.

Schema:
- (org_id, slug) UNIQUE — slug uniqueness scoped per org.
- CHECK char_length(prompt_text) <= 8000 — hard upper bound on text
  that will be injected into every LiteLLM chat call for every user
  with this template active.
- CHECK scope IN ('org', 'personal') — no cross-tenant or other variants
  (see SPEC: the Jantine branch used "global" which was dropped).
- Index on (org_id, is_active) — fast lookup path used by
  `/internal/templates/effective`.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "t2a3b4c5d6e7"
down_revision = "t1e2m3p4l5s1"
branch_labels = None
depends_on = None


_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "portal_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("portal_orgs.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="org"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint("uq_portal_template_org_slug", "portal_templates", ["org_id", "slug"])
    op.create_index(
        "ix_portal_template_org_id_active",
        "portal_templates",
        ["org_id", "is_active"],
    )
    op.create_check_constraint(
        "ck_portal_template_prompt_len",
        "portal_templates",
        "char_length(prompt_text) <= 8000",
    )
    op.create_check_constraint(
        "ck_portal_template_scope",
        "portal_templates",
        "scope IN ('org', 'personal')",
    )

    # RLS strict — pattern mirrored from alembic/versions/1b8736eb6455_add_rls_phase2_user_tables.py
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "ALTER TABLE portal_templates ENABLE ROW LEVEL SECURITY"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "ALTER TABLE portal_templates FORCE ROW LEVEL SECURITY"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON portal_templates USING (org_id = {_T})"
    )


def downgrade() -> None:
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "DROP POLICY IF EXISTS tenant_isolation ON portal_templates"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "ALTER TABLE portal_templates NO FORCE ROW LEVEL SECURITY"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "ALTER TABLE portal_templates DISABLE ROW LEVEL SECURITY"
    )
    op.drop_constraint("ck_portal_template_scope", "portal_templates")
    op.drop_constraint("ck_portal_template_prompt_len", "portal_templates")
    op.drop_index("ix_portal_template_org_id_active", table_name="portal_templates")
    op.drop_constraint("uq_portal_template_org_slug", "portal_templates")
    op.drop_table("portal_templates")
