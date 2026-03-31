import logging

import httpx
from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


async def _check(url: str, timeout: float = 5.0) -> str:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return "ok" if resp.status_code < 500 else "degraded"
    except Exception:
        return "degraded"


@router.get("/health")
async def health() -> dict:
    docling_status = await _check(f"{settings.docling_url}/health")
    infinity_status = await _check(f"{settings.infinity_url}/health")
    searxng_status = await _check(f"{settings.searxng_url}/")

    return {
        "status": "ok",
        "docling": docling_status,
        "infinity": infinity_status,
        "vector_backend": settings.vector_backend,
        "searxng": searxng_status,
    }
