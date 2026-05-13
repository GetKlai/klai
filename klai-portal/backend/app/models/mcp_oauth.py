"""MCP OAuth 2.1 models — SPEC-MCP-AUTH-001.

Two tables for the OAuth authorization-server surface that lets third-party
MCP clients (Claude Desktop, Cursor, ChatGPT custom connectors) authenticate
against klai-knowledge-mcp:

- ``PortalOAuthClient`` — Dynamic Client Registration storage (RFC 7591).
  No tenant column: clients register anonymously, tenant scoping happens on
  token issuance.
- ``PortalMcpToken`` — issued access + refresh tokens, hash-stored. RLS
  Category-D (strict) per ``portal-security.md``. SHA-256 raw bytes for the
  hash columns to make ``hmac.compare_digest`` the only sensible compare path.

DDL lives in ``alembic/versions/9f4e2c8a1b7d_add_mcp_oauth_tables.py``;
ownership transfer + RLS policies in ``post_deploy_9f4e2c8a1b7d.sql``.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalOAuthClient(Base):
    """OAuth 2.1 client registered via DCR (RFC 7591).

    No ``org_id`` — clients are org-overstijgend. The defense surface is at
    the application layer:

    1. ``redirect_uris`` validated against an allowlist on registration
       (SPEC-MCP-AUTH-001 REQ-20).
    2. Per-IP rate-limit on the ``POST /oauth/register`` endpoint (REQ-27).
    3. ``soft_deleted_at`` filters in every read query — there is no admin
       UI surface that exposes hard-deletes.

    See SPEC-MCP-AUTH-001 § Architecture Decision A4.
    """

    __tablename__ = "portal_oauth_clients"
    __table_args__ = (
        CheckConstraint(
            "application_type IN ('native', 'web')",
            name="ck_portal_oauth_clients_application_type",
        ),
        Index("ix_portal_oauth_clients_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 128-bit URL-safe random token via ``secrets.token_urlsafe(16)``. Unique
    # globally; no semantic structure (the user-facing display string is
    # ``client_name``).
    client_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    grant_types: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default='\'["authorization_code", "refresh_token"]\'::jsonb',
    )
    response_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="'[\"code\"]'::jsonb")
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(32), nullable=False, server_default="none")
    application_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="'[\"mcp:knowledge\"]'::jsonb")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # IPv4/IPv6 of the registering peer. Set by the DCR endpoint from the
    # incoming connection's IP (or X-Forwarded-For when trusted). Used for
    # the consent-page "newly registered" badge and post-incident DCR-spam
    # forensics.
    created_by_ip: Mapped[Any | None] = mapped_column(INET, nullable=True)
    soft_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PortalMcpToken(Base):
    """OAuth 2.1 access + refresh token issued for an MCP client.

    Stored as SHA-256 raw bytes (never hex-encoded) — saves storage and
    makes ``hmac.compare_digest`` the only sensible comparison path
    (mechanically caught by ``no-secret-eq-compare`` ast-grep rule).

    The model carries both the access-token-hash and the refresh-token-hash
    on a single row. Refresh-token rotation (REQ-26) marks ``revoked_at`` on
    the old row and inserts a fresh one with ``replaced_by_token_id``
    pointing back — replay-detection scans this chain.

    @MX:ANCHOR fan_in=high — verified by every knowledge-mcp tool call via
    the ``POST /internal/mcp-token/verify`` endpoint. Schema changes here
    cascade to ``mcp_token_verifier``, ``mcp_token_verify_cache``, and the
    ``McpTokenAsserter`` shared library.
    @MX:REASON Cross-service contract: any rename to ``access_token_hash``
    or ``resource_uri`` requires synchronized changes in
    ``klai-libs/identity-assert/`` and ``klai-knowledge-mcp/auth.py``.
    @MX:SPEC SPEC-MCP-AUTH-001
    """

    __tablename__ = "portal_mcp_tokens"
    __table_args__ = (Index("ix_portal_mcp_tokens_org_user", "org_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("portal_oauth_clients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # SHA-256 raw bytes (32 bytes). Length-fixed at the DB level via
    # ``LargeBinary(length=32)``; the application enforces it via
    # ``hashlib.sha256(token.encode()).digest()``.
    access_token_hash: Mapped[bytes] = mapped_column(LargeBinary(length=32), nullable=False)
    refresh_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(length=32), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="'[\"mcp:knowledge\"]'::jsonb")
    # RFC 8707 audience-binding. Knowledge-mcp validates against this on
    # every call — tokens issued for a different resource are rejected.
    resource_uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Refresh-rotation chain. When ``grant_type=refresh_token`` mints a new
    # token, old.revoked_at = NOW() AND old.replaced_by_token_id = new.id.
    # Replay-detection (REQ-26) walks this chain and revokes the entire
    # ``(client_id, user_id)`` token-set on suspect reuse.
    replaced_by_token_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("portal_mcp_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
