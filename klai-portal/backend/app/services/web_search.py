"""Server-side web search for the Partner API.

Calls the same self-hosted SearXNG instance the chat surfaces use, takes the
top results, and turns them into a compact context block that gets appended to
the chat system prompt. Fail-open by design: if SearXNG is slow or down, we log
and return no results so the answer still goes out (without web context) rather
than failing the whole request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.trace import get_trace_headers

if TYPE_CHECKING:
    from app.core.config import Settings

logger = structlog.get_logger()

# Keep web results short: enough to ground an answer, not enough to blow up the
# prompt or leak a wall of scraped text.
_MAX_CONTENT_CHARS = 600


async def search_web(
    query: str,
    *,
    settings: Settings,
    limit: int = 5,
    timeout: float = 8.0,
) -> list[dict[str, str]]:
    """Return up to ``limit`` web results for ``query`` from SearXNG.

    Each result is ``{"title", "url", "content"}``. Returns an empty list on any
    failure (timeout, non-200, malformed body) — the caller treats "no results"
    and "search failed" the same: answer without web context.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    url = f"{settings.searxng_url.rstrip('/')}/search"
    params = {"q": cleaned, "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=get_trace_headers())
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
    except Exception:
        logger.warning("web_search_failed", query_len=len(cleaned), exc_info=True)
        return []

    raw_results = body.get("results")
    if not isinstance(raw_results, list):
        logger.warning("web_search_unexpected_body", query_len=len(cleaned))
        return []

    results: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        result_url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not result_url or not title:
            continue
        content = str(item.get("content") or "").strip()[:_MAX_CONTENT_CHARS]
        results.append({"title": title, "url": result_url, "content": content})
        if len(results) >= limit:
            break
    return results


def build_web_results_block(results: list[dict[str, str]]) -> str:
    """Render web results as a system-prompt addition the model can ground on."""
    if not results:
        return ""
    lines = [
        "",
        "[Web results]",
        "Live web results for this question. Use them when they help, and cite the URL of any result you rely on.",
    ]
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r['title']} - {r['url']}")
        if r["content"]:
            lines.append(r["content"])
    lines.append("[End web results]")
    return "\n".join(lines)
