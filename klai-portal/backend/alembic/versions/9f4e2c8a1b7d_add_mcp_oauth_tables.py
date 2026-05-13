"""add mcp oauth tables (SPEC-MCP-AUTH-001 Fase 1)

Two new tables for the OAuth 2.1 authorization-server surface that lets
third-party MCP clients (Claude Desktop, Cursor, ChatGPT custom connectors)
authenticate against klai-knowledge-mcp:

- ``portal_oauth_clients``: Dynamic Client Registration storage (RFC 7591).
  Org-overstijgend (no ``org_id`` column) — clients are registered without a
  tenant context; tenant-scoping happens on token issuance via
  ``portal_mcp_tokens.org_id``. NO RLS on this table — see post_deploy SQL.

- ``portal_mcp_tokens``: issued access + refresh tokens, hash-stored. RLS
  Category-D (strict) per ``portal-security.md``. Bridges
  (org_id, user_id) → access_token_hash for the
  ``POST /internal/mcp-token/verify`` lookup pattern (REQ-9).

Both tables use:
- BIGSERIAL PK (room to grow; portal_users / portal_orgs use INT but token
  volume could exceed 2.1B over the long run).
- ``LargeBinary`` for hash columns (32 raw bytes from SHA-256, never hex).
- JSONB for ``redirect_uris``, ``grant_types``, ``response_types``, ``scopes``
  — enables future querying without schema migration.
- ``ON DELETE CASCADE`` from portal_orgs / portal_users (token has no value
  without its owning user/org); ``ON DELETE RESTRICT`` from
  portal_oauth_clients (a client can't be deleted while tokens reference it).

RLS, ownership transfer, and grants live in
``post_deploy_9f4e2c8a1b7d.sql`` because portal_api (the migration role) is
not the table owner and cannot ``ALTER TABLE ... OWNER TO klai`` or
``ENABLE ROW LEVEL SECURITY``. See ``portal-backend.md`` § "Alembic cannot
drop non-portal_api-owned tables" for the full reasoning.

The current alembic head landscape has multiple unmerged heads (18 as of
2026-05-06). This migration uses ``z3a4b5c6d7e8`` as ``down_revision`` —
the most recently modified head as of this writing. If a merge migration
is shipped before this lands, update ``down_revision`` accordingly.

Revision ID: 9f4e2c8a1b7d
Revises: z3a4b5c6d7e8
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9f4e2c8a1b7d"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── portal_oauth_clients ─────────────────────────────────────────────
    op.create_table(
        "portal_oauth_clients",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column(
            "redirect_uris",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "grant_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text('\'["authorization_code", "refresh_token"]\'::jsonb'),
        ),
        sa.Column(
            "response_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"code\"]'::jsonb"),
        ),
        sa.Column(
            "token_endpoint_auth_method",
            sa.String(32),
            nullable=False,
            server_default="none",
        ),
        sa.Column("application_type", sa.String(16), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"mcp:knowledge\"]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # IPv4/IPv6 of the IP that registered this client. Used for DCR rate-limit
        # forensics and the consent-page "newly registered" badge (REQ-27).
        sa.Column("created_by_ip", postgresql.INET(), nullable=True),
        sa.Column("soft_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "application_type IN ('native', 'web')",
            name="ck_portal_oauth_clients_application_type",
        ),
    )
    op.create_index(
        "ix_portal_oauth_clients_created_at",
        "portal_oauth_clients",
        ["created_at"],
    )
    op.create_index(
        "ix_portal_oauth_clients_active",
        "portal_oauth_clients",
        ["client_id"],
        unique=False,
        postgresql_where=sa.text("soft_deleted_at IS NULL"),
    )

    # ─── portal_mcp_tokens ────────────────────────────────────────────────
    op.create_table(
        "portal_mcp_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("portal_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.BigInteger(),
            sa.ForeignKey("portal_oauth_clients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # SHA-256 raw bytes (32 bytes). Never hex-encoded — saves 50% storage
        # and makes constant-time comparison via hmac.compare_digest mandatory.
        sa.Column("access_token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("refresh_token_hash", sa.LargeBinary(length=32), nullable=True),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"mcp:knowledge\"]'::jsonb"),
        ),
        # RFC 8707 audience-binding — the canonical resource URI the token
        # was issued for. knowledge-mcp validates against this on every call.
        sa.Column("resource_uri", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Refresh-token rotation chain. When grant_type=refresh_token mints a
        # new token, the old token's revoked_at is set AND replaced_by_token_id
        # points at the new row. Replay-detection (REQ-26) sees this chain and
        # revokes all tokens for (client_id, user_id) on suspect reuse.
        sa.Column(
            "replaced_by_token_id",
            sa.BigInteger(),
            sa.ForeignKey("portal_mcp_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ux_portal_mcp_tokens_access_hash",
        "portal_mcp_tokens",
        ["access_token_hash"],
        unique=True,
    )
    # Partial unique index on refresh_token_hash — NULL is allowed (some flows
    # may not mint a refresh token), but when present it must be globally unique.
    op.create_index(
        "ux_portal_mcp_tokens_refresh_hash",
        "portal_mcp_tokens",
        ["refresh_token_hash"],
        unique=True,
        postgresql_where=sa.text("refresh_token_hash IS NOT NULL"),
    )
    op.create_index(
        "ix_portal_mcp_tokens_org_user",
        "portal_mcp_tokens",
        ["org_id", "user_id"],
    )
    op.create_index(
        "ix_portal_mcp_tokens_active_expires",
        "portal_mcp_tokens",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # Reverse-order drops. portal_mcp_tokens first because of FK to
    # portal_oauth_clients. The post_deploy_*.sql counterpart handles RLS
    # disable and ownership-revert as klai-superuser; this Python downgrade
    # may fail under portal_api with 42501 if RLS-policies were already
    # applied — that's expected and acceptable for a downgrade path.
    op.drop_index("ix_portal_mcp_tokens_active_expires", table_name="portal_mcp_tokens")
    op.drop_index("ix_portal_mcp_tokens_org_user", table_name="portal_mcp_tokens")
    op.drop_index("ux_portal_mcp_tokens_refresh_hash", table_name="portal_mcp_tokens")
    op.drop_index("ux_portal_mcp_tokens_access_hash", table_name="portal_mcp_tokens")
    op.drop_table("portal_mcp_tokens")
    op.drop_index("ix_portal_oauth_clients_active", table_name="portal_oauth_clients")
    op.drop_index("ix_portal_oauth_clients_created_at", table_name="portal_oauth_clients")
    op.drop_table("portal_oauth_clients")
