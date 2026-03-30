"""SQLAlchemy model for knowledge gap events."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalRetrievalGap(Base):
    __tablename__ = "portal_retrieval_gaps"
    __table_args__ = (
        CheckConstraint(
            "gap_type IN ('hard', 'soft')",
            name="ck_retrieval_gaps_gap_type",
        ),
        Index("ix_retrieval_gaps_org_occurred", "org_id", "occurred_at"),
        Index("ix_retrieval_gaps_org_query", "org_id", "query_text"),
        Index("ix_retrieval_gaps_open", "org_id", "query_text", postgresql_where=text("resolved_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    query_text: Mapped[str] = mapped_column(String, nullable=False)
    gap_type: Mapped[str] = mapped_column(String, nullable=False)
    top_score: Mapped[float | None] = mapped_column(Double, nullable=True)
    nearest_kb_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    chunks_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    retrieval_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
