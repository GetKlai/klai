"""SPEC-PORTAL-RBAC-REFACTOR-001 REQ-14 + REQ-18 — fan out role-change
notifications to klai-knowledge-mcp.

Background-task lifetime: ``asyncio`` only keeps a weak reference to tasks
returned by ``loop.create_task``. If the only reference lives on the
event loop it can be garbage-collected mid-flight, silently cancelling
the notification. We pin every task in a module-level set and let it
remove itself on completion (RUF006-compliant).

When an admin changes a user's role in portal-api, the MCP server must
emit ``notifications/tools/list_changed`` on every active session for
that user so Klai-controlled clients (LibreChat-MCP-bridge) reload the
filtered tool-list without a reconnect. Third-party clients (Claude
Desktop, ChatGPT desktop) honour the notification per MCP spec — some
auto-refresh, others require user action.

This module is the SENDER side of the cross-service hop. The receiver
lives in ``klai-knowledge-mcp/main.py::_notify_role_change``.

REQ-14 framing: "caller-services SHALL signen die claim in elke
MCP-call". The strict-read of "signen" is a JWT signature; the chosen
implementation uses ``X-Internal-Secret`` (constant-time compared,
shared secret per SPEC-SEC-INTERNAL-001) over an internal-network HTTP
call. The trust semantics are equivalent: portal-api is the AUTHORITY
for the user's effective_role and the MCP server trusts portal-api on
this channel.

The call is FIRE-AND-FORGET. Any failure is logged but never bubbled to
the user-facing role-change response — the worst case (notification
lost) degrades to "the client's tool-list is briefly stale until the
next reconnect/poll", which is the existing behaviour without REQ-18.
"""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import structlog

from app.core.config import settings
from app.trace import get_trace_headers

logger = structlog.get_logger()


def _hash_user_id(user_id: str) -> str:
    """PII-clean log key — same sha256[:16] format the receiver uses on
    the knowledge-mcp side, so the two sides of the same notify-hop can
    be correlated by ``user_id_hash`` in VictoriaLogs."""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


# Per-call timeout. Short because the MCP endpoint is in-cluster and the
# operation is tiny (look up a dict, fan out per-session SSE writes).
# Five seconds is plenty; longer would block the role-change response
# behind the fire-and-forget shield's task scheduling on slow networks.
_NOTIFY_TIMEOUT_SECONDS = 5.0

# Pin in-flight tasks so asyncio's weak reference doesn't garbage-collect
# the notification mid-HTTP-call. Tasks self-remove via the done-callback.
_inflight_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


async def _notify_role_change_inner(zitadel_user_id: str) -> None:
    """Single-shot HTTP POST to klai-knowledge-mcp's notify endpoint."""
    url = settings.knowledge_mcp_url.rstrip("/") + "/internal/notify-role-change"
    # ``settings.internal_secret`` is the canonical shared secret; in prod
    # it is the same value that knowledge-mcp validates against under the
    # ``PORTAL_INTERNAL_SECRET`` env var. SOPS keeps them in sync.
    secret = settings.internal_secret
    if not secret:
        logger.warning("mcp_role_notify_skipped_no_secret")
        return

    async with httpx.AsyncClient(timeout=_NOTIFY_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            url,
            json={"user_id": zitadel_user_id},
            headers={
                "X-Internal-Secret": secret,
                **get_trace_headers(),
            },
        )
        if resp.status_code >= 400:
            logger.warning(
                "mcp_role_notify_non_2xx",
                status_code=resp.status_code,
                # PII-clean: hash matches the receiver's log key so a single
                # ``user_id_hash:<x>`` query in VictoriaLogs surfaces both
                # sides of the cross-service hop.
                user_id_hash=_hash_user_id(zitadel_user_id),
            )
        else:
            try:
                notified = resp.json().get("notified", 0)
            except Exception:
                notified = -1
            logger.info(
                "mcp_role_notify_ok",
                notified=notified,
                user_id_hash=_hash_user_id(zitadel_user_id),
            )


def fire_role_change_notification(zitadel_user_id: str) -> None:
    """Schedule a fire-and-forget MCP role-change notification.

    Caller MUST be on a running asyncio event loop (any FastAPI request
    handler qualifies). The returned ``Task`` is intentionally not
    awaited — completing the role-change response should never wait on
    a downstream MCP fan-out.

    Errors are caught at the inner-coroutine level and logged at
    warning. They never propagate.
    """

    async def _runner() -> None:
        try:
            await _notify_role_change_inner(zitadel_user_id)
        except Exception:
            # Fire-and-forget contract: never raise out of the task.
            # ``logger.warning`` with ``exc_info=True`` keeps the traceback
            # in VictoriaLogs without crashing the role-change request.
            logger.warning(
                "mcp_role_notify_failed",
                exc_info=True,
                user_id_hash=_hash_user_id(zitadel_user_id),
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — caller is mis-using the helper. Log and skip.
        logger.warning("mcp_role_notify_no_event_loop")
        return

    task = loop.create_task(_runner())
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
