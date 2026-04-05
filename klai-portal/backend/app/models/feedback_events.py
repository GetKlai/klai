"""SQLAlchemy model for knowledge feedback events.

# @MX:NOTE: [AUTO] RLS-protected table for KB feedback signals. SPEC-KB-015.
# @MX:NOTE: No user_id column -- privacy by design. librechat_user_id is transient only.

Follows portal_retrieval_gaps pattern for RLS.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalFeedbackEvent(Base):
    __tablename__ = "portal_feedback_events"
    __table_args__ = (
        CheckConstraint(
            "rating IN ('thumbsUp', 'thumbsDown')",
            name="ck_feedback_events_rating",
        ),
        UniqueConstraint("message_id", "conversation_id", name="uq_feedback_events_msg_conv"),
        Index("ix_feedback_events_org_occurred", "org_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    rating: Mapped[str] = mapped_column(String, nullable=False)
    tag: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    feedback_text: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    chunk_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True, default=None)
    correlated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    model_alias: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
