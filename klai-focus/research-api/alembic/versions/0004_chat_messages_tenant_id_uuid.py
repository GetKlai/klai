"""A-11: Fix research.chat_messages.tenant_id from VARCHAR(64) to UUID

chat_messages.tenant_id was created as VARCHAR(64) in 0002_chat_history.py,
while notebooks.tenant_id, sources.tenant_id, and chunks.tenant_id were all
created as UUID. The inconsistency means RLS Cat-D policies that cast
_rls_current_org_id() to uuid cannot compare against chat_messages.tenant_id
without an implicit cast — which is fragile and slows down index scans.

Finding: A-11 (audit-tenant-isolation-2026-05-05)
Refs: SPEC-TI-004-RLS-RESEARCH

No users in prod on the research schema — USING cast is safe.

Revision ID: 0004_chat_messages_uuid
Revises: 0003_drop_embedding
Create Date: 2026-05-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_chat_messages_uuid"
down_revision: str | Sequence[str] | None = "0003_drop_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cast is safe: no prod data on research schema (confirmed 2026-05-05).
    # Any non-UUID value would raise here and abort the migration — which is
    # the correct fail-loud behaviour (no silent data corruption).
    op.execute(
        "ALTER TABLE research.chat_messages ALTER COLUMN tenant_id TYPE uuid USING tenant_id::uuid"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE research.chat_messages "
        "ALTER COLUMN tenant_id TYPE VARCHAR(64) USING tenant_id::text"
    )
