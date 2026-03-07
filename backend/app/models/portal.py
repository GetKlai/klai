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
    billing_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    plan: Mapped[str] = mapped_column(Text, nullable=False, default="professional", server_default="professional")
    billing_cycle: Mapped[str] = mapped_column(Text, nullable=False, default="monthly", server_default="monthly")

    users: Mapped[list["PortalUser"]] = relationship(back_populates="org")


class PortalUser(Base):
    __tablename__ = "portal_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"))
    email: Mapped[str] = mapped_column(String(255), index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    org: Mapped["PortalOrg"] = relationship(back_populates="users")
