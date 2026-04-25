"""
GET /health

Returns scribe-api health and whisper-server reachability.
No authentication required. Used by Docker healthcheck and Uptime Kuma.

SPEC-SEC-HYGIENE-001 REQ-37.2 — error responses MUST NOT leak the internal
whisper URL or exception detail. Generic 503 + opaque body; full traceback
goes to structlog only.
"""
from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.whisper_server_url}/health")
    except httpx.ConnectError:
        # Generic body — never echo the URL. Full exception in structlog.
        logger.warning("whisper_health_connect_error", exc_info=True)
        return JSONResponse(
            {"status": "error", "detail": "whisper unreachable"},
            status_code=503,
        )
    except Exception:
        # Defense-in-depth: any other exception sanitised to the same body.
        logger.warning("whisper_health_unexpected_error", exc_info=True)
        return JSONResponse(
            {"status": "error", "detail": "whisper unreachable"},
            status_code=503,
        )

    if resp.status_code == 200:
        return JSONResponse({"status": "ok", "whisper_server": "ok"})

    logger.warning("whisper_health_non_200", status_code=resp.status_code)
    return JSONResponse(
        {"status": "degraded", "detail": "whisper unreachable"},
        status_code=503,
    )
