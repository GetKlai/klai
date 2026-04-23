"""Reconcile legacy portal_templates state (SPEC-CHAT-TEMPLATES-CLEANUP-001)

Revision ID: u1r2e3c4o5n6
Revises: t3a4b5c6d7e8
Create Date: 2026-04-23

Idempotent reconciliation for environments where `portal_templates` already
existed before SPEC-CHAT-TEMPLATES-001 landed (e.g. production, which had
a legacy-era template seed predating alembic visibility). On a fresh DB
this migration is a no-op.

Reconciles the following drift:

1. Data: `scope='global'` (legacy default) → `scope='org'` (current domain).
2. Data: rows whose `created_by` predates the `"system"` seed convention
   get normalised — only for the four default-template slugs to avoid
   touching user-authored rows.
3. Index: old `ix_portal_template_org_id (org_id)` replaced by current
   `ix_portal_template_org_id_active (org_id, is_active)` (matches model
   and internal-endpoint lookup path).
4. CHECK constraint `ck_portal_template_prompt_len` (<= 8000 chars).
5. CHECK constraint `ck_portal_template_scope` (IN ('org','personal')).
6. Column default `scope` → `'org'`.
7. Row-Level Security strict (ENABLE + FORCE + `tenant_isolation` policy
   without `OR IS NULL` fallback). All read paths MUST `set_tenant()`.

Every DDL uses `DROP … IF EXISTS + CREATE …` or `IF NOT EXISTS` so the
migration is safely re-entrant and does not fail against state that already
matches the target.

Downgrade reverts step 7 (drops policy + disables RLS). Steps 1-6 are
intentionally one-way — reverting the CHECK constraints or the index
rename would put the schema back into an inconsistent legacy state.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "u1r2e3c4o5n6"
down_revision = "t3a4b5c6d7e8"
branch_labels = None
depends_on = None


_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"
_DEFAULT_SLUGS = ("klantenservice", "formeel", "creatief", "samenvatter")


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: scope='global' legacy → 'org' current.
    # RLS is not yet active at this point of the upgrade, so unqualified
    # UPDATE sees all rows. On a fresh DB this matches zero rows.
    conn.execute(sa.text("UPDATE portal_templates SET scope = 'org' WHERE scope = 'global'"))

    # Step 2: normalise the four seeded default templates to created_by='system'.
    # Guarded by the slug whitelist so user-authored rows are never touched.
    conn.execute(
        sa.text(
            "UPDATE portal_templates SET created_by = 'system' WHERE slug = ANY(:slugs) AND created_by <> 'system'"
        ).bindparams(slugs=list(_DEFAULT_SLUGS))
    )

    # Step 3: replace legacy index `ix_portal_template_org_id` (org_id only)
    # with the current `ix_portal_template_org_id_active (org_id, is_active)`
    # that matches the SQLAlchemy model and the internal-endpoint query path.
    op.execute("DROP INDEX IF EXISTS ix_portal_template_org_id")
    op.execute("CREATE INDEX IF NOT EXISTS ix_portal_template_org_id_active ON portal_templates (org_id, is_active)")

    # Step 4 + 5: CHECK constraints. DROP-IF-EXISTS + ADD for idempotence.
    op.execute("ALTER TABLE portal_templates DROP CONSTRAINT IF EXISTS ck_portal_template_prompt_len")
    op.execute(
        "ALTER TABLE portal_templates "
        "ADD CONSTRAINT ck_portal_template_prompt_len "
        "CHECK (char_length(prompt_text) <= 8000)"
    )
    op.execute("ALTER TABLE portal_templates DROP CONSTRAINT IF EXISTS ck_portal_template_scope")
    op.execute(
        "ALTER TABLE portal_templates ADD CONSTRAINT ck_portal_template_scope CHECK (scope IN ('org', 'personal'))"
    )

    # Step 6: scope DEFAULT to 'org'. No IF block needed — repeated SET is a no-op.
    op.execute("ALTER TABLE portal_templates ALTER COLUMN scope SET DEFAULT 'org'")

    # Step 7: RLS strict. ENABLE/FORCE are idempotent. POLICY needs DROP+CREATE
    # because PostgreSQL doesn't support CREATE POLICY IF NOT EXISTS.
    op.execute("ALTER TABLE portal_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portal_templates FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_templates")
    op.execute(f"CREATE POLICY tenant_isolation ON portal_templates USING (org_id = {_T})")


def downgrade() -> None:
    # Only the RLS+policy is reversed — reverting data/CHECK/index back to
    # the legacy drift is not desired (it would re-break the invariants).
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_templates")
    op.execute("ALTER TABLE portal_templates NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portal_templates DISABLE ROW LEVEL SECURITY")
