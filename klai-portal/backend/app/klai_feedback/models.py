from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FeedbackSubmission(Base):
    __tablename__ = "feedback_submissions"
    __table_args__ = (
        CheckConstraint(
            "source IN ('assistant_feedback', 'assistant_problem', 'assistant_question', 'chat_rating', 'manual_import')",
            name="ck_feedback_submissions_source",
        ),
        CheckConstraint(
            "status IN ('new', 'open', 'resolved', 'dismissed', 'support')",
            name="ck_feedback_submissions_status",
        ),
        Index("ix_feedback_submissions_org_created", "org_id", "created_at"),
        Index("ix_feedback_submissions_source_created", "source", "created_at"),
        Index("ix_feedback_submissions_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'new'"))
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("portal_orgs.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    route_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    viewport: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FeedbackItem(Base):
    __tablename__ = "feedback_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('feature', 'bug', 'ux_confusion', 'docs', 'support_pattern')",
            name="ck_feedback_items_kind",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_feedback_items_status",
        ),
        Index("ix_feedback_items_status_updated", "status", "updated_at"),
        Index("ix_feedback_items_area_status", "area", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'inbox'"))
    area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority_score: Mapped[int] = mapped_column(nullable=False, server_default="0")
    org_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    user_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    external_tracker_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_tracker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_tracker_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    public_feedback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    public_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    public_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_window: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notification_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'not_needed'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FeedbackItemLink(Base):
    __tablename__ = "feedback_item_links"
    __table_args__ = (
        CheckConstraint(
            "link_type IN ('upvote', 'evidence', 'bug_repro', 'support_signal')",
            name="ck_feedback_item_links_link_type",
        ),
        CheckConstraint(
            "created_by IN ('ai', 'staff')",
            name="ck_feedback_item_links_created_by",
        ),
        Index("ix_feedback_item_links_submission", "submission_id"),
    )

    item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("feedback_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("feedback_submissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    link_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[int | None] = mapped_column(nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class FeedbackTriageSuggestion(Base):
    __tablename__ = "feedback_triage_suggestions"
    __table_args__ = (
        Index("ix_feedback_triage_suggestions_submission", "submission_id"),
        Index("ix_feedback_triage_suggestions_created", "created_at"),
        Index("uq_feedback_triage_suggestions_submission_model", "submission_id", "model", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("feedback_submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suggested_severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duplicate_candidates_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    suggested_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class FeedbackNotification(Base):
    __tablename__ = "feedback_notifications"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('in_app', 'email')",
            name="ck_feedback_notifications_channel",
        ),
        CheckConstraint(
            "status IN ('draft', 'queued', 'sent', 'failed', 'skipped')",
            name="ck_feedback_notifications_status",
        ),
        CheckConstraint(
            "generated_by IN ('ai', 'staff', 'system')",
            name="ck_feedback_notifications_generated_by",
        ),
        Index("ix_feedback_notifications_user_created", "org_id", "user_id", "created_at"),
        Index("ix_feedback_notifications_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("feedback_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    submission_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("feedback_submissions.id", ondelete="SET NULL"),
        nullable=True,
    )
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("portal_orgs.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'system'"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
