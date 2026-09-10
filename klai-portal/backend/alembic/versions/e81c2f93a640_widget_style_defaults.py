"""Add optional tenant defaults for widget appearance (metadata-only DDL)."""

from alembic import op

revision = "e81c2f93a640"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.portal_orgs
        ADD COLUMN IF NOT EXISTS widget_css_variables jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(widget_css_variables) = 'object')
    """)


def downgrade() -> None:
    op.drop_column("portal_orgs", "widget_css_variables")
