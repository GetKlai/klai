"""Notification helpers for sending emails via klai-mailer (SPEC-AUTH-006 R7/R16)."""

import structlog

from app.core.config import settings

logger = structlog.get_logger()

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


async def notify_admin_join_request(
    email: str,
    display_name: str,
    admin_email: str | None = None,
) -> None:
    """Send join request notification email to org admins via klai-mailer.

    C7.3: Never fail the main flow — exceptions are caught by the caller.
    """
    if not settings.mailer_url:
        logger.warning("mailer_url not configured — skipping join request notification")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "join_request_admin",
                    "to": admin_email or "",
                    "locale": "nl",
                    "variables": {
                        "name": display_name,
                        "email": email,
                    },
                },
            )
    except Exception:
        logger.warning("klai-mailer notification failed", email=email)


async def notify_user_join_approved(
    email: str,
    display_name: str,
    workspace_url: str,
) -> None:
    """Send approval confirmation email to the user via klai-mailer."""
    if not settings.mailer_url:
        logger.warning("mailer_url not configured — skipping approval notification")
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
                        "workspace_url": workspace_url,
                    },
                },
            )
    except Exception:
        logger.warning("klai-mailer approval notification failed", email=email)
