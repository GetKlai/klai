"""Taxonomy models for knowledge base categorisation."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalTaxonomyNode(Base):
    __tablename__ = "portal_taxonomy_nodes"
    __table_args__ = (
        Index("ix_taxonomy_nodes_kb_id", "kb_id"),
        Index("ix_taxonomy_nodes_parent_id", "parent_id"),
        Index(
            "uq_taxonomy_nodes_sibling_name",
            "kb_id",
            "parent_id",
            "name",
            unique=True,
            postgresql_where="parent_id IS NOT NULL",
        ),
        Index(
            "uq_taxonomy_nodes_root_name",
            "kb_id",
            "name",
            unique=True,
            postgresql_where="parent_id IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("portal_taxonomy_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


class PortalTaxonomyProposal(Base):
    __tablename__ = "portal_taxonomy_proposals"
    __table_args__ = (
        Index("ix_taxonomy_proposals_kb_status", "kb_id", "status"),
        CheckConstraint(
            "proposal_type IN ('new_node', 'merge', 'split', 'rename')",
            name="ck_taxonomy_proposal_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_taxonomy_proposal_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    proposal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
