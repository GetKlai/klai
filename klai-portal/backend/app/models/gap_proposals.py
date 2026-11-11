"""SQLAlchemy model for the proposal and the owner's decision per knowledge-gap cluster."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalGapProposal(Base):
    """What is missing for one gap cluster and where it belongs, plus the owner's decision on the cluster.

    One row per cluster of the weekly gap list (``cluster_key`` from
    app/services/knowledge_gap_report.py). A decision can exist without a
    generated proposal, which is why the proposal fields are nullable; a
    decided cluster leaves the list.
    """

    __tablename__ = "portal_gap_proposals"
    __table_args__ = (
        UniqueConstraint("org_id", "cluster_key", name="uq_gap_proposals_org_cluster"),
        CheckConstraint("where_kind IN ('existing', 'new')", name="ck_gap_proposals_where_kind"),
        CheckConstraint("decision IN ('none', 'rejected', 'out_of_scope')", name="ck_gap_proposals_decision"),
        CheckConstraint("status IN ('generating', 'ready', 'failed')", name="ck_gap_proposals_status"),
        # The weekly cap counts the model calls of an org's rows attempted in the window.
        Index("ix_gap_proposals_org_attempted", "org_id", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    cluster_key: Mapped[str] = mapped_column(String, nullable=False)
    missing: Mapped[str | None] = mapped_column(String, nullable=True)
    where_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    where_title: Mapped[str | None] = mapped_column(String, nullable=True)
    where_url: Mapped[str | None] = mapped_column(String, nullable=True)
    where_section: Mapped[str | None] = mapped_column(String, nullable=True)
    example_text: Mapped[str | None] = mapped_column(String, nullable=True)
    open_facts: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The model call is reserved before it is made: 'generating' while it runs,
    # 'ready' with a proposal, 'failed' with ``failure_reason``. NULL on a row
    # that only carries a decision. ``attempts`` is the number of model calls
    # on this row and ``attempted_at`` the last one, which the weekly cap counts,
    # failed calls included.
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'none'"))
    # The portal user who decided. No ForeignKey(): portal_api has no
    # REFERENCES privilege on the klai-owned portal_users; the FK (ON DELETE
    # SET NULL) is added by post_deploy_77d01eafb800_gap_proposals_rls.sql.
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PortalGapKeptQuestion(Base):
    """A counted widget gap turn kept past the widget-message retention, for orgs at telemetry ``full``.

    The retention sweep (app/services/widget_messages_retention.py) writes the
    row in the transaction that deletes the turn's messages, so a turn is
    either live in ``widget_messages`` or here. Only what the gap list needs
    survives: the visitor's question as the list shows it (masked and cut), the
    signals the grouping reads (``kept_question`` in
    app/services/knowledge_gap_report.py), the time, and a keyed hash of the
    conversation. Removed after ``KEPT_QUESTION_DAYS``, when its group is
    decided, when its conversation is marked as a test, and when the org leaves
    ``full``.
    """

    __tablename__ = "portal_gap_kept_questions"
    __table_args__ = (
        UniqueConstraint("org_id", "message_id", name="uq_gap_kept_questions_org_message"),
        Index("ix_gap_kept_questions_org_turn_at", "org_id", "turn_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    # The id of the deleted assistant message. Nothing is left to point at; it
    # keeps a turn from being kept twice, or read twice next to its live row.
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # conversation_ref() in knowledge_gap_report: an HMAC of org and conversation
    # id, so the list counts distinct conversations without a link back to one.
    conversation_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    cluster_key: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(String(200), nullable=False)
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False)
    turn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
