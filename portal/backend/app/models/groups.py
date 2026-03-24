"""
Group models: PortalGroup and PortalGroupMembership.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalGroup(Base):
    __tablename__ = "portal_groups"
    __table_args__ = (
        # Case-insensitive unique constraint handled via functional index in migration
        Index("ix_portal_groups_org_id", "org_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


class PortalGroupMembership(Base):
    __tablename__ = "portal_group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "zitadel_user_id", name="uq_group_membership"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("portal_groups.id", ondelete="CASCADE"), nullable=False)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_group_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
