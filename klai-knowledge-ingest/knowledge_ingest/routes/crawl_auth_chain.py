"""Detect which other hosts a site's sign-in passes through.

A site behind a login often hands the browser to a second host (SSO/OAuth):
``/login`` answers 302 to ``https://idp.example.com/oauth/authorize`` which
answers 302 back. The wizard needs those hosts at setup time, because the
session lives in cookies for both of them and each host's cookies must be
entered separately.
"""

from urllib.parse import urljoin, urlsplit

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from knowledge_ingest.identity import assert_caller_identity_tenant_only
from knowledge_ingest.routes.crawl import _probe_fetch
from knowledge_ingest.utils.url_validator import validate_url_pinned

logger = structlog.get_logger()
router = APIRouter()

# Browsers stop at 20; a sign-in handoff is two or three hops, so five is
# generous without letting a redirect loop hold the request open.
_MAX_REDIRECTS = 5


def _loggable(url: str) -> str:
    """``url`` without its query string or fragment: an identity-provider hop
    carries ``state`` and a callback carries the authorization ``code``."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{parts.path}"


class AuthChainRequest(BaseModel):
    url: str
    org_id: str = ""


class AuthChainResponse(BaseModel):
    hosts: list[str]


async def _hosts_on_path(start: str) -> list[str]:
    """Hosts a cookie-less GET of ``start`` is redirected through, in order.

    Every hop is SSRF-validated and pinned before it is fetched. A hop that
    fails validation or the fetch ends the walk: a host we could not verify is
    never reported, since the wizard would then ask for cookies for it and the
    keep-alive would later fetch it.
    """
    hosts: list[str] = []
    url = start
    for _ in range(_MAX_REDIRECTS + 1):
        if urlsplit(url).scheme != "https":
            break
        try:
            validated = await validate_url_pinned(url)
            response = await _probe_fetch(url, pin_map={validated.hostname: validated.preferred_ip})
        except Exception as exc:
            logger.info("auth_chain_hop_stopped", url=_loggable(url), error=str(exc))
            break
        hosts.append(validated.hostname.lower())
        if not response.location or response.status_code not in (301, 302, 303, 307, 308):
            break
        url = urljoin(url, response.location)
    return hosts


@router.post("/ingest/v1/crawl/auth-chain", response_model=AuthChainResponse)
async def auth_chain(body: AuthChainRequest, request: Request) -> AuthChainResponse:
    """Other hosts reached while signing in to the site at ``body.url``.

    Tries the base URL itself (a protected site often redirects straight to
    its identity provider) and the ``/login`` page, both without cookies.
    """
    if body.org_id:
        await assert_caller_identity_tenant_only(request, claimed_org_id=body.org_id)

    parts = urlsplit(body.url)
    if parts.scheme != "https" or not parts.hostname:
        raise HTTPException(status_code=400, detail="url must be an https URL")
    base_host = parts.hostname.lower()

    seen: list[str] = []
    for start in (body.url, f"https://{parts.netloc}/login"):
        for host in await _hosts_on_path(start):
            if host != base_host and host not in seen:
                seen.append(host)
    logger.info("auth_chain_detected", url=_loggable(body.url), hosts=seen)
    return AuthChainResponse(hosts=seen)
