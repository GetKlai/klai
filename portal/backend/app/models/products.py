"""Product entitlement model -- tracks which products a user has access to."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalUserProduct(Base):
    __tablename__ = "portal_user_products"
    __table_args__ = (
        UniqueConstraint("zitadel_user_id", "product", name="uq_user_product"),
        Index("ix_portal_user_products_org_product", "org_id", "product"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"), nullable=False)
    product: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    enabled_by: Mapped[str] = mapped_column(String(64), nullable=False)
