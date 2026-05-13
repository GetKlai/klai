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

from urllib.parse import quote

import httpx
import structlog

from app.core.config import settings
from app.services.waitlist_token import (
    DEFAULT_TTL_SECONDS,
    WaitlistTokenUnavailable,
    sign_invite_token,
)

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


# ---------------------------------------------------------------------------
# SPEC-LAUNCH-SOFTLAUNCH-001 B-2 — waitlist confirmation + invite
# ---------------------------------------------------------------------------


def _locale_from_email(email: str) -> str:
    """NL for .nl recipients, EN otherwise. Q4 assumption."""
    return "nl" if email.strip().lower().endswith(".nl") else "en"


async def send_waitlist_confirmation(
    *,
    name: str,
    email: str,
    company: str,
) -> None:
    """Send the post-submit confirmation email to a waitlist subscriber.

    SPEC-LAUNCH-SOFTLAUNCH-001 B-2 Q3. Fire-and-forget: exceptions are
    swallowed so the caller (Twenty CRM poller) keeps iterating other
    deals even if the mailer is down.

    Recipient binding: klai-mailer asserts ``to == variables.email``.
    """
    if not settings.mailer_url:
        logger.warning("mailer_url_not_configured_waitlist_confirmation")
        return

    locale = _locale_from_email(email)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "waitlist_confirmation",
                    "to": email,
                    "locale": locale,
                    "variables": {
                        "name": name,
                        "email": email,
                        "company": company,
                    },
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "mailer_notify_waitlist_confirmation_4xx5xx",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
    except Exception:
        logger.warning("mailer_notify_waitlist_confirmation_failed", exc_info=True)


async def send_waitlist_invite(
    *,
    name: str,
    email: str,
    company: str,
    signup_url: str,
    expires_in_hours: int,
) -> None:
    """Send the magic-link invite email to a waitlist subscriber.

    SPEC-LAUNCH-SOFTLAUNCH-001 B-2 Q1/Q2. Caller generates the signed
    token + builds ``signup_url``.
    """
    if not settings.mailer_url:
        logger.warning("mailer_url_not_configured_waitlist_invite")
        return

    locale = _locale_from_email(email)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "waitlist_invite",
                    "to": email,
                    "locale": locale,
                    "variables": {
                        "name": name,
                        "email": email,
                        "company": company,
                        "signup_url": signup_url,
                        "expires_in_hours": expires_in_hours,
                    },
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "mailer_notify_waitlist_invite_4xx5xx",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
    except Exception:
        logger.warning("mailer_notify_waitlist_invite_failed", exc_info=True)


async def send_onboarding_invite(
    *,
    name: str,
    email: str,
    cal_url: str,
    locale: str | None = None,
) -> dict[str, str | bool] | None:
    """Send Mail 1 of the onboarding drip to a waitlist subscriber.

    Triggered by a CRM-side button (Twenty Workflow → portal-api
    /internal/onboarding/start). Calls klai-mailer's `/internal/send`
    with the `onboarding_invite` template — recipient is bound to
    `variables.email` server-side, so the body's `to` and `email` MUST
    match (mailer returns 400 otherwise).

    Returns the mailer response dict (`{sent, subject, body_html}`) on
    success so callers can echo the rendered mail back to the user
    (Twenty workflow run log + Note-on-Person step).
    Returns None on every failure path (misconfigured, 4xx/5xx, network
    error). The caller decides whether to surface failure to the user.
    """
    if not settings.mailer_url:
        logger.warning("mailer_url_not_configured_onboarding_invite")
        return None

    effective_locale = locale or _locale_from_email(email)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.mailer_url}/internal/send",
                headers={"X-Internal-Secret": settings.internal_secret},
                json={
                    "template": "onboarding_invite",
                    "to": email,
                    "locale": effective_locale,
                    "variables": {
                        "name": name,
                        "email": email,
                        "cal_url": cal_url,
                    },
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "mailer_notify_onboarding_invite_4xx5xx",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return None
            try:
                return resp.json()
            except ValueError:
                logger.warning("mailer_notify_onboarding_invite_bad_json", body=resp.text[:300])
                return {"sent": True, "subject": "", "body_html": ""}
    except Exception:
        logger.warning("mailer_notify_onboarding_invite_failed", exc_info=True)
        return None


async def issue_waitlist_invite(
    *,
    name: str,
    email: str,
    company: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """Generate token + build URL + send invite mail.

    Returns True if the mail send was attempted, False if a precondition
    failed (no token key configured, no mailer URL, no frontend URL).
    """
    if not settings.mailer_url or not settings.frontend_url:
        logger.warning(
            "issue_waitlist_invite_misconfigured",
            mailer_url_set=bool(settings.mailer_url),
            frontend_url_set=bool(settings.frontend_url),
        )
        return False

    try:
        token = sign_invite_token(email, company, ttl_seconds=ttl_seconds)
    except WaitlistTokenUnavailable:
        logger.warning("issue_waitlist_invite_no_token_key")
        return False

    base = settings.frontend_url.rstrip("/")
    signup_url = f"{base}/signup?token={quote(token)}&email={quote(email)}&company={quote(company)}"

    expires_in_hours = max(1, ttl_seconds // 3600)
    await send_waitlist_invite(
        name=name,
        email=email,
        company=company,
        signup_url=signup_url,
        expires_in_hours=expires_in_hours,
    )
    return True
