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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalRetrievalGap(Base):
    __tablename__ = "portal_retrieval_gaps"
    __table_args__ = (
        CheckConstraint(
            # 'content' is the fixed gap_type for case-backed support findings:
            # a content gap can exist even when retrieval scores are healthy, so
            # the analyzer's nullable hard/soft signal is kept in ``evidence``
            # instead. 'hard'/'soft' keep their retrieval-telemetry meaning.
            "gap_type IN ('hard', 'soft', 'content')",
            name="ck_retrieval_gaps_gap_type",
        ),
        CheckConstraint(
            "resolved_by IN ('rescorer', 'review', 'manual', 'test')",
            name="ck_retrieval_gaps_resolved_by",
        ),
        # Only the six diagnoses that create inbox findings ever land on a gap
        # row; the analyzer keeps covered/non_knowledge/uncertain on the case.
        # NULL = a legacy retrieval-telemetry row (no content diagnosis).
        CheckConstraint(
            "diagnosis IS NULL OR diagnosis IN "
            "('missing', 'incomplete', 'outdated', 'contradictory', 'findability', 'audience')",
            name="ck_retrieval_gaps_diagnosis",
        ),
        Index("ix_retrieval_gaps_org_occurred", "org_id", "occurred_at"),
        Index("ix_retrieval_gaps_org_query", "org_id", "query_text"),
        Index("ix_retrieval_gaps_open", "org_id", "query_text", postgresql_where=text("resolved_at IS NULL")),
        Index("ix_retrieval_gaps_conversation", "conversation_id"),
        # One finding per case; also the hot filter for case-detail links and
        # for excluding case rows from the rescorer.
        Index("ix_retrieval_gaps_support_case", "support_case_id"),
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
    # review that opened it was deleted), 'manual' (POST /api/app/gaps/resolve),
    # or 'test' (PUT .../conversations/{id}/test marked the conversation as a
    # test message; unmarking reopens exactly the rows this closer stamped).
    # NULL while the gap is open.
    resolved_by: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    # The portal user who closed it via 'review', 'manual' or 'test'; NULL for
    # 'rescorer' and for open gaps. No ForeignKey() here for the same reason
    # as conversation_id above -- portal_api has no REFERENCES privilege on
    # portal_users (klai-owned); the FK (ON DELETE SET NULL) is added by
    # post_deploy_997e0b66f750_gaps_resolved_by_fk.sql as klai superuser.
    resolved_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # SPEC-RAG-SUPPORT-GAP: case-backed findings. A gap sourced from an
    # imported support case links to it (single case per finding); the grouped
    # inbox aggregates distinct case ids per group into ``support_case_ids``.
    # NULL keeps the historic retrieval-telemetry meaning intact. No
    # ForeignKey() here — portal_api has no REFERENCES privilege on
    # portal_support_cases in the same split as conversation_id/resolved_by
    # above; the real FK (ON DELETE CASCADE, so deleting a case removes its
    # derived findings) is added by post_deploy_<rev>_support_cases_rls.sql.
    support_case_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # Content diagnosis (missing/incomplete/outdated/contradictory/findability/
    # audience), separate from the hard/soft retrieval signal. NULL for legacy
    # telemetry rows.
    diagnosis: Mapped[str | None] = mapped_column(String(24), nullable=True, default=None)
    # Normalized grouping key for support findings: equivalent questions across
    # cases collapse into one inbox group without touching the legacy
    # query_text grouping. NULL for retrieval-telemetry rows.
    question_key: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # Who the answer is for (e.g. 'customer' | 'internal'); NULL when unknown.
    audience: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    # Analyzer evidence for this finding: message_ids, compared articles,
    # missing_information and rationale. NULL for telemetry rows.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
