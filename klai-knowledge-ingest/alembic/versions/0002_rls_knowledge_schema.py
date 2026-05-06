"""SPEC-TI-003 0002 -- Enable RLS on knowledge.* tenant-tagged tables.

Runs ENABLE ROW LEVEL SECURITY + FORCE ROW LEVEL SECURITY on every table
in the knowledge schema that carries an org_id column (9 tables) plus the
4 junction tables that are CASCADE children of those tables.

RLS *policies* are NOT created here -- they require klai superuser and are
therefore in the companion post-deploy SQL file:
  klai-knowledge-ingest/alembic/versions/post_deploy_dd1b439a57d0.sql

Run as knowledge_ingest role (limited privileges). ENABLE/FORCE is allowed
for table owners. The knowledge_ingest role is the table owner per the
0001_baseline migration (all tables created without explicit OWNER clause
use the connected role at creation time).

Idempotent: IF NOT EXISTS guards not needed for ALTER TABLE ENABLE/FORCE --
they are idempotent by definition (re-running is a no-op if already set).

Revision ID: dd1b439a57d0
Revises: 0001_baseline
Create Date: 2026-05-05
Finding: A-8 (knowledge.* ZERO RLS)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "dd1b439a57d0"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables with a direct org_id column (Cat-D primary tables).
_ORG_ID_TABLES = [
    "knowledge.artifacts",
    "knowledge.entities",
    "knowledge.crawl_domains",
    "knowledge.crawl_jobs",
    "knowledge.crawled_pages",
    "knowledge.kb_config",
    "knowledge.org_config",
    "knowledge.page_links",
    "knowledge.parent_chunks",
]

# Junction tables -- no own org_id; policies use subquery via parent.
# Also enabled here so the Cat-D policies added by post_deploy SQL take effect.
_JUNCTION_TABLES = [
    "knowledge.artifact_entities",
    "knowledge.artifact_images",
    "knowledge.derivations",
    "knowledge.embedding_queue",
]

# rag_eval_results is intentionally EXCLUDED -- analytics/eval table with no
# tenant ownership column (see SPEC-TI-003 AC-4).


def upgrade() -> None:
    """Enable + force RLS on all tenant-scoped knowledge.* tables.

    Policies are added in post_deploy_dd1b439a57d0.sql (klai superuser only).
    """
    for table in _ORG_ID_TABLES + _JUNCTION_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Disable RLS -- only for dev rollback, never on production."""
    for table in _JUNCTION_TABLES + _ORG_ID_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
