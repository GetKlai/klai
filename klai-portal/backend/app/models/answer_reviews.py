"""AnswerReview ORM model — SPEC-KNOWLEDGE-ACTIVITY-001 §4.2.

One row per assistant answer a Klai colleague judged: whether it was correct,
why not when it was not, and a free-text note. Unlike the nightly LLM judge in
``app/models/conversation_quality.py`` this is the human verdict, and it is
written to outlive the 7-day ``widget_messages_retention_days`` purge — which
is why the conversation/message foreign keys are nullable with ``SET NULL``
and why ``band_at_review`` / ``judge_outcome_at_review`` /
``judge_failure_category_at_review`` snapshot the retrieval state instead of
reading it back later (it is recomputed nightly, see SPEC §4.4).

Rows key off ``message_id`` for ``channel='webchat'`` and off
``external_message_id`` (the LibreChat Mongo ObjectId string) for
``channel='librechat'``, mirroring ``ConversationQualityJudgment``. Uniqueness
is enforced by partial indexes so that purged rows (both keys NULL) do not
collide.

DDL lives in ``post_deploy_c2a7e9d4b1f6_answer_reviews_rls.sql`` (klai-owned
table, Cat-D RLS) — this class only describes the shape for the ORM layer.
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnswerReview(Base):
    __tablename__ = "answer_reviews"
    __table_args__ = (
        CheckConstraint("channel IN ('webchat', 'librechat')", name="ck_answer_reviews_channel"),
        CheckConstraint(
            "verdict IN ('correct','incomplete','wrong','not_a_fault')",
            name="ck_answer_reviews_verdict",
        ),
        CheckConstraint(
            "cause IN ('knowledge_missing','knowledge_wrong','behaviour','none')",
            name="ck_answer_reviews_cause",
        ),
        CheckConstraint(
            "(verdict IN ('correct','not_a_fault') AND cause = 'none') "
            "OR (verdict IN ('incomplete','wrong') AND cause <> 'none')",
            name="ck_answer_reviews_cause_matches_verdict",
        ),
        CheckConstraint(
            "band_at_review IN ('high','medium','low','unknown')",
            name="ck_answer_reviews_band_at_review",
        ),
        # Partial uniques (both keys are NULL once the conversation is purged,
        # and those rows must not collide) — see post_deploy SQL.
        Index(
            "uq_answer_reviews_message",
            "message_id",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
        Index(
            "uq_answer_reviews_external_message",
            "external_message_id",
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index("ix_answer_reviews_org_reviewed", "org_id", "reviewed_at"),
        Index("ix_answer_reviews_conversation", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="webchat")
    # Nullable + SET NULL, not CASCADE: the review must survive the retention
    # purge of the conversation it judged (see post_deploy SQL comment).
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("widget_conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("widget_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Mongo ObjectId string for channel='librechat' rows; NULL for webchat rows.
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Survives the purge so the review can still be placed in a transcript that
    # no longer exists locally.
    turn_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable + SET NULL: a departed colleague must not take their reviews
    # (or the gap history behind them) with them.
    reviewer_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("portal_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    cause: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    kb_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Review-time snapshots: the live band/judge values are recomputed nightly
    # and the KB changes, so reading them back would rewrite history.
    band_at_review: Mapped[str] = mapped_column(String(8), nullable=False)
    judge_outcome_at_review: Mapped[str | None] = mapped_column(String(24), nullable=True)
    judge_failure_category_at_review: Mapped[str | None] = mapped_column(String(24), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    gap_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("portal_retrieval_gaps.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
