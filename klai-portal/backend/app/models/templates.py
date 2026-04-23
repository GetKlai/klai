"""Prompt template model.

Per SPEC-CHAT-TEMPLATES-001. Org-scoped response-scaffolds applied in the
LiteLLM pre-call hook. Distinct from guardrail Rules (SPEC-CHAT-GUARDRAILS-001):
templates are a productfeature for response styling, not a safety layer.

# @MX:NOTE: RLS strict is enforced at the DB level (see the
# `add_portal_templates` migration). Any query path MUST have called
# `set_tenant()` first, otherwise the query returns zero rows.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalTemplate(Base):
    __tablename__ = "portal_templates"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_portal_template_org_slug"),
        Index("ix_portal_template_org_id_active", "org_id", "is_active"),
        CheckConstraint(
            "char_length(prompt_text) <= 8000",
            name="ck_portal_template_prompt_len",
        ),
        CheckConstraint(
            "scope IN ('org', 'personal')",
            name="ck_portal_template_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, server_default="org")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
