"""
Audit log model -- immutable append-only record of access control events.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalAuditLog(Base):
    __tablename__ = "portal_audit_log"
    __table_args__ = (
        # Supports paginated queries filtered by org, ordered by time
        Index("ix_portal_audit_log_org_created", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
