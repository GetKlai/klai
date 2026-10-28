"""SQLAlchemy model for the proposal and the owner's decision per knowledge-gap cluster."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
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
        # The weekly generation cap counts per org.
        Index("ix_gap_proposals_org_generated", "org_id", "generated_at"),
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
    decision: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'none'"))
    # The portal user who decided. No ForeignKey(): portal_api has no
    # REFERENCES privilege on the klai-owned portal_users; the FK (ON DELETE
    # SET NULL) is added by post_deploy_77d01eafb800_gap_proposals_rls.sql.
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
