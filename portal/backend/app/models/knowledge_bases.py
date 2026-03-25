"""Knowledge base models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalKnowledgeBase(Base):
    __tablename__ = "portal_knowledge_bases"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_portal_kb_org_slug"),
        Index("ix_portal_kb_org_id", "org_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="internal")
    docs_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    gitea_repo_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="org")
    owner_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class PortalUserKBAccess(Base):
    __tablename__ = "portal_user_kb_access"
    __table_args__ = (
        UniqueConstraint("kb_id", "user_id", name="uq_user_kb_access"),
        Index("ix_user_kb_access_kb_id", "kb_id"),
        Index("ix_user_kb_access_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by: Mapped[str] = mapped_column(Text, nullable=False)


class PortalGroupKBAccess(Base):
    __tablename__ = "portal_group_kb_access"
    __table_args__ = (
        UniqueConstraint("group_id", "kb_id", name="uq_group_kb_access"),
        Index("ix_group_kb_access_group_id", "group_id"),
        Index("ix_group_kb_access_kb_id", "kb_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("portal_groups.id", ondelete="CASCADE"), nullable=False)
    kb_id: Mapped[int] = mapped_column(ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="viewer")
