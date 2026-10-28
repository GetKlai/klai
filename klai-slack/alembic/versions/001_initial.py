"""Initial schema: Bolt OAuth tables (tokens encrypted, TEXT) and the klai-slack tables.

The Bolt tables mirror slack_sdk 3.45's SQLAlchemy store definitions, with
two deliberate differences: token columns are TEXT because an AES-256-GCM
ciphertext of a rotating token exceeds VARCHAR(200), and timestamps stay
WITHOUT TIME ZONE because the store writes naive UTC datetimes
(``normalize_datetime_for_db``) and compares against them.

Revision ID: 001_initial
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bot_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(32), nullable=False),
        sa.Column("app_id", sa.String(32), nullable=False),
        sa.Column("enterprise_id", sa.String(32)),
        sa.Column("enterprise_name", sa.String(200)),
        sa.Column("team_id", sa.String(32)),
        sa.Column("team_name", sa.String(200)),
        sa.Column("bot_token", sa.Text),
        sa.Column("bot_id", sa.String(32)),
        sa.Column("bot_user_id", sa.String(32)),
        sa.Column("bot_scopes", sa.String(1000)),
        sa.Column("bot_refresh_token", sa.Text),
        sa.Column("bot_token_expires_at", sa.DateTime),
        sa.Column("is_enterprise_install", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("installed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "slack_installations",
        *_bot_columns(),
        sa.Column("enterprise_url", sa.String(200)),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("user_token", sa.Text),
        sa.Column("user_scopes", sa.String(1000)),
        sa.Column("user_refresh_token", sa.Text),
        sa.Column("user_token_expires_at", sa.DateTime),
        sa.Column("incoming_webhook_url", sa.String(200)),
        sa.Column("incoming_webhook_channel", sa.String(200)),
        sa.Column("incoming_webhook_channel_id", sa.String(200)),
        sa.Column("incoming_webhook_configuration_url", sa.String(200)),
        sa.Column("token_type", sa.String(32)),
    )
    op.create_index(
        "slack_installations_idx",
        "slack_installations",
        ["client_id", "enterprise_id", "team_id", "user_id", "installed_at"],
    )

    op.create_table("slack_bots", *_bot_columns())
    op.create_index(
        "slack_bots_idx", "slack_bots", ["client_id", "enterprise_id", "team_id", "installed_at"]
    )

    op.create_table(
        "slack_oauth_states",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("state", sa.String(200), nullable=False),
        sa.Column("expire_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "workspace_links",
        sa.Column("team_id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("org_name", sa.Text, nullable=False),
        sa.Column("klai_api_key_enc", sa.Text, nullable=False),
        sa.Column("key_id", sa.String(36), nullable=False),
        sa.Column("linked_by_slack_user_id", sa.String(32), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "channel_defaults",
        sa.Column("team_id", sa.String(32), primary_key=True),
        sa.Column("channel_id", sa.String(32), primary_key=True),
        sa.Column("kb_ids", postgresql.ARRAY(sa.Integer), nullable=False),
        sa.Column("set_by", sa.String(32), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "thread_scopes",
        sa.Column("team_id", sa.String(32), primary_key=True),
        sa.Column("channel_id", sa.String(32), primary_key=True),
        sa.Column("thread_ts", sa.String(32), primary_key=True),
        sa.Column("kb_ids", postgresql.ARRAY(sa.Integer), nullable=False),
        sa.Column("set_by", sa.String(32), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "turns",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("channel_id", sa.String(32), nullable=False),
        sa.Column("thread_ts", sa.String(32), nullable=False),
        sa.Column("message_ts", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("completion_id", sa.String(128)),
        sa.Column("kb_ids", postgresql.ARRAY(sa.Integer)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="turns_role_check"),
    )
    op.create_index(
        "turns_thread_idx", "turns", ["team_id", "channel_id", "thread_ts", "created_at"]
    )
    op.create_index("turns_created_at_idx", "turns", ["created_at"])

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("processed_events_received_at_idx", "processed_events", ["received_at"])


def downgrade() -> None:
    for table in (
        "processed_events",
        "turns",
        "thread_scopes",
        "channel_defaults",
        "workspace_links",
        "slack_oauth_states",
        "slack_bots",
        "slack_installations",
    ):
        op.drop_table(table)
