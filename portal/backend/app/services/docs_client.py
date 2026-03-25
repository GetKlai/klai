"""Client for calling klai-docs internal API."""

import logging

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

log = logging.getLogger(__name__)


async def provision_gitea_repo(
    org_slug: str,
    kb_name: str,
    kb_slug: str,
    visibility: str,
) -> str:
    """Provision a Gitea repo via klai-docs.

    Returns gitea_repo_slug.
    Raises httpx.HTTPStatusError on failure.
    """
    async with httpx.AsyncClient(
        base_url="http://docs-app:3000",
        headers={
            "X-Internal-Secret": settings.docs_internal_secret,
            "X-User-ID": "system",
            "Content-Type": "application/json",
        },
        timeout=10.0,
    ) as client:
        resp = await client.post(
            f"/api/orgs/{org_slug}/kbs",
            json={"name": kb_name, "slug": kb_slug, "visibility": visibility},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["slug"]


async def get_page_count(org_slug: str, kb_slug: str) -> int | None:
    """Return the number of published docs pages for a KB, or None on error."""
    async with httpx.AsyncClient(
        base_url="http://docs-app:3000",
        headers={
            "X-Internal-Secret": settings.docs_internal_secret,
            "X-User-ID": "system",
        },
        timeout=5.0,
    ) as client:
        resp = await client.get(f"/api/orgs/{org_slug}/kbs/{kb_slug}/page-count")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("count")
        return None


async def provision_and_store(
    org_slug: str,
    kb_name: str,
    kb_slug: str,
    visibility: str,
    db: AsyncSession,
) -> str:
    """Provision Gitea repo and store the slug. Rolls back db on failure.

    Callers must have already flushed the KB row (so it exists in the session
    but is not committed). On success, the caller is responsible for committing.
    On failure, the session is rolled back and HTTPException(502) is raised.
    """
    try:
        return await provision_gitea_repo(org_slug, kb_name, kb_slug, visibility)
    except httpx.HTTPStatusError as exc:
        log.exception("Gitea provisioning failed for KB slug=%s", kb_slug)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gitea provisioning mislukt",
        ) from exc
