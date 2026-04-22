"""Image extraction utilities for connector adapters."""

import re
from urllib.parse import urljoin

# Matches markdown image syntax: ![alt text](url)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def extract_markdown_image_urls(content: str) -> list[tuple[str, str]]:
    """Extract image references from markdown content.

    Returns a list of ``(alt_text, url)`` tuples.  Data URIs are skipped.
    """
    results: list[tuple[str, str]] = []
    for alt, url in _MD_IMAGE_RE.findall(content):
        url = url.strip()
        if url.startswith("data:"):
            continue
        results.append((alt, url))
    return results


# @MX:NOTE: Guards against crawl4ai srcset parsing producing fragments as "src"
# @MX:REASON: Cloudflare image URLs contain commas (e.g. "/w=1920,quality=90,fit=scale-down")
#   which break naive srcset comma-splitters. Fragments like "quality=90" or "fit=scale-down"
#   arrive as separate src values — they must be rejected before resolve_relative_url()
#   resolves them as relative paths against base_url and generates 404 download storms.
def is_valid_image_src(src: str) -> bool:
    """Return True if *src* looks like a plausible image URL or relative path.

    Rejects srcset fragments such as ``quality=90`` or ``fit=scale-down`` that
    reach us when HTML parsers split on comma without respecting commas inside URLs.
    """
    if not src:
        return False
    s = src.strip()
    if s.startswith("data:"):
        return False
    # Absolute URL or protocol-relative: accept.
    if s.startswith(("http://", "https://", "//")):
        return True
    # Root-relative or dot-relative: accept.
    if s.startswith(("/", "./", "../")):
        return True
    # Anything else must at least contain a path separator OR a file-like dot
    # with no '=' (disqualifies ``quality=90`` style fragments).
    if "=" in s and "/" not in s:
        return False
    return "/" in s or "." in s


def resolve_relative_url(url: str, base_url: str) -> str:
    """Resolve *url* against *base_url* if it is relative.

    Absolute URLs (starting with ``http://`` or ``https://``) are returned
    unchanged.  If *base_url* is empty the original *url* is returned as-is.
    """
    if url.startswith(("http://", "https://")):
        return url
    if not base_url:
        return url
    return urljoin(base_url, url)


def dedupe_image_urls(urls: list[str]) -> list[str]:
    """Return *urls* with duplicates removed, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out
