"""Client for calling klai-docs internal API."""

import logging

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.trace import get_trace_headers

logger = logging.getLogger(__name__)


def _docs_headers() -> dict[str, str]:
    """Return internal service headers for docs-app calls.

    Strips X-Org-ID from trace headers: portal sends the numeric DB org_id,
    but docs-app compares it against zitadel_org_id → always 403.
    X-Internal-Secret already authenticates the caller; the org check is redundant.
    """
    return {k: v for k, v in get_trace_headers().items() if k != "X-Org-ID"}


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
        base_url="http://docs-app:3010/docs",
        headers={
            "X-Internal-Secret": settings.docs_internal_secret,
            "X-User-ID": "system",
            "Content-Type": "application/json",
            **_docs_headers(),
        },
        timeout=10.0,
    ) as client:
        # docs-app only accepts "public"/"private"; portal uses "internal" for private
        docs_visibility = "public" if visibility == "public" else "private"
        resp = await client.post(
            f"/api/orgs/{org_slug}/kbs",
            json={"name": kb_name, "slug": kb_slug, "visibility": docs_visibility},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["slug"]


async def get_page_count(org_slug: str, kb_slug: str) -> int | None:
    """Return the number of published docs pages for a KB, or None on error."""
    async with httpx.AsyncClient(
        base_url="http://docs-app:3010/docs",
        headers={
            "X-Internal-Secret": settings.docs_internal_secret,
            "X-User-ID": "system",
            **_docs_headers(),
        },
        timeout=5.0,
    ) as client:
        resp = await client.get(f"/api/orgs/{org_slug}/kbs/{kb_slug}/page-count")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("count")
        return None


async def deprovision_kb(org_slug: str, kb_slug: str) -> None:
    """Deprovision KB via klai-docs: deletes docs.knowledge_bases row, Gitea repo, and Qdrant vectors.

    Raises HTTPException(502) on failure.
    """
    async with httpx.AsyncClient(
        base_url="http://docs-app:3010/docs",
        headers={
            "X-Internal-Secret": settings.docs_internal_secret,
            "X-User-ID": "system",
            **_docs_headers(),
        },
        timeout=30.0,  # longer timeout: Gitea + Qdrant cleanup
    ) as client:
        resp = await client.delete(f"/api/orgs/{org_slug}/kbs/{kb_slug}")
        if resp.status_code == 404:
            # Already gone -- treat as success
            return
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "KB deprovisioning failed for org=%s kb=%s: %s %s",
                org_slug,
                kb_slug,
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Docs/Gitea cleanup failed",
            ) from exc
        except httpx.ConnectError as exc:
            logger.exception("KB deprovisioning connect error for org=%s kb=%s", org_slug, kb_slug)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Docs/Gitea cleanup failed",
            ) from exc


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
        logger.exception(
            "Gitea provisioning failed for KB slug=%s: %s %s",
            kb_slug,
            exc.response.status_code,
            exc.response.text[:500],
        )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gitea provisioning mislukt",
        ) from exc
    except httpx.ConnectError as exc:
        logger.exception("Gitea provisioning connect error for KB slug=%s: %s", kb_slug, exc)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gitea provisioning mislukt",
        ) from exc
