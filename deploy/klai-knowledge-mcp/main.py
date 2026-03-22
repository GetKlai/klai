"""
klai-knowledge-mcp
MCP server that lets LibreChat agents save content to a user's personal
knowledge base in Klai Docs (Gitea-backed markdown).

Transport: streamable-http (LibreChat v0.8.4+)
Auth:      service token forwarded to klai-docs as X-Internal-Secret
Identity:  X-User-ID + X-Org-Slug request headers injected by LibreChat config;
           accessed per-call via FastMCP Context parameter.
"""

import os
import re
import uuid
from datetime import date

import httpx
from mcp.server.fastmcp import Context, FastMCP

# ── Config ────────────────────────────────────────────────────────────────────
KLAI_DOCS_API_BASE = os.environ["KLAI_DOCS_API_BASE"]    # http://docs-app:3000
DOCS_INTERNAL_SECRET = os.environ["DOCS_INTERNAL_SECRET"]
PUBLIC_KB_BASE_URL = os.getenv("PUBLIC_KB_BASE_URL", "")  # https://kb.${DOMAIN}
PERSONAL_KB = os.getenv("PERSONAL_KB_NAME", "personal")
DEFAULT_ORG_SLUG = os.getenv("DEFAULT_ORG_SLUG", "")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = text.encode("ascii", "ignore").decode()  # strip accented chars
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:60] or "note"


def _infer_provenance(assertion_mode: str) -> str:
    """Map assertion_mode → provenance_type (§3.2)."""
    return "observed" if assertion_mode == "quoted" else "synthesized"


# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "klai-knowledge",
    instructions=(
        "You have access to the user's personal Klai Knowledge Base. "
        "Call save_to_personal_kb whenever the user wants to save, note, "
        "or remember something from the conversation."
    ),
)


@mcp.tool(
    description="""Save content to the user's personal knowledge base.

WHEN TO CALL: user says "sla dit op", "onthoud dit", "save this", "note this",
or expresses intent to keep something for later reference.

PARAMETERS:
  title         — short, descriptive title (max 80 chars); you generate this
  content       — the text to save; may be a summary, quote, or elaboration
  assertion_mode — pick the best fit:
    factual     : verified claim ("the return period is 30 days")
    procedural  : step-by-step instruction or process
    belief      : likely true but not confirmed ("we think this affects macOS 14")
    hypothesis  : explicitly speculative, needs validation
    quoted      : verbatim or close paraphrase of a named external source
  tags          : 1–5 tags; choose from the seed list or use free-form:
    voip, macos, windows, networking, auth, billing, onboarding, procedure,
    product, integration, workaround, decision, insight, research, meeting,
    customer, support, configuration, security, dns
  source_note   : (optional) if the user mentioned a source (article, URL,
                  book, person), put that reference here; leave empty otherwise
"""
)
async def save_to_personal_kb(
    title: str,
    content: str,
    assertion_mode: str,
    tags: list[str],
    ctx: Context,
    source_note: str = "",
) -> str:
    # Headers are injected by LibreChat via mcpServers.headers config
    headers = ctx.request_context.request.headers
    user_id = headers.get("x-user-id", "")
    org_slug = headers.get("x-org-slug", DEFAULT_ORG_SLUG)

    if not user_id:
        return "Error: X-User-ID header missing — cannot identify user."
    if not org_slug:
        return "Error: X-Org-Slug header missing and DEFAULT_ORG_SLUG not set."

    valid_modes = {"factual", "procedural", "belief", "hypothesis", "quoted"}
    if assertion_mode not in valid_modes:
        assertion_mode = "factual"

    artifact_id = str(uuid.uuid4())
    slug = f"{_slugify(title)}-{artifact_id[:6]}"
    page_path = f"users/{user_id}/{slug}"  # klai-docs appends .md

    today = date.today().isoformat()

    # klai-docs PUT accepts `frontmatter` dict; extra fields are spread into
    # the page frontmatter by route.ts (see klai-knowledge-architecture.md §5.4)
    request_body = {
        "title": title,
        "content": content.strip(),
        "icon": "📝",
        "edit_access": "owner",
        "frontmatter": {
            "provenance_type": _infer_provenance(assertion_mode),
            "assertion_mode": assertion_mode,
            "synthesis_depth": 1,
            "belief_time_start": today,
            "belief_time_end": None,
            "superseded_by": None,
            "confidence": "medium",
            "derived_from": [],
            **({"source_note": source_note} if source_note else {}),
            **({"tags": tags[:5]} if tags else {}),
            "created_by": user_id,
            "system_time": today,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{KLAI_DOCS_API_BASE}/api/orgs/{org_slug}/kbs/{PERSONAL_KB}/pages/{page_path}",
                json=request_body,
                headers={
                    "X-Internal-Secret": DOCS_INTERNAL_SECRET,
                    "X-User-ID": user_id,
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError as exc:
        return f"Error: could not reach klai-docs API ({exc})."

    if resp.status_code not in (200, 201):
        return (
            f"Error: klai-docs returned HTTP {resp.status_code}. "
            f"Details: {resp.text[:300]}"
        )

    location = (
        f"{PUBLIC_KB_BASE_URL}/{org_slug}/{PERSONAL_KB}/{page_path}"
        if PUBLIC_KB_BASE_URL
        else f"path: {page_path}"
    )
    return (
        f"Saved as **{title}**.\n"
        f"ID: `{artifact_id}`\n"
        f"Type: {assertion_mode}\n"
        f"Location: {location}"
    )


# ── ASGI app ──────────────────────────────────────────────────────────────────
# streamable_http_app() returns a Starlette app with the MCP endpoint at /mcp
app = mcp.streamable_http_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, log_level="info")
