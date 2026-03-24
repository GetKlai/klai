"""Docs library models."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalDocsLibrary(Base):
    __tablename__ = "portal_docs_libraries"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_portal_docs_org_slug"),
        Index("ix_portal_docs_org_id", "org_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


class PortalGroupDocsAccess(Base):
    __tablename__ = "portal_group_docs_access"
    __table_args__ = (
        UniqueConstraint("group_id", "library_id", name="uq_group_docs_access"),
        Index("ix_group_docs_access_group_id", "group_id"),
        Index("ix_group_docs_access_library_id", "library_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("portal_groups.id", ondelete="CASCADE"), nullable=False
    )
    library_id: Mapped[int] = mapped_column(
        ForeignKey("portal_docs_libraries.id", ondelete="CASCADE"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by: Mapped[str] = mapped_column(String(64), nullable=False)
