"""Shield models.

Shield tokens authenticate the browser extension test surface. They are
separate from partner API keys so a leaked or revoked extension credential
cannot be confused with server-to-server API access.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalShieldToken(Base):
    __tablename__ = "portal_shield_tokens"
    __table_args__ = (
        Index("ix_portal_shield_tokens_token_hash", "token_hash", unique=True),
        Index("ix_portal_shield_tokens_org_user", "org_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid().cast(String),
    )
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PortalShieldAuthCode(Base):
    __tablename__ = "portal_shield_auth_codes"
    __table_args__ = (
        Index("ix_portal_shield_auth_codes_code_hash", "code_hash", unique=True),
        Index("ix_portal_shield_auth_codes_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid().cast(String),
    )
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PortalShieldLog(Base):
    __tablename__ = "portal_shield_logs"
    __table_args__ = (
        Index("ix_portal_shield_logs_org_created_at", "org_id", "created_at"),
        Index("ix_portal_shield_logs_token_created_at", "token_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid().cast(String),
    )
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("portal_shield_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    surface: Mapped[str] = mapped_column(String(32), nullable=False, server_default="browser_extension")
    check_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="input")
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default="basic")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, server_default="[]")
    sources: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, server_default="[]")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
