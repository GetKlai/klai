"""
GET /health

Returns scribe-api health and whisper-server reachability.
No authentication required. Used by Docker healthcheck and Uptime Kuma.
"""
import logging

import httpx
from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    whisper_status = "ok"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.whisper_server_url}/health")
            if resp.status_code != 200:
                whisper_status = "degraded"
    except Exception as exc:
        logger.warning("whisper-server health check failed: %s", exc)
        whisper_status = "degraded"

    return {"status": "ok", "whisper_server": whisper_status}
