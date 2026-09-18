"""SQLAlchemy model for imported support cases (restricted evidence).

One tenant-scoped table holds the validated case payload as JSONB plus its
analysis. Raw support cases are NOT ordinary KB content: the case's ``kb_slug``
names the comparison scope, never a publication destination, so the answer
extracted from a case cannot leak back into the corpus being measured
(``docs/architecture/support-gap-detection.md`` → "Source and evidence
boundary").

RLS: Category-D (strict). Every access path sets ``app.current_org_id`` before
touching this table; the ``tenant_isolation`` policy is applied by
``post_deploy_<rev>_support_cases_rls.sql`` as the ``klai`` superuser (portal_api
is not the table owner). Cascading deletion of derived gap findings is the FK
``portal_retrieval_gaps.support_case_id`` (ON DELETE CASCADE), also added there.
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalSupportCase(Base):
    __tablename__ = "portal_support_cases"
    __table_args__ = (
        # Stable identity: a repeat import of the same source case lands on the
        # same row, so counts never inflate (contract: "Case upserts serialize
        # by stable identity").
        UniqueConstraint(
            "org_id",
            "kb_slug",
            "source",
            "account_id",
            "external_id",
            name="uq_portal_support_cases_identity",
        ),
        CheckConstraint(
            "source IN ('hubspot', 'audio')",
            name="ck_portal_support_cases_source",
        ),
        CheckConstraint(
            "status IN ('incomplete', 'pending', 'analyzed', 'failed')",
            name="ck_portal_support_cases_status",
        ),
        Index("ix_portal_support_cases_org_kb", "org_id", "kb_slug"),
        Index("ix_portal_support_cases_connector", "connector_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Comparison scope only, never a publication target. String slug (not FK)
    # mirrors how the gap table stores nearest_kb_slug — the KB is resolved and
    # access-checked at the API boundary.
    kb_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    # Owning connector; NULL for audio transcripts imported straight to a KB.
    # UUID to match PortalConnector.id (a Postgres FK cannot relate VARCHAR to
    # UUID). No ForeignKey() here — this table is klai-owned, so ALTER TABLE to
    # add the FK runs as the klai superuser in
    # post_deploy_<rev>_support_cases_rls.sql (ON DELETE CASCADE, so deleting a
    # connector removes its cases and their findings).
    connector_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, default=None)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    # The portal user credited as owner (connector.created_by, or the importer
    # for a transcript). Kept for the analyzer's authorized-identity scoping.
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    # Validated SupportCasePayload, verbatim. Server-computed hash of its
    # evidence drives change detection.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Version of app.services.support_case_analysis that produced ``analysis``;
    # NULL until analysed. A repeat run under a newer version updates in place.
    analysis_version: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # All analyzed outcomes (covered/non_knowledge/uncertain included) so an
    # empty gap list is distinguishable from failed analysis. NULL until run.
    analysis: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    # Human reviews of the analysis, kept apart from the model output: a JSONB
    # map ``"{analysis_revision}:{finding_index}" -> {decision, note,
    # reviewed_by, reviewed_at}``. NULL/`{}` on rows never reviewed. Entries under
    # a superseded revision are retained (retention/audit); the detail API only
    # surfaces the review whose key matches the current revision. See
    # ``app/services/support_case_reviews.py``.
    reviews: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
