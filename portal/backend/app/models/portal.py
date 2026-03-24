from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PortalOrg(Base):
    __tablename__ = "portal_orgs"

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_org_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    moneybird_contact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    moneybird_subscription_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    plan: Mapped[str] = mapped_column(Text, nullable=False, default="professional", server_default="professional")
    billing_cycle: Mapped[str] = mapped_column(Text, nullable=False, default="monthly", server_default="monthly")
    seats: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    default_language: Mapped[Literal["nl", "en"]] = mapped_column(
        String(8), nullable=False, default="nl", server_default="nl"
    )
    librechat_container: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_librechat_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_librechat_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    litellm_team_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioning_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    mfa_policy: Mapped[Literal["optional", "recommended", "required"]] = mapped_column(
        String(16), nullable=False, default="optional", server_default="optional"
    )

    users: Mapped[list["PortalUser"]] = relationship(back_populates="org")


class PortalUser(Base):
    __tablename__ = "portal_users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'offboarded')", name="ck_portal_users_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"))
    role: Mapped[Literal["admin", "member"]] = mapped_column(
        String(20), nullable=False, default="member", server_default="member"
    )
    preferred_language: Mapped[Literal["nl", "en"]] = mapped_column(
        String(8), nullable=False, default="nl", server_default="nl"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    org: Mapped["PortalOrg"] = relationship(back_populates="users")
