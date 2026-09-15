"""SQLAlchemy model for knowledge gap events."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalRetrievalGap(Base):
    __tablename__ = "portal_retrieval_gaps"
    __table_args__ = (
        CheckConstraint(
            "gap_type IN ('hard', 'soft')",
            name="ck_retrieval_gaps_gap_type",
        ),
        CheckConstraint(
            "resolved_by IN ('rescorer', 'review', 'manual')",
            name="ck_retrieval_gaps_resolved_by",
        ),
        Index("ix_retrieval_gaps_org_occurred", "org_id", "occurred_at"),
        Index("ix_retrieval_gaps_org_query", "org_id", "query_text"),
        Index("ix_retrieval_gaps_open", "org_id", "query_text", postgresql_where=text("resolved_at IS NULL")),
        Index("ix_retrieval_gaps_conversation", "conversation_id"),
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
    taxonomy_node_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    # SPEC-MCP-RETRIEVAL-001 REQ-9: optional OAuth client_id for caller
    # attribution. ``None`` = LibreChat traffic; populated = third-party
    # MCP client (Claude Desktop / Cursor / ChatGPT).
    caller_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # SPEC-KNOWLEDGE-ACTIVITY-001 §4.5: provenance, so a knowledge editor can
    # jump from a gap to the conversation it came from. No ForeignKey() here on
    # purpose — the migration runs as portal_api, which has no REFERENCES
    # privilege on widget_conversations (same split as b7e4f1a9c3d2); the real
    # FK (ON DELETE SET NULL) is added by
    # post_deploy_d8b3f6a1c4e9_gaps_conversation_fk.sql as klai superuser.
    conversation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # Language (BCP 47) of the question that produced the gap — "missing" in
    # nl and in en are two different gaps to fill.
    language: Mapped[str | None] = mapped_column(String(8), nullable=True, default=None)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # Who closed the gap: 'rescorer' (app/services/gap_rescorer.py),
    # 'review' (the answer-review cause moved away from knowledge, or the
    # review that opened it was deleted), or 'manual' (POST
    # /api/app/gaps/resolve). NULL while the gap is open.
    resolved_by: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    # The portal user who closed it via 'review' or 'manual'; NULL for
    # 'rescorer' and for open gaps. No ForeignKey() here for the same reason
    # as conversation_id above -- portal_api has no REFERENCES privilege on
    # portal_users (klai-owned); the FK (ON DELETE SET NULL) is added by
    # post_deploy_997e0b66f750_gaps_resolved_by_fk.sql as klai superuser.
    resolved_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
