"""vexa webhook idempotency receipts

Vexa's webhook.v1 contract is explicitly at-least-once: "A logical event may be
POSTed more than once — the initial send, a retry-queue drain, a restart replay,
or a cross-replica race can all re-emit it", with a 60/300/1800/7200s retry
schedule. `event_id` is the receiver's idempotency key and is stable across
redeliveries; `created_at` and the HMAC signature deliberately are not.

Klai's handler discarded `event_id` entirely, so a redelivered
`meeting.completed` set the meeting back to `stopping`, re-ran transcription and
recording cleanup, and re-emitted the product event. A transient failure on the
second pass could overwrite a good `done` with `failed`.

This table is the dedupe ledger. `event_id` is globally unique on Vexa's side
(derived from connection_id · event_type · new_status), so the constraint is a
plain unique index rather than a composite with org_id — and the webhook has no
tenant context at insert time anyway.

Retention: rows older than 48h are prunable (contract says receivers must dedupe
for at least 48 hours). Pruning is not scheduled here; the table is tiny and a
follow-up sweep can add it.

Not RLS-protected: it holds no tenant data (an opaque upstream id, a timestamp
and the meeting FK), and it is written on the pre-tenant-context path where a
Cat-D policy would raise 42501.

Revision ID: 8ad64a1112d9
Revises: p4q5r6s7t8u9
"""

import sqlalchemy as sa
from alembic import op

revision = "8ad64a1112d9"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vexa_webhook_receipts",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("vexa_meeting_id", sa.Integer(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_vexa_webhook_receipts_received_at",
        "vexa_webhook_receipts",
        ["received_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vexa_webhook_receipts_received_at",
        table_name="vexa_webhook_receipts",
        if_exists=True,
    )
    op.drop_table("vexa_webhook_receipts", if_exists=True)
