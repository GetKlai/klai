"""
klai-knowledge-mcp
MCP server that lets LibreChat agents save content to:
  - personal knowledge base  (via knowledge-ingest API)
  - organisation knowledge base  (via knowledge-ingest API)
  - documentation KB in Klai Docs  (via Gitea-backed klai-docs API)

Transport: streamable-http (LibreChat v0.8.4+)
Auth:      service token forwarded to klai-docs as X-Internal-Secret
Identity:  X-User-ID, X-Org-ID, X-Org-Slug request headers injected by
           LibreChat config; accessed per-call via FastMCP Context parameter.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from logging_setup import setup_logging

setup_logging()

logger = logging.getLogger(__name__)

# -- Config -------------------------------------------------------------------
KLAI_DOCS_API_BASE = os.environ["KLAI_DOCS_API_BASE"]  # http://docs-app:3000
DOCS_INTERNAL_SECRET = os.environ["DOCS_INTERNAL_SECRET"]
KNOWLEDGE_INGEST_URL = os.environ["KNOWLEDGE_INGEST_URL"]  # http://knowledge-ingest:8000
KNOWLEDGE_INGEST_SECRET = os.getenv("KNOWLEDGE_INGEST_SECRET", "")
_INTERNAL_SECRET_HEADER = "X-Internal-Secret"
DEFAULT_ORG_SLUG = os.getenv("DEFAULT_ORG_SLUG", "")

# Path validation patterns
_KB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

AssertionMode = Literal["factual", "procedural", "quoted", "belief", "hypothesis"]
VALID_ASSERTION_MODES: frozenset[str] = frozenset(get_args(AssertionMode))

# Error messages (bilingual NL/EN)
_ERR_SAVE = (
    "Er is een fout opgetreden bij het opslaan. Probeer het opnieuw.\n"
    "(An error occurred while saving. Please try again.)"
)
_ERR_ASSERTION_MODE = (
    "Error: invalid assertion_mode '{}'. "
    f"Valid values: {', '.join(sorted(VALID_ASSERTION_MODES))}"
)


# -- Identity -----------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Identity:
    """User/org identity extracted from request headers."""

    user_id: str
    org_id: str
    org_slug: str


# @MX:NOTE _get_identity — headers injected by LibreChat config;
# X-User-ID = LibreChat MongoDB user ID = same as data["user"] in LiteLLM hook
def _get_identity(ctx: Context) -> Identity:
    """Extract identity headers from the FastMCP request context.

    Raises ValueError when required headers are absent.
    """
    headers = ctx.request_context.request.headers
    user_id = headers.get("x-user-id", "")
    org_id = headers.get("x-org-id", "")
    org_slug = headers.get("x-org-slug") or DEFAULT_ORG_SLUG
    if not headers.get("x-org-slug"):
        logger.warning(
            "X-Org-Slug header missing; falling back to DEFAULT_ORG_SLUG=%r. "
            "Check LibreChat header forwarding config.",
            DEFAULT_ORG_SLUG,
        )

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
    return Identity(user_id=user_id, org_id=org_id, org_slug=org_slug)


def _validate_incoming_secret(ctx: Context) -> None:
    """Validate X-Internal-Secret on incoming MCP requests.

    Raises ValueError when the secret is configured but missing or incorrect.
    No-ops when KNOWLEDGE_INGEST_SECRET is not set (gradual rollout).
    """
    if not KNOWLEDGE_INGEST_SECRET:
        return
    headers = ctx.request_context.request.headers
    provided = headers.get(_INTERNAL_SECRET_HEADER.lower(), "")
    if not provided or not hmac.compare_digest(provided, KNOWLEDGE_INGEST_SECRET):
        raise ValueError("Invalid or missing X-Internal-Secret header.")


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

    headers: dict[str, str] = {}
    if KNOWLEDGE_INGEST_SECRET:
        headers[_INTERNAL_SECRET_HEADER] = KNOWLEDGE_INGEST_SECRET

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


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = text.encode("ascii", "ignore").decode()  # strip accented chars
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:60] or "note"


# -- MCP server ---------------------------------------------------------------
mcp = FastMCP(
    "klai-knowledge",
    transport_security=TransportSecuritySettings(
        # Docker-internal service: no external access, DNS rebinding protection not needed.
        # LibreChat sends Host: klai-knowledge-mcp:8080 which is the Docker service name.
        enable_dns_rebinding_protection=False,
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
  assertion_mode - pick the best fit: "factual", "procedural", "quoted",
                   "belief", or "hypothesis"
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
    try:
        _validate_incoming_secret(ctx)
        identity = _get_identity(ctx)
    except ValueError as exc:
        return f"Error: {exc}"

    if not assertion_mode:
        assertion_mode = "factual"
    elif assertion_mode not in VALID_ASSERTION_MODES:
        return _ERR_ASSERTION_MODE.format(assertion_mode)

    ok = await _save_to_ingest(
        org_id=identity.org_id,
        kb_slug=f"personal-{identity.user_id}",
        title=title,
        content=content,
        assertion_mode=assertion_mode,
        tags=tags,
        source_note=source_note,
        user_id=identity.user_id,
    )
    if not ok:
        return _ERR_SAVE

    return f"\u2713 Opgeslagen in jouw persoonlijke kennisbank: {title}"


# -- Tool: save_org_knowledge -------------------------------------------------
@mcp.tool(
    description="""Save content to the ORGANISATION knowledge base.

WHEN TO CALL: user says "sla dit op voor het team", "deel dit met de organisatie",
"save this for the team", "share with the org", "voeg toe aan de teamkennis",
or expresses intent to share knowledge with the whole organisation.

PARAMETERS:
  title          - short, descriptive title (max 80 chars); you generate this
  content        - the text to save; may be a summary, quote, or elaboration
  assertion_mode - pick the best fit: "factual", "procedural", "quoted",
                   "belief", or "hypothesis"
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
    try:
        _validate_incoming_secret(ctx)
        identity = _get_identity(ctx)
    except ValueError as exc:
        return f"Error: {exc}"

    if not assertion_mode:
        assertion_mode = "factual"
    elif assertion_mode not in VALID_ASSERTION_MODES:
        return _ERR_ASSERTION_MODE.format(assertion_mode)

    ok = await _save_to_ingest(
        org_id=identity.org_id,
        kb_slug="org",
        title=title,
        content=content,
        assertion_mode=assertion_mode,
        tags=tags,
        source_note=source_note,
    )
    if not ok:
        return _ERR_SAVE

    return f"\u2713 Opgeslagen in de organisatie-kennisbank: {title}"


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
    try:
        _validate_incoming_secret(ctx)
        identity = _get_identity(ctx)
    except ValueError as exc:
        return f"Error: {exc}"

    # V009: reject path traversal in caller-supplied KB coordinates
    if kb_name is not None and not _KB_NAME_PATTERN.match(kb_name):
        return (
            "Error: kb_name contains invalid characters. "
            "Only alphanumeric, hyphens, and underscores are allowed."
        )
    if page_path is not None:
        if ".." in page_path or "\\" in page_path or page_path.startswith("/"):
            return "Error: page_path contains invalid path components."

    org_slug = identity.org_slug
    if not org_slug:
        return "Error: X-Org-Slug header missing and DEFAULT_ORG_SLUG not set."

    # Resolve KB name — always fetch list to validate, auto-select if only one
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{KLAI_DOCS_API_BASE}/api/orgs/{org_slug}/kbs",
                headers={
                    _INTERNAL_SECRET_HEADER: DOCS_INTERNAL_SECRET,
                    "X-User-ID": identity.user_id,
                    "X-Org-ID": identity.org_id,
                },
            )
    except httpx.RequestError as exc:
        logger.error("KB list fetch failed: %s", exc)
        return _ERR_SAVE

    if resp.status_code != 200:
        logger.error(
            "KB list fetch returned %d: %s (org_slug=%s, org_id=%s)",
            resp.status_code, resp.text[:200], org_slug, identity.org_id,
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
            options = ", ".join(
                f"{kb.get('slug', '?')} ({kb.get('name', '')})" for kb in kbs
            )
            return (
                f"Meerdere kennisbanken beschikbaar: {options}. "
                "Geef de slug op als kb_name bij de volgende aanroep."
            )
    elif kb_name not in valid_slugs:
        options = ", ".join(valid_slugs)
        return (
            f"Onbekende kb_name '{kb_name}'. Geldige slugs: {options}."
        )

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
            "created_by": identity.user_id,
            "system_time": today,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{KLAI_DOCS_API_BASE}/api/orgs/{org_slug}/kbs/{kb_name}/pages/{page_path}",
                json=request_body,
                headers={
                    _INTERNAL_SECRET_HEADER: DOCS_INTERNAL_SECRET,
                    "X-User-ID": identity.user_id,
                    "X-Org-ID": identity.org_id,
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError as exc:
        return f"Error: could not reach klai-docs API ({exc})."

    if resp.status_code not in (200, 201):
        return f"Error: klai-docs returned HTTP {resp.status_code}. Details: {resp.text[:300]}"

    return f"\u2713 Opgeslagen in kennisbank **{kb_name}**: {title} (pad: {page_path})"


# -- ASGI app -----------------------------------------------------------------
app = mcp.streamable_http_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, log_level="info")  # noqa: S104
