"""Health check endpoint for klai-connector."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    """Return service health status, version, and supported connector types."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "connector_types": ["github"],
    }
