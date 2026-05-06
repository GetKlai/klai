"""SPEC-TI-007 / C-1 -- Add knowledge.gitea_repo_to_org table.

Replaces the spoofable Gitea-API description-field org_id lookup with a
trusted DB mapping. The table is owned by the `klai` superuser role and
protected by RLS Cat-D policy identical to other knowledge-schema tables.

Revision ID: 0002_gitea_repo_to_org
Revises: 0001_baseline
Create Date: 2026-05-06
"""

from alembic import op

# revision identifiers, used by Alembic
revision = "0002_gitea_repo_to_org"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the mapping table in the knowledge schema.
    # The table is created here; RLS ENABLE + policy are in the companion
    # post_deploy_0002_gitea_repo_to_org.sql (must run as klai superuser).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge.gitea_repo_to_org (
            full_name TEXT PRIMARY KEY,
            org_id    TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        COMMENT ON TABLE knowledge.gitea_repo_to_org IS
            'SPEC-TI-007 / C-1: Trusted mapping from Gitea repo full_name '
            '(org-{slug}/{kb_slug}) to Zitadel org_id. Replaces the '
            'spoofable Gitea-API org.description field.';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge.gitea_repo_to_org;")
