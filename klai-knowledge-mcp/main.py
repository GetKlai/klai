"""
klai-knowledge-mcp
MCP server that lets LibreChat agents save content to:
  - personal knowledge base  (via knowledge-ingest API)
  - organisation knowledge base  (via knowledge-ingest API)
  - documentation KB in Klai Docs  (via Gitea-backed klai-docs API)

Transport: streamable-http (LibreChat v0.8.4+)
Auth:      service token forwarded to klai-docs as X-Internal-Secret
Identity:  X-User-ID, X-Org-ID, X-Org-Slug, Authorization: Bearer <user_jwt>
           injected by LibreChat config; the (X-User-ID, X-Org-ID, X-Org-Slug)
           tuple is *claimed* and MUST be cross-checked against portal-api's
           /internal/identity/verify before any upstream call. Verified
           values flow downstream — caller-asserted values never do.
           See SPEC-SEC-IDENTITY-ASSERT-001 REQ-2.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

import httpx
from klai_identity_assert import (
    IdentityAsserter,
    McpTokenAsserter,
    VerifyResult,
)
from klai_retrieval_telemetry import (
    classify_gap as _classify_gap,
)
from klai_retrieval_telemetry import (
    fire_gap_event,
    fire_retrieval_log,
)
from log_utils import sanitize_response_body, verify_shared_secret
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from logging_setup import setup_logging

setup_logging()

logger = logging.getLogger(__name__)

# -- Config -------------------------------------------------------------------
KLAI_DOCS_API_BASE = os.environ["KLAI_DOCS_API_BASE"]  # http://docs-app:3000
DOCS_INTERNAL_SECRET = os.environ["DOCS_INTERNAL_SECRET"]
KNOWLEDGE_INGEST_URL = os.environ["KNOWLEDGE_INGEST_URL"]  # http://knowledge-ingest:8000
# SPEC-MCP-RETRIEVAL-001: retrieval-api endpoint for the search_knowledge tool.
# Default targets the Docker-internal hostname; SOPS sets this in production.
KNOWLEDGE_RETRIEVE_URL = os.environ.get(
    "KNOWLEDGE_RETRIEVE_URL", "http://retrieval-api:8040/retrieve"
)
# SPEC-MCP-RETRIEVAL-001: secret for the X-Internal-Secret header on outbound
# /retrieve calls. retrieval-api validates against its own INTERNAL_SECRET env
# (mapped from RETRIEVAL_API_INTERNAL_SECRET in SOPS) — DIFFERENT from
# KNOWLEDGE_INGEST_SECRET (knowledge-ingest service) and PORTAL_INTERNAL_SECRET
# (portal-api). Using the wrong one yields HTTP 401 invalid_internal_secret.
# When unset (older deploys), fall back to PORTAL_INTERNAL_SECRET so the
# header still ships — same fallback the LiteLLM hook uses.
RETRIEVAL_INTERNAL_SECRET = os.environ.get("RETRIEVAL_INTERNAL_SECRET") or os.environ.get(
    "PORTAL_INTERNAL_SECRET", ""
)
# SPEC-SEC-INTERNAL-001 REQ-9.5: KNOWLEDGE_INGEST_SECRET is now mandatory.
# Empty / missing causes module-load failure rather than silently omitting
# the X-Internal-Secret header on outbound calls (the previous "gradual
# rollout" path that turned authenticated traffic into unauthenticated).
KNOWLEDGE_INGEST_SECRET = os.environ["KNOWLEDGE_INGEST_SECRET"]
# SPEC-SEC-IDENTITY-ASSERT-001 REQ-2: portal-api /internal/identity/verify
# coordinates. Both required at startup -- fail-closed if missing.
PORTAL_API_URL = os.environ["PORTAL_API_URL"]
PORTAL_INTERNAL_SECRET = os.environ["PORTAL_INTERNAL_SECRET"]
# SPEC-MCP-AUTH-001 REQ-8 + REQ-14: canonical resource URI for RFC 8707
# audience binding. Tokens issued for any other resource are rejected by
# portal-api; the PRM-endpoint advertises this URI back to clients.
# Kept optional with a sensible default so existing LibreChat-only deploys
# don't crash if the env-var hasn't been added to SOPS yet.
MCP_OAUTH_RESOURCE_URL = os.environ.get("MCP_OAUTH_RESOURCE_URL", "https://mcp.getklai.com")
# SPEC-MCP-AUTH-001: portal-api OAuth issuer base URL — appears in PRM as
# the authorization-server location (RFC 9728).
MCP_OAUTH_ISSUER_BASE_URL = os.environ.get("MCP_OAUTH_ISSUER_BASE_URL", "https://my.getklai.com")

# SPEC-SEC-INTERNAL-001 REQ-9.5: enforce non-empty values. ``os.environ[...]``
# above raises KeyError on missing; the assertions below close the
# empty-string hole. Module fails to import (process exits non-zero) when
# any required secret is the empty string.
_REQ95_HINT = "must be a non-empty string (SPEC-SEC-INTERNAL-001 REQ-9.5)"
if not DOCS_INTERNAL_SECRET:
    raise RuntimeError(f"DOCS_INTERNAL_SECRET {_REQ95_HINT}")
if not KNOWLEDGE_INGEST_SECRET:
    raise RuntimeError(f"KNOWLEDGE_INGEST_SECRET {_REQ95_HINT}")
if not PORTAL_INTERNAL_SECRET:
    raise RuntimeError(f"PORTAL_INTERNAL_SECRET {_REQ95_HINT}")

_INTERNAL_SECRET_HEADER = "X-Internal-Secret"

# SPEC-SEC-INTERNAL-001 REQ-4: secret values to scrub from any upstream
# response body before logging. Built once at import time -- the values
# come from os.environ above which is already frozen by the time any
# request fires. Values shorter than 8 chars are skipped to mirror the
# library guard (avoid over-redaction of common substrings).
_KNOWN_SECRETS: frozenset[str] = frozenset(
    s
    for s in (DOCS_INTERNAL_SECRET, KNOWLEDGE_INGEST_SECRET, PORTAL_INTERNAL_SECRET)
    if len(s) >= 8
)

# Path validation patterns
_KB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# SPEC-TAXONOMY-001 (revised): vocabulary matches the live DB constraint
# (artifacts_assertion_mode_check) and klai-knowledge-ingest. ``unknown`` is
# the system default for content with no epistemic classification — keeps
# untagged content out of the (future) feit-boost in retrieval scoring
# (DD-2 in spec.md). See the Realignment Note in
# .moai/specs/SPEC-TAXONOMY-001/spec.md.
AssertionMode = Literal["factual", "belief", "hypothesis", "procedural", "quoted", "unknown"]
VALID_ASSERTION_MODES: frozenset[str] = frozenset(get_args(AssertionMode))

# Error messages (bilingual NL/EN)
_ERR_SAVE = (
    "Er is een fout opgetreden bij het opslaan. Probeer het opnieuw.\n"
    "(An error occurred while saving. Please try again.)"
)
_ERR_IDENTITY_REJECTED = (
    "Toegang geweigerd. Controleer of je de juiste organisatie en gebruiker bent.\n"
    "(Identity verification failed. The reason code is in the server logs — "
    "the MCP intentionally does not echo it to the client.)"
)
_ERR_ASSERTION_MODE = (
    "Error: invalid assertion_mode '{}'. "
    f"Valid values: {', '.join(sorted(VALID_ASSERTION_MODES))}"
)
# SPEC-MCP-RETRIEVAL-001 REQ-17/REQ-18: bilingual generic for retrieval failures.
# Status codes + correlation context land in structlog; the user-facing string
# stays generic so the MCP host (Claude Desktop) does not surface internal
# upstream details to the LLM.
_ERR_KB_UNAVAILABLE = (
    "Kennisbank tijdelijk niet bereikbaar. Probeer het later opnieuw.\n"
    "(Knowledge base unavailable. Please try again.)"
)


# -- Identity -----------------------------------------------------------------
# SEC: asserted — values lifted from caller-controlled headers; MUST be
# verified via portal-api /internal/identity/verify before any upstream use
# (REQ-2.4). Storing this as a distinct type from VerifyResult keeps the
# claimed-vs-verified distinction visible in every call site.
@dataclass(frozen=True, slots=True)
class _ClaimedIdentity:
    user_id: str
    org_id: str
    org_slug: str


def _request_headers(ctx: Context):
    """Return the headers from the FastMCP request context.

    FastMCP types ``request_context.request`` as Optional; in practice every
    tool invocation comes from an HTTP request, so ``None`` would indicate a
    programming error elsewhere. Fail loud rather than silently coerce —
    and centralise the guard so individual call sites stay readable.
    """
    request = ctx.request_context.request
    if request is None:
        raise RuntimeError("Tool invoked outside an HTTP request context")
    return request.headers


def _get_claimed_identity(ctx: Context) -> _ClaimedIdentity:
    """Extract the caller-asserted identity tuple from request headers.

    REQ-2.4: this returns a *claim*, never trusted on its own. Every call site
    pairs this with ``_verify_identity`` before forwarding upstream. The
    DEFAULT_ORG_SLUG fallback (REQ-2.6) has been removed — a missing
    X-Org-Slug header is now a hard error, no silent identity downgrade.

    Raises ValueError when any of the three required headers is absent.
    """
    headers = _request_headers(ctx)
    user_id = headers.get("x-user-id", "")
    org_id = headers.get("x-org-id", "")
    org_slug = headers.get("x-org-slug", "")

    if not user_id:
        raise ValueError(
            "X-User-ID header is missing. "
            "Ensure LibreChat forwards identity headers to this MCP server."
        )
    if not org_id:
        raise ValueError(
            "X-Org-ID header is missing. "
            "Ensure LibreChat forwards identity headers to this MCP server."
        )
    if not org_slug:
        raise ValueError(
            "X-Org-Slug header is missing. "
            "Ensure LibreChat forwards identity headers to this MCP server."
        )
    return _ClaimedIdentity(user_id=user_id, org_id=org_id, org_slug=org_slug)


def _extract_user_jwt(ctx: Context) -> str | None:
    """Pull the end-user Zitadel JWT from ``Authorization: Bearer <jwt>``.

    Returns ``None`` when the header is absent or malformed — the verify
    call then falls back to the membership path (REQ-2.5 without retry: a
    deny is a deny, no second call). The shared ``X-Internal-Secret`` is a
    different header on incoming traffic and is unrelated to this JWT.
    """
    headers = _request_headers(ctx)
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    return token or None


def _validate_incoming_secret(ctx: Context) -> None:
    """Validate X-Internal-Secret on incoming MCP requests.

    SPEC-SEC-INTERNAL-001 REQ-1.5 / REQ-1.6: comparison is constant-time via
    ``log_utils.verify_shared_secret``. REQ-9.5: KNOWLEDGE_INGEST_SECRET is
    enforced at import time to be non-empty, so the legacy
    ``if not KNOWLEDGE_INGEST_SECRET: return`` "gradual rollout" branch is
    gone -- every incoming MCP request MUST carry a valid header.

    Raises ValueError when the header is missing or does not match.
    """
    headers = _request_headers(ctx)
    provided = headers.get(_INTERNAL_SECRET_HEADER.lower(), "")
    if not verify_shared_secret(provided, KNOWLEDGE_INGEST_SECRET):
        raise ValueError("Invalid or missing X-Internal-Secret header.")


# -- Identity verification ---------------------------------------------------
# Module-level singletons: each Asserter pools an httpx.AsyncClient and
# carries a per-process LRU cache, so they MUST be reused across tool calls.
_asserter = IdentityAsserter(
    portal_base_url=PORTAL_API_URL,
    internal_secret=PORTAL_INTERNAL_SECRET,
)
# SPEC-MCP-AUTH-001 Fase 3 — sibling asserter for the OAuth-token pad. Same
# httpx pool semantics as IdentityAsserter; different portal endpoint
# (/internal/mcp-token/verify) and different request shape (raw_token).
_mcp_token_asserter = McpTokenAsserter(
    portal_base_url=PORTAL_API_URL,
    internal_secret=PORTAL_INTERNAL_SECRET,
    caller_service="knowledge-mcp",
)


# SPEC-MCP-AUTH-001 REQ-12: unified verified-identity shape for both auth
# paths. Tools never branch on which pad provided the identity — they just
# receive a frozen ``_VerifiedIdentity`` and forward upstream.
#
# SPEC-MCP-RETRIEVAL-001 REQ-3: ``client_id`` carries the OAuth client
# attribution for telemetry labelling. ``None`` on the LibreChat path
# (existing tools never read it); set on the OAuth path so the upcoming
# ``search_knowledge`` tool can label retrieval-log + gap-event entries.
@dataclass(frozen=True, slots=True)
class _VerifiedIdentity:
    user_id: str
    org_id: str
    org_slug: str
    client_id: str | None = None


async def _verify_identity(ctx: Context, claimed: _ClaimedIdentity) -> VerifyResult:
    """Call portal-api /internal/identity/verify for the claimed tuple.

    Forwards the end-user JWT (when present) so verify can do the strong
    sub/resourceowner check. When no JWT is forwarded, the portal falls back
    to a membership lookup. Either path proves the claimed user genuinely
    belongs to the claimed org — the only way to neutralise the M1 + D1
    chain in spec.md.
    """
    bearer_jwt = _extract_user_jwt(ctx)
    request_headers = dict(_request_headers(ctx))
    return await _asserter.verify(
        caller_service="knowledge-mcp",
        claimed_user_id=claimed.user_id,
        claimed_org_id=claimed.org_id,
        bearer_jwt=bearer_jwt,
        claimed_org_slug=claimed.org_slug,
        request_headers=request_headers,
    )


def _log_identity_deny(claimed: _ClaimedIdentity, result: VerifyResult) -> None:
    """Server-side log of why a call was rejected.

    REQ-2.2: the reason code stays in logs only — never echoed to the MCP
    client (information-leak prevention).
    """
    logger.warning(
        "knowledge_mcp_identity_rejected: reason=%s claimed_user_id=%s "
        "claimed_org_id=%s claimed_org_slug=%s",
        result.reason,
        claimed.user_id,
        claimed.org_id,
        claimed.org_slug,
    )


# -- SPEC-MCP-AUTH-001 dispatcher ---------------------------------------------
# Tools call ``_identify_request(ctx)``. The dispatcher branches on the
# Authorization header:
#
#   - ``Authorization: Bearer klai_mcp_<...>``  (NOT klai_mcp_rt_)
#         → OAuth-token pad: portal /internal/mcp-token/verify lookup
#   - anything else (including absent / Zitadel-JWT / X-Internal-Secret)
#         → existing LibreChat pad (X-Internal-Secret + claimed-identity
#           headers + portal /internal/identity/verify)
#
# Both paths converge on a frozen ``_VerifiedIdentity``. Tool bodies stay
# untouched — they receive the same shape regardless of which pad ran.

# Dispatcher primitives live in dispatcher.py so unit-tests can exercise
# them without pulling in FastMCP / klai-libs heavy imports. The prefixes
# below are part of the cross-service contract with
# ``app.services.mcp_oauth.ACCESS_TOKEN_PREFIX`` in portal-api.
from dispatcher import (  # noqa: E402 — module-level after env validation by design
    OAUTH_ACCESS_PREFIX as _OAUTH_ACCESS_PREFIX,
)
from dispatcher import (  # noqa: E402
    OAUTH_REFRESH_PREFIX as _OAUTH_REFRESH_PREFIX,
)
from dispatcher import (  # noqa: E402
    looks_like_oauth_access_token as _looks_like_oauth_access_token,
)

# Quiet "imported but unused" — these are re-exported as module-level
# attributes for any test or downstream code that references them via
# the ``_-prefixed`` aliases.
_ = (_OAUTH_ACCESS_PREFIX, _OAUTH_REFRESH_PREFIX, _looks_like_oauth_access_token)


class _IdentificationFailed(RuntimeError):
    """Raised when neither auth pad produced a verified identity.

    The string carrier is intentionally a generic NL/EN message — reason
    codes stay in logs (info-leak prevention). Specific tools convert this
    into the bilingual user-facing error.
    """


async def _identify_via_oauth_token(ctx: Context, raw_token: str) -> _VerifiedIdentity:
    """Verify a klai_mcp_<...> bearer token against portal-api.

    Audience-binding: portal-api validates the token's resource_uri matches
    the canonical ``MCP_OAUTH_RESOURCE_URL``. We propagate that as a header
    only for trace-correlation; the actual binding happens server-side in
    the verify endpoint.
    """
    request_headers = dict(_request_headers(ctx))
    result = await _mcp_token_asserter.verify(
        raw_token=raw_token,
        request_headers=request_headers,
    )
    if not result.verified:
        # ``result.reason`` is an enum-string from a fixed allowlist
        # (invalid_format, unknown_token, token_revoked, ...). Never a credential.
        # Event name avoids the literal "token"/"credential" substring so
        # semgrep's logger-credential-leak rule does not false-positive.
        logger.warning("knowledge_mcp_oauth_assertion_denied: reason=%s", result.reason)
        raise _IdentificationFailed(_ERR_IDENTITY_REJECTED)
    assert result.user_id is not None
    assert result.org_id is not None
    assert result.org_slug is not None
    return _VerifiedIdentity(
        user_id=result.user_id,
        org_id=result.org_id,
        org_slug=result.org_slug,
        # SPEC-MCP-RETRIEVAL-001 REQ-4: propagate OAuth client_id from the
        # mcp-token verify response. ``None`` if portal returns it null
        # (older portal builds may not include the field) — the tool that
        # consumes it must treat None as "no caller attribution".
        client_id=result.client_id,
    )


async def _identify_via_internal_secret(ctx: Context) -> _VerifiedIdentity:
    """Existing LibreChat pad: X-Internal-Secret + claimed identity headers.

    Behaviourally identical to the pre-SPEC-MCP-AUTH-001 flow — kept verbatim
    so LibreChat regression cannot drift. Wraps the three discrete steps
    (validate-secret + extract-claim + verify-claim) into one call.
    """
    _validate_incoming_secret(ctx)  # raises ValueError on miss/mismatch
    claimed = _get_claimed_identity(ctx)  # raises ValueError on missing headers
    verified = await _verify_identity(ctx, claimed)
    if not verified.verified:
        _log_identity_deny(claimed, verified)
        raise _IdentificationFailed(_ERR_IDENTITY_REJECTED)
    assert verified.user_id is not None
    assert verified.org_id is not None
    assert verified.org_slug is not None
    return _VerifiedIdentity(
        user_id=verified.user_id,
        org_id=verified.org_id,
        org_slug=verified.org_slug,
    )


async def _identify_request(ctx: Context) -> _VerifiedIdentity:
    """Single entry point used by every tool.

    Branch on the Authorization header. The OAuth-token pad is selected
    only when the token-prefix is exactly ``klai_mcp_`` (not ``klai_mcp_rt_``);
    every other shape (no auth header, Zitadel-JWT bearer, X-Internal-Secret
    only) falls through to the LibreChat pad.

    @MX:ANCHOR fan_in=high — called from save_personal_knowledge,
    save_org_knowledge, save_to_docs (and any future tool). Cross-service
    contract: the returned ``_VerifiedIdentity`` shape MUST stay aligned with
    ``IdentityAsserter.VerifyResult`` so downstream upstream-calls can use
    either path indistinguishably.
    @MX:REASON A regression here that misroutes klai_mcp_rt_ tokens to the
    OAuth pad would cause portal /internal/mcp-token/verify to return
    invalid_format and deny — but a regression that misroutes Zitadel-JWTs
    (LibreChat optional bearer) would silently break the LibreChat pad.
    @MX:SPEC SPEC-MCP-AUTH-001 REQ-15
    """
    headers = _request_headers(ctx)
    authorization = headers.get("authorization", "")
    if _looks_like_oauth_access_token(authorization):
        raw_token = authorization.split(" ", 1)[1].strip()
        return await _identify_via_oauth_token(ctx, raw_token)
    return await _identify_via_internal_secret(ctx)


# -- Helpers ------------------------------------------------------------------
async def _save_to_ingest(
    org_id: str,
    kb_slug: str,
    title: str,
    content: str,
    assertion_mode: str,
    tags: list[str],
    source_note: str | None,
    user_id: str | None = None,
) -> bool:
    """POST a document to the knowledge-ingest API.

    Returns True on success (2xx), False on any HTTP or network error.
    """
    payload: dict = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": title,
        "content": content.strip(),
        "metadata": {
            "tags": tags[:5],
            "assertion_mode": assertion_mode,
            **({"source_note": source_note} if source_note else {}),
        },
    }
    if user_id is not None:
        payload["user_id"] = user_id

    # SPEC-SEC-INTERNAL-001 REQ-9.5: header is unconditional. The startup
    # guard above ensures KNOWLEDGE_INGEST_SECRET is a non-empty string.
    headers: dict[str, str] = {_INTERNAL_SECRET_HEADER: KNOWLEDGE_INGEST_SECRET}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{KNOWLEDGE_INGEST_URL}/ingest/v1/document",
                json=payload,
                headers=headers,
            )
    except httpx.RequestError:
        return False

    return resp.status_code in (200, 201, 202)


def _validate_page_path(page_path: str) -> None:
    """Reject ``page_path`` shapes that try to escape the docs KB tree.

    Conservative-by-default rejection rules (SPEC-SEC-HYGIENE-001 REQ-46.1):

    1. literal ``..``, ``\\``, or leading ``/`` — the historic checks.
    2. any literal ``%`` character — catches every URL-encoded variant
       (``%2e%2e``, ``%2E%2E``, ``%2f``, ``%20``, etc.) without needing to
       enumerate them. The legitimate caller is LibreChat, which generates
       paths from prose; it has no reason to URL-encode.
    3. Unicode-NFKC normalisation: if the normalised form contains ``..``,
       ``\\``, or starts with ``/``, reject. Catches the FULLWIDTH FULL STOP
       (U+FF0E) and other compatibility-equivalent glyphs that fold to the
       same codepoints under NFKC.

    Out of scope (REQ-46.2 / REQ-46.3, deferred to follow-up SPEC):
    overlong UTF-8, IDN homoglyph permutations, and the klai-docs route-
    handler audit that determines downstream blast radius. This helper is a
    safe-by-default stub for the hygiene SPEC; the full encoding matrix
    lands in the split SPEC once the klai-docs audit completes.

    Raises ``ValueError`` on any rejected input. Returns ``None`` on accept.
    """
    if ".." in page_path or "\\" in page_path or page_path.startswith("/"):
        raise ValueError("page_path contains invalid path components")
    if "%" in page_path:
        raise ValueError("page_path contains URL-encoded characters; pass the decoded form")
    import unicodedata

    normalised = unicodedata.normalize("NFKC", page_path)
    if normalised != page_path and (
        ".." in normalised or "\\" in normalised or normalised.startswith("/")
    ):
        raise ValueError(
            "page_path contains compatibility-equivalent characters that decode to traversal"
        )


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = text.encode("ascii", "ignore").decode()  # strip accented chars
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:60] or "note"


# -- MCP server ---------------------------------------------------------------
# klai-security-audit 2026-05-07 finding B3 (CANNOT-VERIFY → reviewed
# 2026-05-07): FastMCP's Streamable HTTP transport binds session state to
# the server-generated ``Mcp-Session-Id`` (uuid4, 128-bit). Per-session
# state is transport-only (request streams, SSE writers); identity is
# re-derived from the ``Authorization`` header on every tool call (see
# ``_identify_request`` invocations in each tool body) and the
# ``_WWWAuthenticateMiddleware`` runs BEFORE FastMCP touches the request.
# Net: a hijacked session-id cannot read another user's data today.
#
# Latent caveat: the GET-SSE notification stream binds to session-id only.
# If anyone in the future wires up server-pushed notifications via that
# stream that carry identity-scoped payloads (org_id-specific events,
# per-user alerts, etc.), the emitter MUST re-derive identity from the
# live ``Authorization`` header on every emit — NOT from cached session
# state. Without that discipline the GEEL caveat in finding B3 turns
# into a real cross-token leak.
mcp = FastMCP(
    "klai-knowledge",
    transport_security=TransportSecuritySettings(
        # @MX:ANCHOR fan_in=1 — DNS-rebinding protection re-enabled by
        # SPEC-MCP-AUTH-001 Fase 5. The MCP is now internet-reachable via
        # Caddy at mcp.getklai.com; LibreChat still reaches it via the
        # Docker-internal hostname (which doesn't carry a Host header that
        # matches the allowlist, so it's exempt from DNS-rebinding checks
        # by virtue of internal-network bypass at the Caddy level).
        # @MX:REASON Without DNS-rebinding protection on a public MCP,
        # any attacker page can issue cross-origin requests and have them
        # accepted. The two-line lift here is the entire defense.
        # @MX:SPEC SPEC-MCP-AUTH-001 REQ-A6
        enable_dns_rebinding_protection=True,
        # Without an explicit allowlist FastMCP rejects every Host (HTTP 421
        # "Misdirected Request"). Public route comes in via Caddy with
        # Host: mcp.getklai.com. LibreChat still reaches the same container
        # via the Docker-internal hostname klai-knowledge-mcp:8080.
        allowed_hosts=[
            "mcp.getklai.com",
            "klai-knowledge-mcp",
            "klai-knowledge-mcp:8080",
            "localhost",
            "localhost:8080",
            "127.0.0.1",
            "127.0.0.1:8080",
        ],
    ),
    instructions=(
        "Je hebt toegang tot de kennisbank van de gebruiker en de organisatie.\n\n"
        "PERSOONLIJKE KENNISBANK (save_personal_knowledge):\n"
        "Trigger: 'sla dit op', 'onthoud dit', 'save this', 'note this',\n"
        "'bewaar dit voor mij', 'remember this for me'\n\n"
        "ORGANISATIE KENNISBANK (save_org_knowledge):\n"
        "Trigger: 'sla dit op voor het team', 'deel dit met de organisatie',\n"
        "'save this for the team', 'share with the org',\n"
        "'voeg toe aan de teamkennis', 'organisatiekennis'\n\n"
        "DOCUMENTATIE (save_to_docs):\n"
        "Trigger: 'voeg toe aan de docs', 'bewaar in documentatie',\n"
        "'add to docs', 'save to documentation'\n\n"
        "ONDUIDELIJK? Vraag: 'Wil je dit opslaan voor jezelf, "
        "of voor de hele organisatie?'\n\n"
        "You have access to the user's personal and organisation knowledge base.\n"
        "When scope is unclear, ask: 'Do you want to save this for yourself, "
        "or for the whole organisation?'"
    ),
)


# -- Tool: save_personal_knowledge --------------------------------------------
@mcp.tool(
    description="""Save content to the user's PERSONAL knowledge base.

WHEN TO CALL: user says "sla dit op", "onthoud dit", "save this", "note this",
"bewaar dit voor mij", "remember this for me", or expresses intent to keep
something for their own reference.

PARAMETERS:
  title          - short, descriptive title (max 80 chars); you generate this
  content        - the text to save; may be a summary, quote, or elaboration
  assertion_mode - pick the best fit: "factual", "belief", "hypothesis",
                   "procedural", "quoted", or "unknown" (use "unknown" if
                   the epistemic status is unclear; it is also the system
                   default for missing values)
  tags           - 1-5 tags; free-form or from seed list
  source_note    - (optional) source reference if mentioned by user
"""
)
async def save_personal_knowledge(
    title: str,
    content: str,
    assertion_mode: str,
    tags: list[str],
    ctx: Context,
    source_note: str | None = None,
) -> str:
    # SPEC-MCP-AUTH-001 REQ-12 + REQ-15: dispatcher selects OAuth-token pad
    # vs LibreChat internal-secret pad based on Authorization header prefix.
    try:
        verified = await _identify_request(ctx)
    except ValueError as exc:
        return f"Error: {exc}"
    except _IdentificationFailed as exc:
        return str(exc)

    if not assertion_mode:
        # SPEC-TAXONOMY-001 DD-2: missing → "unknown", not "factual".
        # Avoids an unwarranted feit-boost in (future) retrieval scoring.
        assertion_mode = "unknown"
    elif assertion_mode not in VALID_ASSERTION_MODES:
        return _ERR_ASSERTION_MODE.format(assertion_mode)

    assert verified.user_id is not None and verified.org_id is not None
    # @MX:NOTE: the personal-KB slug is derived deterministically from the
    # verified user_id (`f"personal-{...}"`). An attacker who learns a
    # victim's Zitadel sub can therefore reconstruct the slug. The structural
    # neutralisation — membership-check between user_id and the KB so that
    # knowing the slug is not enough — lives in SPEC-SEC-IDENTITY-ASSERT-001
    # (already shipped; see `_verify_identity` above). SPEC-SEC-HYGIENE-001
    # REQ-48 documents that the slug FORMAT stays unchanged on purpose:
    # rotating the derivation strategy would break every existing personal KB
    # without adding security beyond what IDENTITY-ASSERT-001 already provides.
    # If a future SPEC migrates to an opaque slug format, that SPEC owns the
    # data migration path — it is not in this codebase's scope.
    ok = await _save_to_ingest(
        org_id=verified.org_id,
        kb_slug=f"personal-{verified.user_id}",
        title=title,
        content=content,
        assertion_mode=assertion_mode,
        tags=tags,
        source_note=source_note,
        user_id=verified.user_id,
    )
    if not ok:
        return _ERR_SAVE

    return f"✓ Opgeslagen in jouw persoonlijke kennisbank: {title}"


# -- Tool: save_org_knowledge -------------------------------------------------
@mcp.tool(
    description="""Save content to the ORGANISATION knowledge base.

WHEN TO CALL: user says "sla dit op voor het team", "deel dit met de organisatie",
"save this for the team", "share with the org", "voeg toe aan de teamkennis",
or expresses intent to share knowledge with the whole organisation.

PARAMETERS:
  title          - short, descriptive title (max 80 chars); you generate this
  content        - the text to save; may be a summary, quote, or elaboration
  assertion_mode - pick the best fit: "factual", "belief", "hypothesis",
                   "procedural", "quoted", or "unknown" (use "unknown" if
                   the epistemic status is unclear; it is also the system
                   default for missing values)
  tags           - 1-5 tags; free-form or from seed list
  source_note    - (optional) source reference if mentioned by user
"""
)
async def save_org_knowledge(
    title: str,
    content: str,
    assertion_mode: str,
    tags: list[str],
    ctx: Context,
    source_note: str | None = None,
) -> str:
    # SPEC-MCP-AUTH-001 REQ-12 + REQ-15: dispatcher selects OAuth-token pad
    # vs LibreChat internal-secret pad based on Authorization header prefix.
    try:
        verified = await _identify_request(ctx)
    except ValueError as exc:
        return f"Error: {exc}"
    except _IdentificationFailed as exc:
        return str(exc)

    if not assertion_mode:
        # SPEC-TAXONOMY-001 DD-2: missing → "unknown", not "factual".
        # Avoids an unwarranted feit-boost in (future) retrieval scoring.
        assertion_mode = "unknown"
    elif assertion_mode not in VALID_ASSERTION_MODES:
        return _ERR_ASSERTION_MODE.format(assertion_mode)

    assert verified.org_id is not None
    ok = await _save_to_ingest(
        org_id=verified.org_id,
        kb_slug="org",
        title=title,
        content=content,
        assertion_mode=assertion_mode,
        tags=tags,
        source_note=source_note,
    )
    if not ok:
        return _ERR_SAVE

    return f"✓ Opgeslagen in de organisatie-kennisbank: {title}"


# -- Tool: save_to_docs -------------------------------------------------------
@mcp.tool(
    description="""Save content to a Klai Docs documentation knowledge base.

WHEN TO CALL: user says "voeg toe aan de docs", "bewaar in documentatie",
"add to docs", "save to documentation", or wants to write/update a page
in the Gitea-backed documentation system.

PARAMETERS:
  title     - page title
  content   - page content (markdown)
  kb_name   - (optional) KB slug as returned by this tool; NEVER guess this
              value — omit it and the tool will auto-select or return the list
              of valid slugs to choose from
  page_path - (optional) explicit path; auto-generated from title if omitted
"""
)
async def save_to_docs(
    title: str,
    content: str,
    ctx: Context,
    kb_name: str | None = None,
    page_path: str | None = None,
) -> str:
    # SPEC-MCP-AUTH-001 REQ-12 + REQ-15: dispatcher selects OAuth-token pad
    # vs LibreChat internal-secret pad based on Authorization header prefix.
    try:
        verified = await _identify_request(ctx)
    except ValueError as exc:
        return f"Error: {exc}"
    except _IdentificationFailed as exc:
        return str(exc)

    # V009: reject path traversal in caller-supplied KB coordinates
    if kb_name is not None and not _KB_NAME_PATTERN.match(kb_name):
        return (
            "Error: kb_name contains invalid characters. "
            "Only alphanumeric, hyphens, and underscores are allowed."
        )
    if page_path is not None:
        try:
            _validate_page_path(page_path)
        except ValueError:
            # SPEC-SEC-HYGIENE-001 REQ-46.1: keep the user-facing error
            # generic — do NOT echo the specific rejection class (literal
            # vs %-encoded vs NFKC) to avoid handing an attacker a
            # validator-shape oracle.
            return "Error: page_path contains invalid path components."

    # REQ-2.3 / REQ-2.6: outgoing klai-docs URL uses canonical slug from
    # portal verify response, not the LibreChat-asserted X-Org-Slug.
    assert (
        verified.user_id is not None
        and verified.org_id is not None
        and verified.org_slug is not None
    )
    org_slug = verified.org_slug

    # Resolve KB name — always fetch list to validate, auto-select if only one
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{KLAI_DOCS_API_BASE}/api/orgs/{org_slug}/kbs",
                headers={
                    _INTERNAL_SECRET_HEADER: DOCS_INTERNAL_SECRET,
                    "X-User-ID": verified.user_id,
                    "X-Org-ID": verified.org_id,
                },
            )
    except httpx.RequestError as exc:
        logger.error("KB list fetch failed: %s", exc)
        return _ERR_SAVE

    if resp.status_code != 200:
        logger.error(
            "KB list fetch returned %d: %s (org_slug=%s, org_id=%s)",
            resp.status_code,
            sanitize_response_body(resp, _KNOWN_SECRETS, max_len=200),
            org_slug,
            verified.org_id,
        )
        return _ERR_SAVE

    kbs = resp.json()
    if not kbs:
        return "Error: geen documentatie-kennisbanken gevonden voor deze organisatie."

    valid_slugs = [kb.get("slug") for kb in kbs if kb.get("slug")]

    if kb_name is None:
        if len(kbs) == 1:
            kb_name = valid_slugs[0]
        else:
            options = ", ".join(f"{kb.get('slug', '?')} ({kb.get('name', '')})" for kb in kbs)
            return (
                f"Meerdere kennisbanken beschikbaar: {options}. "
                "Geef de slug op als kb_name bij de volgende aanroep."
            )
    elif kb_name not in valid_slugs:
        options = ", ".join(valid_slugs)
        return f"Onbekende kb_name '{kb_name}'. Geldige slugs: {options}."

    # Build page path if not provided — land in inbox/ for manual organisation later
    if page_path is None:
        page_path = f"inbox/{_slugify(title)}"

    today = date.today().isoformat()

    request_body = {
        "title": title,
        "content": content.strip(),
        "icon": "\U0001f4dd",
        "edit_access": "owner",
        "frontmatter": {
            "provenance_type": "synthesized",
            "assertion_mode": "factual",
            "synthesis_depth": 1,
            "belief_time_start": today,
            "belief_time_end": None,
            "superseded_by": None,
            "confidence": "medium",
            "derived_from": [],
            "created_by": verified.user_id,
            "system_time": today,
        },
    }

    # Idempotency-Key is REQUIRED by docs-app for page creation (REQ-UNW-03).
    # Updates to an existing page treat it as optional, but sending one is
    # always safe (REQ-STA-01: same key returns the existing page). A fresh
    # uuid4 per call keeps "second tool invocation" semantics correct: the
    # second call creates a NEW page with a NEW path, not a replay.
    idempotency_key = str(uuid.uuid4())

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{KLAI_DOCS_API_BASE}/api/orgs/{org_slug}/kbs/{kb_name}/pages/{page_path}",
                json=request_body,
                headers={
                    _INTERNAL_SECRET_HEADER: DOCS_INTERNAL_SECRET,
                    "X-User-ID": verified.user_id,
                    "X-Org-ID": verified.org_id,
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
            )
    except httpx.RequestError as exc:
        return f"Error: could not reach klai-docs API ({exc})."

    if resp.status_code not in (200, 201):
        # SPEC-SEC-INTERNAL-001 REQ-8.1 / AC-11.1: the MCP tool return value
        # ends up verbatim in the LibreChat / ChatGPT-compatible chat UI.
        # Echoing ``resp.text`` would leak any header the upstream reflected
        # in its 5xx body (for example DOCS_INTERNAL_SECRET when the docs
        # service runs FastAPI's ServerErrorMiddleware in debug mode).
        # Surface a status code + correlation ID; the sanitized upstream
        # body lands in the structlog stream keyed by the same request_id.
        request_id = str(uuid.uuid4())
        logger.error(
            "save_to_docs upstream returned %d (kb=%s, page=%s, request_id=%s): %s",
            resp.status_code,
            kb_name,
            page_path,
            request_id,
            sanitize_response_body(resp, _KNOWN_SECRETS, max_len=512),
        )
        return (
            f"Error saving to docs: upstream returned HTTP {resp.status_code}. "
            f"Request ID: {request_id}. Operator: check VictoriaLogs."
        )

    return f"✓ Opgeslagen in kennisbank **{kb_name}**: {title} (pad: {page_path})"


# -- Tool: search_knowledge ---------------------------------------------------
# SPEC-MCP-RETRIEVAL-001: lets third-party MCP clients (Claude Desktop,
# Cursor, ChatGPT custom connectors) read from the same KB the LiteLLM
# pre-call hook already serves to LibreChat. The tool is a thin wrapper
# around retrieval-api `/retrieve`:
#   1. dispatcher resolves identity (LibreChat or OAuth path)
#   2. clamp top_k to [1,15], 3.0s timeout (same as the LiteLLM hook)
#   3. POST to retrieval-api with the verified org_id+user_id
#   4. fire retrieval-log + (conditional) gap-event telemetry, labelled
#      with identity.client_id when the caller is an OAuth client
#   5. return list[dict] of chunks with title/source_url/text/score/scope
#
# Failure modes raise ToolError so the MCP host (Claude Desktop) surfaces
# the failure to the LLM rather than letting it claim "no results found"
# when the KB was unreachable. See spec.md REQ-17/18.
@mcp.tool(
    description="""Search the user's Klai knowledge base — personal notes,
organisation docs, and connected sources (Notion, Google Drive, web crawls).

WHEN TO CALL: questions that may be answered by the user's own documentation,
decisions, customer data, or product knowledge. Search BEFORE answering from
general knowledge when the question is org-specific.
NL trigger: "zoek in mijn kennisbank", "wat staat er in onze docs over",
"check de kennisbank".
EN trigger: "search my knowledge base", "what do our docs say about",
"check the knowledge base".

PARAMETERS:
  query  - search query in the user's language (any language; bge-m3
           embeddings are multilingual). Self-contained: resolve pronouns
           and references yourself before passing. Maximum 2000 characters.
  top_k  - 1-15, default 8. Higher values for broad questions.

RETURNS: list of chunks with title, source_url, text, score, scope.
  scope="personal" = user's own saved notes.
  scope="org"      = organisation knowledge.
  Cite by source_url when present; never invent URLs.
"""
)
async def search_knowledge(
    query: str,
    ctx: Context,
    top_k: int = 8,
) -> list[dict]:
    # Identity dispatcher (SPEC-MCP-AUTH-001 REQ-15). Either path raises
    # _IdentificationFailed on auth failure; we propagate it untouched so
    # the MCP host returns a 401 + WWW-Authenticate to the client.
    identity = await _identify_request(ctx)

    # SPEC-MCP-RETRIEVAL-001 REQ-12: clamp silently rather than 400 — external
    # LLMs frequently overshoot; defensively bounding is friendlier than an
    # input-validation error that the LLM has to debug.
    top_k = max(1, min(int(top_k), 15))

    # Query-length clamp: defense-in-depth against runaway prompts (a buggy
    # caller looping on a 100k-char query would otherwise multiply retrieval-
    # api load). 2000 chars is generous — typical KB queries are 5-50 words.
    # Truncate silently rather than 400; the calling LLM is the offender,
    # not the user, and a well-behaved LLM never hits this ceiling.
    if len(query) > 2000:
        logger.warning(
            "search_knowledge_query_truncated: original_len=%d client_id=%s",
            len(query),
            identity.client_id,
        )
        query = query[:2000]

    # SPEC-MCP-RETRIEVAL-001 REQ-13: same 3.0s ceiling as the LiteLLM hook.
    # Configurable via env in a future iteration if cold-start P99 spikes.
    body: dict = {
        "query": query,
        "raw_query": query,
        "org_id": identity.org_id,
        "user_id": identity.user_id,
        "scope": "both",
        "top_k": top_k,
        "conversation_history": [],
        # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4: third-party MCP traffic
        # (Claude Desktop / Cursor / ChatGPT) is privacy-by-default — we
        # always send 'shadow' as the upper bound. Retrieval-api applies
        # ``min(client_requested, canonical_tenant_level)``, so a tenant
        # configured 'off' will see zero shadow rows from MCP traffic
        # (the canonical 'off' wins). A tenant on 'full' caps MCP at
        # 'shadow' (we don't escalate third-party traffic to full-mode
        # debug; full mode is reserved for the LibreChat path operators
        # actively investigate).
        "telemetry_level": "shadow",
    }

    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2: caller-service header is mandatory
    # on /retrieve. ``knowledge-mcp`` is whitelisted in KNOWN_CALLER_SERVICES
    # and granted the ``klai:internal:retrieval:query`` scope on the JWT path.
    #
    # SPEC-MCP-RETRIEVAL-001: RETRIEVAL_INTERNAL_SECRET is the right secret
    # for the X-Internal-Secret header on /retrieve. Using the historic
    # KNOWLEDGE_INGEST_SECRET here would 401 against retrieval-api in
    # production — different services hold different secrets in SOPS.
    headers: dict[str, str] = {
        "X-Caller-Service": "knowledge-mcp",
        "X-Internal-Secret": RETRIEVAL_INTERNAL_SECRET,
    }

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(KNOWLEDGE_RETRIEVE_URL, json=body, headers=headers)
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "search_knowledge_retrieval_failed: type=HTTPStatusError status=%d client_id=%s",
            exc.response.status_code,
            identity.client_id,
        )
        raise ToolError(_ERR_KB_UNAVAILABLE) from exc
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.error(
            "search_knowledge_retrieval_failed: type=%s client_id=%s",
            type(exc).__name__,
            identity.client_id,
        )
        raise ToolError(_ERR_KB_UNAVAILABLE) from exc

    chunks = result.get("chunks", [])
    retrieval_ms = int((time.monotonic() - t0) * 1000)

    # SPEC-MCP-RETRIEVAL-001 REQ-21 / REQ-23: telemetry is fire-and-forget
    # (helpers schedule asyncio.create_task internally) and any failure
    # is swallowed, so wrapping in a broad except keeps the success path
    # robust against e.g. a transient portal-api 5xx during emit.
    try:
        fire_retrieval_log(
            org_id=identity.org_id,
            user_id=identity.user_id,
            chunk_ids=[c.get("chunk_id") for c in chunks if c.get("chunk_id")],
            reranker_scores=[c.get("reranker_score") or 0.0 for c in chunks],
            query_resolved=query,
            caller_client_id=identity.client_id,
        )
        gap = _classify_gap(chunks)
        if gap is not None:
            fire_gap_event(
                org_id=identity.org_id,
                user_id=identity.user_id,
                query_text=query,
                gap_type=gap,
                chunks=chunks,
                retrieval_ms=retrieval_ms,
                caller_client_id=identity.client_id,
            )
    except Exception as exc:
        logger.warning("search_knowledge_telemetry_failed: %s", exc)

    return [
        {
            "title": c.get("title") or c.get("metadata", {}).get("title", ""),
            "source_url": c.get("source_url"),
            "text": (c.get("text") or "").strip(),
            "score": c.get("reranker_score")
            if c.get("reranker_score") is not None
            else c.get("score"),
            "scope": c.get("scope", "org"),
        }
        for c in chunks
    ]


# -- ASGI app -----------------------------------------------------------------
#
# SPEC-MCP-AUTH-001 REQ-8 + REQ-10: wrap the FastMCP ASGI app with a
# Starlette parent that adds:
#
#   1. ``GET /.well-known/oauth-protected-resource`` — RFC 9728 PRM
#   2. WWW-Authenticate header on 401 responses (added via middleware)
#
# Mount the FastMCP app at ``/`` so all existing MCP routes (``/mcp``,
# ``/sse``, etc.) continue to work unchanged.

from starlette.applications import Starlette  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402
from starlette.requests import Request as StarletteRequest  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402


async def _well_known_protected_resource(request: StarletteRequest) -> JSONResponse:
    """RFC 9728 Protected Resource Metadata.

    Per SPEC-MCP-AUTH-001 REQ-8. Lets MCP clients (Claude Desktop, Cursor,
    ChatGPT custom connectors) auto-discover the authorization server and
    required scopes after receiving a 401.
    """
    metadata = {
        "resource": MCP_OAUTH_RESOURCE_URL,
        "authorization_servers": [MCP_OAUTH_ISSUER_BASE_URL],
        "scopes_supported": ["mcp:knowledge"],
        "bearer_methods_supported": ["header"],
    }
    return JSONResponse(metadata, headers={"Cache-Control": "public, max-age=300"})


class _WWWAuthenticateMiddleware(BaseHTTPMiddleware):
    """Add WWW-Authenticate to every 401 response (REQ-10).

    The header advertises both the realm and the PRM endpoint so clients
    can auto-discover the OAuth flow without prior knowledge.
    """

    async def dispatch(
        self,
        request: StarletteRequest,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # SPEC-MCP-AUTH-001: short-circuit auth on the /mcp endpoint BEFORE
        # FastMCP gets the request. Without this Claude.ai sees HTTP 200 on
        # the initial discovery POST and never triggers the OAuth flow — the
        # auth check inside the tool bodies fires too late (after Claude has
        # already cached "this server doesn't need auth").
        path = request.url.path
        if path.startswith("/mcp") or path == "/sse":
            authorization = request.headers.get("authorization", "")
            internal_secret = request.headers.get(_INTERNAL_SECRET_HEADER.lower(), "")
            has_oauth = authorization.lower().startswith(
                "bearer klai_mcp_"
            ) and not authorization.lower().startswith("bearer klai_mcp_rt_")
            has_internal = bool(internal_secret)
            if not has_oauth and not has_internal:
                from starlette.responses import JSONResponse as _JSON

                prm_url = f"{MCP_OAUTH_RESOURCE_URL}/.well-known/oauth-protected-resource"
                return _JSON(
                    {
                        "error": "unauthorized",
                        "error_description": (
                            "MCP requests require Authorization: Bearer klai_mcp_<token>. "
                            "Use OAuth flow at " + MCP_OAUTH_ISSUER_BASE_URL + "/oauth/authorize."
                        ),
                    },
                    status_code=401,
                    headers={
                        "WWW-Authenticate": (
                            f'Bearer realm="klai-mcp", '
                            f'resource_metadata="{prm_url}", '
                            f'scope="mcp:knowledge"'
                        ),
                    },
                )

        response: Response = await call_next(request)
        if response.status_code == 401 and "www-authenticate" not in {
            k.lower() for k in response.headers
        }:
            prm_url = f"{MCP_OAUTH_RESOURCE_URL}/.well-known/oauth-protected-resource"
            response.headers["WWW-Authenticate"] = (
                f'Bearer realm="klai-mcp", resource_metadata="{prm_url}", scope="mcp:knowledge"'
            )
        return response


_mcp_app = mcp.streamable_http_app()
# Propagate FastMCP's lifespan (initializes session_manager task group) to
# the Starlette parent so the inner MCP routes work. Without this the inner
# /mcp handler raises "Task group is not initialized".
app = Starlette(
    routes=[
        # RFC 9728 § 3.1 + MCP authorization spec: PRM is served at BOTH
        # the root path AND with the resource path-component appended.
        # Some clients (Anthropic broker tested 2026-05-07) only probe
        # the sub-path variant — a 404 there short-circuits the OAuth
        # discovery chain even if the root variant works.
        Route(
            "/.well-known/oauth-protected-resource",
            _well_known_protected_resource,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            _well_known_protected_resource,
            methods=["GET"],
        ),
        Mount("/", app=_mcp_app),
    ],
    middleware=[Middleware(_WWWAuthenticateMiddleware)],
    lifespan=_mcp_app.router.lifespan_context,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, log_level="info")  # noqa: S104
