from datetime import datetime
from sqlalchemy import String, ForeignKey, DateTime, Text, func
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
    librechat_container: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_librechat_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_librechat_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioning_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")

    users: Mapped[list["PortalUser"]] = relationship(back_populates="org")


class PortalUser(Base):
    __tablename__ = "portal_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    org: Mapped["PortalOrg"] = relationship(back_populates="users")
