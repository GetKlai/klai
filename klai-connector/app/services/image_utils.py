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
