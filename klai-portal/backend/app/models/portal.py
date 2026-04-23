from datetime import datetime
from typing import Literal

from sqlalchemy import (
    ARRAY,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PortalOrg(Base):
    __tablename__ = "portal_orgs"

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_org_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # @MX:NOTE: SPEC-PROV-001 M1 — soft-delete marker. When provisioning fails and rollback
    # completes, `deleted_at` is set to release the slug via the partial unique index
    # `ix_portal_orgs_slug_active` (Linear/Notion/GitLab pattern). Retry flow either
    # creates a new row (via signup) or clears this back to NULL (admin retry endpoint).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # @MX:NOTE: SPEC-PROV-001 M7 — per-row freshness marker used by the stuck-detector
    # at portal-api startup to distinguish live provisioning runs from crashed ones.
    # Updated via SQLAlchemy's `onupdate=func.now()` so any state_machine transition
    # implicitly refreshes the timestamp.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    moneybird_contact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    moneybird_subscription_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    plan: Mapped[str] = mapped_column(Text, nullable=False, default="professional", server_default="professional")
    billing_cycle: Mapped[str] = mapped_column(Text, nullable=False, default="monthly", server_default="monthly")
    seats: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    # Slug uniqueness is enforced by the partial unique index `ix_portal_orgs_slug_active`
    # (WHERE deleted_at IS NULL), defined in alembic/versions/p1r2o3v4s5b1.
    slug: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    default_language: Mapped[Literal["nl", "en"]] = mapped_column(
        String(8), nullable=False, default="nl", server_default="nl"
    )
    librechat_container: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_librechat_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_librechat_client_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    litellm_team_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    provisioning_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    mfa_policy: Mapped[Literal["optional", "recommended", "required"]] = mapped_column(
        String(16), nullable=False, default="optional", server_default="optional"
    )
    connector_dek_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    mcp_servers: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    users: Mapped[list["PortalUser"]] = relationship(back_populates="org")


class PortalUser(Base):
    __tablename__ = "portal_users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'offboarded')", name="ck_portal_users_status"),
        UniqueConstraint("zitadel_user_id", "org_id", name="uq_portal_users_zitadel_user_org"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"))
    role: Mapped[Literal["admin", "group-admin", "member"]] = mapped_column(
        String(20), nullable=False, default="member", server_default="member"
    )
    preferred_language: Mapped[Literal["nl", "en"]] = mapped_column(
        String(8), nullable=False, default="nl", server_default="nl"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    github_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Cached mapping from LibreChat MongoDB ObjectId to this portal user.
    # Populated lazily on first knowledge hook call; avoids patching LibreChat.
    librechat_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # KB scope preference — controlled via the KBScopeBar above the LibreChat iframe.
    # kb_pref_version is incremented on every PATCH and used as a cache discriminator
    # in the LiteLLM hook (30s version-pointer TTL → up to 30s propagation lag).
    kb_retrieval_enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    kb_personal_enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    kb_slugs_filter: Mapped[list[str] | None] = mapped_column(ARRAY(String(128)), nullable=True)
    kb_narrow: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    kb_pref_version: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")

    # SPEC-CHAT-TEMPLATES-001: active prompt-template IDs the user has toggled on.
    # NULL means no active templates. Validated at PATCH time to belong to caller's org.
    active_template_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)

    org: Mapped["PortalOrg"] = relationship(back_populates="users")


class PortalOrgAllowedDomain(Base):
    __tablename__ = "portal_org_allowed_domains"
    __table_args__ = (
        UniqueConstraint("org_id", "domain", name="uq_org_allowed_domains_org_domain"),
        UniqueConstraint("domain", name="uq_org_allowed_domains_domain_global"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


class PortalJoinRequest(Base):
    __tablename__ = "portal_join_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("portal_orgs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
