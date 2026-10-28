"""Slack workspace link of an org — SPEC-SLACK-001 §6.2.

One row per org and one per Slack workspace (both UNIQUE, decision D2). The row
is written through ``POST /internal/slack/link-oauth`` once a workspace approved
the app in Slack OAuth, and is the portal's record of which workspace holds the
org's Slack key. Category-D RLS (``post_deploy_sl4c5k6i7n8s_slack_installations.sql``).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalSlackInstallation(Base):
    __tablename__ = "portal_slack_installations"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_portal_slack_installations_org_id"),
        UniqueConstraint("team_id", name="uq_portal_slack_installations_team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    partner_api_key_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("partner_api_keys.id", ondelete="CASCADE"), nullable=False
    )
    installed_by_slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
