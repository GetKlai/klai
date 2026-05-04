"""Notification helpers for sending emails via klai-mailer (SPEC-AUTH-006 R7/R16).

SPEC-SEC-MAILER-INJECTION-001 contract changes (landing with REQ-1..4):
- `notify_admin_join_request` now passes `org_id` so klai-mailer can
  resolve the expected admin recipient via
  `GET /internal/org/<id>/admin-email`. The caller MUST supply `org_id`
  and the pre-resolved `admin_email`; klai-mailer validates them against
  each other and rejects a mismatch with 400.
- `notify_user_join_approved` passes `email` inside variables so
  klai-mailer can bind the recipient against the schema field.
"""

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()


async def notify_admin_join_request(
    *,
    email: str,
    display_name: str,
    org_id: int,
    admin_email: str,
) -> None:
    """Send join request notification email to org admins via klai-mailer.

    Caller MUST pass `org_id` AND `admin_email`. Klai-mailer resolves the
    expected admin via portal-api and rejects a mismatch with 400.
    C7.3 — never fail the main flow; exceptions are caught here.
    """
    if not settings.mailer_url:
        logger.warning("mailer_url_not_configured_admin_join", org_id=org_id)
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "join_request_admin",
                    "to": admin_email,
                    "locale": "nl",
                    "variables": {
                        "name": display_name,
                        "email": email,
                        "org_id": org_id,
                    },
                },
            )
    except Exception:
        logger.warning("mailer_notify_admin_failed", org_id=org_id, exc_info=True)


async def notify_user_join_approved(
    *,
    email: str,
    display_name: str,
    workspace_url: str,
) -> None:
    """Send approval confirmation email to the user via klai-mailer.

    Klai-mailer binds the recipient to `variables.email`; the handler
    returns 400 if `to` differs from it.
    """
    if not settings.mailer_url:
        logger.warning("mailer_url_not_configured_approved")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "join_request_approved",
                    "to": email,
                    "locale": "nl",
                    "variables": {
                        "name": display_name,
                        "email": email,
                        "workspace_url": workspace_url,
                    },
                },
            )
    except Exception:
        logger.warning("mailer_notify_approved_failed", exc_info=True)


async def notify_auto_join_admin(
    *,
    email: str,
    display_name: str,
    domain: str,
    org_id: int,
    admin_email: str,
) -> None:
    """Send auto-join admin notification email via klai-mailer.

    @MX:NOTE SPEC-AUTH-009 R7 -- informs admins when a domain_match user
    auto-joined (auto_accept=True). Uses the auto_join_admin_notification
    template instead of join_request_admin (different message, no approval link).
    """
    if not settings.mailer_url:
        logger.warning("mailer_url_not_configured_auto_join", org_id=org_id)
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "auto_join_admin_notification",
                    "to": admin_email,
                    "locale": "nl",
                    "variables": {
                        "name": display_name,
                        "email": email,
                        "domain": domain,
                        "admin_email": admin_email,
                        "org_id": org_id,
                    },
                },
            )
    except Exception:
        logger.warning("mailer_notify_auto_join_failed", org_id=org_id, exc_info=True)
