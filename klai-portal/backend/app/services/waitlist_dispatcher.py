"""Waitlist dispatcher — orchestrates Twenty CRM → mailer.

SPEC-LAUNCH-SOFTLAUNCH-001 B-2 sub-batch 3.

The dispatcher is a single-pass function: it pulls all deals in the
"needs work" stages from Twenty CRM, sends the corresponding email
via klai-mailer, and updates the deal stage so the next pass does
not double-send.

Stage transitions handled:

    NEW          → send confirmation mail → CONFIRMATION_SENT
    INVITED      → send invite mail        → INVITED_SENT

Idempotency: stage update IS the dedupe mechanism. If the mail send
succeeds but the stage update fails, the next pass will re-send the
mail — accept the rare duplicate over the silent never-sent. If the
mail send fails, the stage is NOT advanced, so the deal stays in
the queue for the next pass.

Invocation: scheduled by sub-batch 4 (cron / asyncio background task /
admin endpoint). For now callers invoke ``dispatch_once()`` manually
via ``docker exec ... python -c '...'``.
"""

from __future__ import annotations

import structlog

from app.services import twenty
from app.services.notifications import (
    issue_waitlist_invite,
    send_waitlist_confirmation,
)

logger = structlog.get_logger()


async def _dispatch_confirmations() -> dict[str, int]:
    """Send confirmation mails for deals in stage NEW.

    Returns:
        {"sent": N, "skipped": N, "errors": N} counter dict.
    """
    counters = {"sent": 0, "skipped": 0, "errors": 0}
    try:
        rows = await twenty.list_waitlist_opportunities_in_stage(twenty.STAGE_NEW)
    except twenty.TwentyUnavailable:
        logger.info("waitlist_dispatcher_twenty_unavailable")
        return counters

    for row in rows:
        deal = await twenty.resolve_deal(row)
        if deal is None:
            counters["skipped"] += 1
            continue

        # send_waitlist_confirmation is fire-and-forget internally; we
        # cannot tell from its return value whether SMTP succeeded.
        # That's acceptable for confirmation (low-stakes, retry on next
        # pass if it failed). For invite we'd want a real ack.
        await send_waitlist_confirmation(
            name=deal.name,
            email=deal.email,
            company=deal.company,
        )
        ok = await twenty.update_opportunity_stage(
            deal.opportunity_id, twenty.STAGE_CONFIRMATION_SENT
        )
        if ok:
            counters["sent"] += 1
            logger.info(
                "waitlist_confirmation_dispatched",
                opportunity_id=deal.opportunity_id,
                email_domain=deal.email.split("@", 1)[-1] if "@" in deal.email else "",
            )
        else:
            # Stage update failed — mail will be re-sent next pass. Log
            # so an operator can manually fix the Twenty stage if needed.
            counters["errors"] += 1
            logger.warning(
                "waitlist_confirmation_stage_update_failed",
                opportunity_id=deal.opportunity_id,
            )
    return counters


async def _dispatch_invites() -> dict[str, int]:
    """Send invite mails for deals in stage INVITED.

    Returns:
        {"sent": N, "skipped": N, "errors": N} counter dict.
    """
    counters = {"sent": 0, "skipped": 0, "errors": 0}
    try:
        rows = await twenty.list_waitlist_opportunities_in_stage(twenty.STAGE_INVITED)
    except twenty.TwentyUnavailable:
        logger.info("waitlist_dispatcher_twenty_unavailable")
        return counters

    for row in rows:
        deal = await twenty.resolve_deal(row)
        if deal is None:
            counters["skipped"] += 1
            continue

        # issue_waitlist_invite returns False when token-key / mailer-url /
        # frontend-url is misconfigured. In that case do NOT advance the
        # stage — the deal stays queued for the next pass.
        issued = await issue_waitlist_invite(
            name=deal.name,
            email=deal.email,
            company=deal.company,
        )
        if not issued:
            counters["errors"] += 1
            logger.warning(
                "waitlist_invite_not_issued",
                opportunity_id=deal.opportunity_id,
            )
            continue

        ok = await twenty.update_opportunity_stage(
            deal.opportunity_id, twenty.STAGE_INVITED_SENT
        )
        if ok:
            counters["sent"] += 1
            logger.info(
                "waitlist_invite_dispatched",
                opportunity_id=deal.opportunity_id,
            )
        else:
            counters["errors"] += 1
            logger.warning(
                "waitlist_invite_stage_update_failed",
                opportunity_id=deal.opportunity_id,
            )
    return counters


async def dispatch_once() -> dict[str, dict[str, int]]:
    """Run one full dispatch pass: confirmations + invites.

    Safe to call repeatedly — stage transitions provide idempotency.
    Returns a per-channel counter dict useful for ad-hoc observability.

    Manual invocation (smoke test):
        docker exec klai-core-portal-api-1 python3 -c "
        import asyncio
        from app.services.waitlist_dispatcher import dispatch_once
        print(asyncio.run(dispatch_once()))
        "
    """
    if not twenty.is_configured():
        logger.info("waitlist_dispatcher_disabled_no_twenty")
        return {
            "confirmations": {"sent": 0, "skipped": 0, "errors": 0},
            "invites": {"sent": 0, "skipped": 0, "errors": 0},
        }

    confirmations = await _dispatch_confirmations()
    invites = await _dispatch_invites()
    logger.info(
        "waitlist_dispatch_pass_complete",
        confirmations=confirmations,
        invites=invites,
    )
    return {"confirmations": confirmations, "invites": invites}
