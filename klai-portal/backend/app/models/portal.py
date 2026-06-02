from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
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
from sqlalchemy.dialects.postgresql import JSONB
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
    # @MX:DEPRECATED — SPEC-PORTAL-PRICING-PER-USER-001 Phase 6 (2026-05-12).
    # ``plan`` is no longer the capability-intersection axis (Phase 4 moved
    # that to ``portal_users.seat_type``) and no longer gates role assignment
    # (Phase 3 removed ``assert_role_allowed_for_plan``). The column stays
    # for the legacy Moneybird billing path (``app/api/billing.py`` +
    # webhooks.py) until Phase 5b ships the real per-seat-type Moneybird
    # migration. Phase 6 dropped the ``portal_orgs_plan_check`` constraint
    # so the column is now free-form.
    plan: Mapped[str] = mapped_column(Text, nullable=False, default="chat", server_default="chat")
    billing_cycle: Mapped[str] = mapped_column(Text, nullable=False, default="monthly", server_default="monthly")
    # @MX:DEPRECATED — SPEC-PORTAL-PRICING-PER-USER-001 Phase 6 (2026-05-12).
    # The hard ``portal_orgs.seats`` cap on invite was removed in Phase 3.
    # Phase 5b's follow-up SPEC drops this column after the real per-seat-
    # type Moneybird migration ships. Until then ``seats`` is still read by
    # the legacy billing path.
    seats: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    # SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light, 2026-05-12) — per-
    # tenant opt-in for the future Moneybird per-seat-type billing path.
    # Default ``false`` for every existing tenant. A tenant admin flips
    # this via the ``/admin/billing`` "switch to per-user billing" CTA
    # (Phase 5b lands the actual mutation; Phase 5 light ships the flag
    # column + a 501 stub on the switch endpoint).
    billing_per_seat_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
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
    # SPEC-INFRA-TENANT-DELETE H2 — external resource IDs persisted at
    # provisioning time (orchestrator finalize) so deprovisioning deletes the
    # exact LiteLLM team / Zitadel OIDC app instead of resolving via fuzzy
    # list lookups that silently returned "" (= "skip") on a false-negative.
    # NULL on legacy rows provisioned before migration e9f1a2b3c4d5;
    # deprovisioning_orchestrator._load_state falls back to the resolve path
    # for those rows.
    litellm_team_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zitadel_oidc_app_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provisioning_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R2 — populated by deprovisioning
    # orchestrator on definitive step failure. Shape: {"step": <name>,
    # "error": <truncated>, "attempt": int, "failed_at": <iso>}. NULL on every
    # other state. Cleared by admin retry endpoint before re-running.
    last_failure: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mfa_policy: Mapped[Literal["optional", "recommended", "required"]] = mapped_column(
        String(16), nullable=False, default="optional", server_default="optional"
    )
    connector_dek_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    mcp_servers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # @MX:ANCHOR SPEC-AUTH-009 R1 -- founder's verified email domain; immutable after creation.
    # C1.2: No endpoint exposes UPDATE for this column. Manual DB intervention only.
    # C1.4: Multiple workspaces may share the same primary_domain.
    primary_domain: Mapped[str] = mapped_column(String(253), nullable=False, server_default="")
    # @MX:NOTE SPEC-AUTH-009 R5 -- when True, domain_match picker entries skip join-request
    # approval and directly INSERT a portal_users row (R4-C4.3). Default False.
    auto_accept_same_domain: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # @MX:NOTE: SPEC-PORTAL-EXTENSIONS-UNIFY-001 (2026-05-12) — single gating
    # column for all tenant extensions. Stores every Klai-staff-managed feature
    # unlock for this org: scribe, docs (= user-facing products with profile-
    # floor), partner_api, widgets, custom_mcps (= platform-only gates).
    # NOT editable by tenant admins; mutated via /api/admin/extensions and
    # /api/admin/orgs/{slug}/platform-unlocks (both gated by
    # require_platform_admin). The legacy `enabled_addons` column was dropped
    # in the same migration that introduced this comment — data was copied
    # forward by the post-deploy SQL (set-union with the existing
    # platform_unlocked_features values).
    platform_unlocked_features: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        default=list,
        server_default="{}",
    )
    # @MX:NOTE: SPEC-PRIVACY-QUERY-SHADOW-001 REQ-1 — per-tenant telemetry mode.
    # 'shadow' (default): embedding + symbolic features only, no raw query persisted.
    # 'off': zero telemetry. 'full': raw query persisted with 7d TTL (audit-trailed).
    # The underlying ENUM (telemetry_level_t) is created by alembic migration
    # g5h6i7j8k9l0; create_type=False so SQLAlchemy doesn't try to recreate it.
    telemetry_level: Mapped[Literal["off", "shadow", "full"]] = mapped_column(
        sa.Enum(
            "off",
            "shadow",
            "full",
            name="telemetry_level_t",
            create_type=False,
        ),
        nullable=False,
        default="shadow",
        server_default="shadow",
    )

    users: Mapped[list["PortalUser"]] = relationship(back_populates="org")


class PortalUser(Base):
    __tablename__ = "portal_users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'offboarded')", name="ck_portal_users_status"),
        UniqueConstraint("zitadel_user_id", "org_id", name="uq_portal_users_zitadel_user_org"),
        # SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 — ``seat_type`` is the
        # per-user account-type (billing tier) DERIVED from ``role`` via
        # ``app.core.seats.suggest_seat``. Phase 1 (v0.1.0-v0.4.0)
        # treated it as decoupled from role with an admin-facing
        # selector; v0.5.0 collapses to role-derives-tier. The DB
        # column + CHECK + migration f66c546c12eb stay; only the UX +
        # invite-side handler changes. Migration f1ff304b7b0a drops the
        # ``'viewer'`` value from the CHECK; viewer tier is gone in
        # v0.5.0.
        CheckConstraint("seat_type IN ('chat', 'knowledge')", name="ck_portal_users_seat_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"))
    role: Mapped[Literal["personal", "company", "kb_manager", "group_manager", "admin"]] = mapped_column(
        sa.Enum(
            "personal",
            "company",
            "kb_manager",
            "group_manager",
            "admin",
            name="portal_user_role",
            create_type=False,  # alembic migration 59fff72b480b creates the type
        ),
        nullable=False,
        default="company",
        server_default="company::portal_user_role",
    )
    # SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0: per-user account type,
    # DERIVED from ``role`` via ``app.core.seats.suggest_seat`` (no
    # admin UI override). Personal/company -> chat, KMs/admins ->
    # knowledge. PATCH /seat endpoint stays callable for admin-tooling
    # escape-hatch but is no longer surfaced in the FE. CHECK
    # constraint enforces the two-value domain at the DB layer.
    seat_type: Mapped[Literal["chat", "knowledge"]] = mapped_column(
        String(16),
        nullable=False,
        default="chat",
        server_default="chat",
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

    # SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-4: user-delete state machine columns.
    # NULL = no deletion attempt. 'failed_partial' = one step failed; use
    # POST .../retry-delete to restart the sequence.
    deletion_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_reason: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    last_attempted_step: Mapped[str | None] = mapped_column(String(64), nullable=True)

    org: Mapped["PortalOrg"] = relationship(back_populates="users")


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
