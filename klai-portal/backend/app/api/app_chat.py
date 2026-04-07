"""App-facing chat health API.

GET /api/app/chat-health — probe the tenant's LibreChat container for endpoint availability.

Called by the frontend chat page to detect broken iframe state before/during load.
Returns a structured health status so the UI can show actionable feedback instead of
silently hanging on "Welcome back" forever.
"""

import logging

import httpx
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.services.events import emit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app", tags=["app-chat"])

# LibreChat containers expose port 3080 on the Docker network.
# Timeout is aggressive: this is a pre-flight check, not a data fetch.
_PROBE_TIMEOUT = 4.0


class ChatHealthOut(BaseModel):
    healthy: bool
    reason: str | None = None


@router.get("/chat-health", response_model=ChatHealthOut)
async def get_chat_health(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ChatHealthOut:
    """Probe the tenant's LibreChat instance for endpoint availability.

    Checks:
    1. Org is provisioned (has a LibreChat container)
    2. LibreChat container responds on /health
    3. LibreChat has at least one configured endpoint (/api/endpoints)

    Returns healthy=false with a machine-readable reason so the frontend
    can show specific feedback (not provisioned / container down / no endpoints).
    """
    _, org, _ = await _get_caller_org(credentials, db)

    if not org.librechat_container:
        return ChatHealthOut(healthy=False, reason="not_provisioned")

    if org.provisioning_status != "ready":
        return ChatHealthOut(healthy=False, reason="provisioning_in_progress")

    base_url = f"http://{org.librechat_container}:3080"

    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            # Step 1: container alive?
            health_resp = await client.get(f"{base_url}/health")
            if health_resp.status_code != 200:
                _emit_failure(org.id, "container_unhealthy")
                return ChatHealthOut(healthy=False, reason="container_unhealthy")

            # Step 2: endpoints available?
            ep_resp = await client.get(f"{base_url}/api/endpoints")
            if ep_resp.status_code != 200:
                _emit_failure(org.id, "endpoints_unreachable")
                return ChatHealthOut(healthy=False, reason="endpoints_unreachable")

            endpoints = ep_resp.json()
            if not endpoints or (isinstance(endpoints, dict) and not any(endpoints.values())):
                _emit_failure(org.id, "no_endpoints")
                return ChatHealthOut(healthy=False, reason="no_endpoints")

    except httpx.TimeoutException:
        _emit_failure(org.id, "timeout")
        return ChatHealthOut(healthy=False, reason="timeout")
    except httpx.ConnectError:
        _emit_failure(org.id, "container_unreachable")
        return ChatHealthOut(healthy=False, reason="container_unreachable")
    except Exception as exc:
        logger.warning("chat-health probe unexpected error for %s: %s", org.librechat_container, exc)
        _emit_failure(org.id, "probe_error")
        return ChatHealthOut(healthy=False, reason="probe_error")

    return ChatHealthOut(healthy=True)


def _emit_failure(org_id: int, reason: str) -> None:
    """Emit a product event so chat failures show up in Grafana dashboards."""
    logger.warning("chat-health failed: org_id=%d reason=%s", org_id, reason)
    emit_event("chat.health_failed", org_id=org_id, properties={"reason": reason})
