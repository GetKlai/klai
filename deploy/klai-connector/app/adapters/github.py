"""GitHub App connector adapter using gidgethub + PyJWT."""

import asyncio
import base64
import fnmatch
import time
from pathlib import PurePosixPath
from typing import Any

import httpx
import jwt
from gidgethub import BadRequest
from gidgethub.httpx import GitHubAPI

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Maximum file size: 50 MB
_MAX_FILE_SIZE = 50 * 1024 * 1024

# Safety buffer before token expiry (seconds)
_TOKEN_EXPIRY_BUFFER = 60


class GitHubAdapter(BaseAdapter):
    """GitHub App connector using the Git Trees API for file discovery.

    Authenticates via a GitHub App JWT exchanged for an installation access token.
    Supports incremental sync by comparing tree SHAs between runs.

    Uses a persistent httpx.AsyncClient for connection reuse across API calls.
    Caches installation access tokens until 60 s before expiry.
    """

    SUPPORTED_TYPES: frozenset[str] = frozenset({".md", ".txt", ".pdf", ".docx", ".rst", ".html", ".csv"})

    def __init__(self, settings: Settings) -> None:
        self._app_id = settings.github_app_id
        self._private_key = settings.github_app_private_key
        self._http_client = httpx.AsyncClient(http2=True, timeout=30.0)
        # Cache: installation_id -> (token, expires_at_monotonic)
        self._token_cache: dict[int, tuple[str, float]] = {}

    async def aclose(self) -> None:
        """Close the persistent HTTP client. Called from app lifespan shutdown."""
        await self._http_client.aclose()

    # -- Authentication -------------------------------------------------------

    def _make_app_jwt(self) -> str:
        """Generate a GitHub App JWT valid for 10 minutes."""
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def _get_installation_token(self, installation_id: int) -> str:
        """Return a valid installation access token, using the cache if possible.

        GitHub installation tokens are valid for 1 hour. The cache returns
        the existing token until 60 s before expiry, then fetches a new one.

        Args:
            installation_id: GitHub App installation ID.
        """
        cached = self._token_cache.get(installation_id)
        if cached is not None:
            token, expires_at = cached
            if time.monotonic() < expires_at - _TOKEN_EXPIRY_BUFFER:
                return token

        app_jwt = self._make_app_jwt()
        gh = GitHubAPI(self._http_client, "klai-connector", oauth_token=app_jwt)
        response = await gh.post(
            "/app/installations/{installation_id}/access_tokens",
            url_vars={"installation_id": str(installation_id)},
            data={},
        )
        token = response["token"]  # type: ignore[index]

        # Parse expiry from response ("expires_at": "2026-03-22T11:00:00Z")
        # Store as monotonic timestamp for expiry checking
        expires_at = time.monotonic() + 3600  # fallback: 1 hour
        if "expires_at" in response:
            try:
                from datetime import UTC, datetime
                dt = datetime.fromisoformat(str(response["expires_at"]).replace("Z", "+00:00"))
                wall_remaining = (dt - datetime.now(UTC)).total_seconds()
                expires_at = time.monotonic() + wall_remaining
            except (ValueError, TypeError):
                pass

        self._token_cache[installation_id] = (token, expires_at)
        return token  # type: ignore[return-value]

    # -- BaseAdapter interface ------------------------------------------------

    async def list_documents(self, connector: Any) -> list[DocumentRef]:
        """List all supported files in the repository.

        Applies the optional ``path_filter`` glob pattern from the connector config.

        Args:
            connector: :class:`Connector` model instance.
        """
        config: dict[str, Any] = connector.config
        installation_id: int = config["installation_id"]
        repo_owner: str = config["repo_owner"]
        repo_name: str = config["repo_name"]
        branch: str = config.get("branch", "main")
        path_filter: str | None = config.get("path_filter")

        token = await self._get_installation_token(installation_id)
        gh = GitHubAPI(self._http_client, "klai-connector", oauth_token=token)
        await self._check_rate_limit(gh)
        tree = await gh.getitem(
            "/repos/{owner}/{repo}/git/trees/{branch}",
            url_vars={
                "owner": repo_owner,
                "repo": repo_name,
                "branch": branch,
            },
            url_vars_extra={"recursive": "1"},  # type: ignore[arg-type]
        )

        refs: list[DocumentRef] = []
        for item in tree.get("tree", []):
            if item.get("type") != "blob":
                continue
            path = item["path"]
            ext = PurePosixPath(path).suffix.lower()
            if ext not in self.SUPPORTED_TYPES:
                continue
            if path_filter and not fnmatch.fnmatch(path, path_filter):
                continue
            refs.append(
                DocumentRef(
                    path=path,
                    ref=item["sha"],
                    size=item.get("size", 0),
                    content_type=ext,
                )
            )

        logger.info(
            "Listed %d documents from %s/%s@%s",
            len(refs), repo_owner, repo_name, branch,
        )
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download file content via the GitHub blob API.

        Args:
            ref: Document reference with SHA and path.
            connector: Connector model (provides auth context).

        Raises:
            ValueError: If the file exceeds 50 MB.
        """
        if ref.size > _MAX_FILE_SIZE:
            raise ValueError(f"File too large: {ref.size} bytes (max 50 MB)")

        config: dict[str, Any] = connector.config
        installation_id: int = config["installation_id"]
        repo_owner: str = config["repo_owner"]
        repo_name: str = config["repo_name"]

        token = await self._get_installation_token(installation_id)
        gh = GitHubAPI(self._http_client, "klai-connector", oauth_token=token)
        await self._check_rate_limit(gh)
        blob = await gh.getitem(
            "/repos/{owner}/{repo}/git/blobs/{sha}",
            url_vars={
                "owner": repo_owner,
                "repo": repo_name,
                "sha": ref.ref,
            },
        )

        content: str = blob.get("content", "")
        encoding: str = blob.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content)
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the current tree SHA for incremental sync.

        Args:
            connector: Connector model instance.

        Returns:
            Dictionary with ``tree_sha`` key.
        """
        config: dict[str, Any] = connector.config
        installation_id: int = config["installation_id"]
        repo_owner: str = config["repo_owner"]
        repo_name: str = config["repo_name"]
        branch: str = config.get("branch", "main")

        token = await self._get_installation_token(installation_id)
        gh = GitHubAPI(self._http_client, "klai-connector", oauth_token=token)
        ref_data = await gh.getitem(
            "/repos/{owner}/{repo}/git/refs/heads/{branch}",
            url_vars={
                "owner": repo_owner,
                "repo": repo_name,
                "branch": branch,
            },
        )
        return {"tree_sha": ref_data["object"]["sha"]}

    # -- Rate limiting --------------------------------------------------------

    @staticmethod
    async def _check_rate_limit(gh: GitHubAPI) -> None:
        """Back off if the GitHub API rate limit is critically low.

        Waits proportionally to the deficit below 100 remaining requests,
        capped at 60 seconds.
        """
        rate_limit = getattr(gh, "rate_limit", None)
        if rate_limit is not None:
            remaining = rate_limit.remaining
            if remaining is not None and remaining < 100:
                wait_time = min(60, max(5, (100 - remaining) * 0.5))
                logger.warning(
                    "GitHub rate limit low (%d remaining), backing off %.1fs",
                    remaining, wait_time,
                )
                await asyncio.sleep(wait_time)
