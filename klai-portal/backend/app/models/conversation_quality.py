"""ConversationQualityJudgment ORM model — SPEC-CHAT-QUALITY-LOOP-001 REQ-1/REQ-5.

Nightly LLM-as-judge verdict for a finished conversation, webchat or
LibreChat, enriching (not replacing) the cheap real-time heuristic in
``widget_conversations.outcome`` (``app/services/widget_outcome.py``) for
the webchat case. One row per conversation:

- ``channel='webchat'`` rows key off ``conversation_id`` (FK into
  ``widget_conversations``, UPSERT on that column).
- ``channel='librechat'`` rows key off ``external_conversation_id`` (the
  Mongo ObjectId string — LibreChat lives in a per-tenant MongoDB, not
  Postgres, so there is no local row to foreign-key against) and leave
  ``conversation_id`` NULL. REQ-5 gates this per org via the
  ``librechat_quality_judge`` platform-unlock feature (default off; Voys is
  the pilot).

``reasoning`` may quote the conversation and is nulled out by the retention
sweep once the underlying ``widget_conversations``/``widget_messages`` rows
are purged (``widget_messages_retention_days``) — a quote surviving past the
conversation it came from is itself identifiable content. See
SPEC-CHAT-QUALITY-LOOP-001 §6.

DDL lives in
``post_deploy_b7e4f1a9c3d2_conversation_quality_judgments_rls.sql``
(klai-owned table, Cat-D RLS) — this class only describes the shape for the
ORM layer.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConversationQualityJudgment(Base):
    __tablename__ = "conversation_quality_judgments"
    __table_args__ = (
        CheckConstraint("channel IN ('webchat', 'librechat')", name="ck_cqj_channel"),
        CheckConstraint(
            "outcome IN ('resolved','partially_resolved','unresolved','escalated','out_of_scope','abandoned_early')",
            name="ck_cqj_outcome",
        ),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN "
            "('retrieval_miss','retrieval_wrong','generation_error',"
            "'policy_refusal','scope_mismatch','user_confusion','none')",
            name="ck_cqj_failure_category",
        ),
        CheckConstraint("confidence IN ('high','medium','low')", name="ck_cqj_confidence"),
        UniqueConstraint("conversation_id", name="uq_conversation_quality_judgments_conversation"),
        UniqueConstraint("external_conversation_id", name="uq_conversation_quality_judgments_external_conversation"),
        Index("ix_conversation_quality_judgments_org_judged", "org_id", "judged_at"),
        Index("ix_conversation_quality_judgments_outcome", "outcome"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    # Nullable + SET NULL, not CASCADE: this row must outlive the purged
    # conversation, anonymized (see post_deploy SQL comment + SPEC §6).
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("widget_conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="webchat")
    # Mongo ObjectId string for channel='librechat' rows; NULL for webchat
    # rows (which use conversation_id instead). See module docstring.
    external_conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str] = mapped_column(String(64), nullable=False)
    judged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
