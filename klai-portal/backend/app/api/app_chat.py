"""App-facing chat health API.

GET /api/app/chat-health — pre-flight probe of the tenant's LibreChat container.

Called by the frontend chat page to detect broken iframe state before/during load.
Returns a structured health status so the UI can show actionable feedback instead of
silently hanging on "Welcome back" forever.

The probe uses only public, unauthenticated LibreChat endpoints (/health and
/api/config) — /api/endpoints requires auth since v0.8.5 and is therefore unusable
as a liveness signal from portal-api.
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
    """Probe the tenant's LibreChat instance for pre-flight availability.

    Checks:
    1. Org is provisioned (has a LibreChat container)
    2. LibreChat container responds on /health (liveness)
    3. LibreChat serves /api/config (client bootstrap config, unauthenticated)

    /api/config is the same endpoint LibreChat's own web client calls before
    login; a 200 there means the app is fully booted and ready to serve the
    iframe. We do NOT probe /api/endpoints — since LibreChat v0.8.5 that
    endpoint requires auth and can't be used as an anonymous liveness check.

    Returns healthy=false with a machine-readable reason so the frontend
    can show specific feedback (not provisioned / container down / not ready).
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

            # Step 2: app bootstrapped? /api/config is public by design
            # (LibreChat's own web client hits it before login).
            cfg_resp = await client.get(f"{base_url}/api/config")
            if cfg_resp.status_code != 200:
                _emit_failure(org.id, "app_not_ready")
                return ChatHealthOut(healthy=False, reason="app_not_ready")

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
