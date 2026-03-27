"""
GitHub API client for org membership management.
Used during offboarding to remove users from the GitHub organisation.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


async def remove_github_org_member(github_username: str) -> bool:
    """
    Remove a user from the GitHub organisation.

    Returns True on success, False on failure.
    Logs a warning on failure — never raises; offboarding must not be blocked.
    """
    if not settings.github_admin_pat:
        logger.warning(
            "GitHub offboarding skipped for %s: GITHUB_ADMIN_PAT not configured",
            github_username,
        )
        return False

    url = f"{_GITHUB_API}/orgs/{settings.github_org}/members/{github_username}"
    try:
        async with httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {settings.github_admin_pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        ) as client:
            response = await client.delete(url)
            if response.status_code == 204:
                logger.info(
                    "GitHub org member removed: %s from %s",
                    github_username,
                    settings.github_org,
                )
                return True
            elif response.status_code == 404:
                logger.info(
                    "GitHub org member not found (already removed?): %s",
                    github_username,
                )
                return True  # idempotent — already gone
            else:
                logger.warning(
                    "GitHub org member removal failed for %s: HTTP %d",
                    github_username,
                    response.status_code,
                )
                return False
    except Exception as exc:
        logger.warning(
            "GitHub org member removal failed for %s: %s",
            github_username,
            exc,
        )
        return False
