"""URL source extractor (SPEC-KB-SOURCES-001 Module 2).

Extracts a user-supplied URL through the authenticated knowledge-ingest
preview service (``knowledge_ingest_client.preview_crawl``) and returns its
title + markdown. SSRF guarding still happens in the portal, before any
outbound call, via ``_url_validator.validate_url``.

Knowledge-ingest owns the crawler and its service authentication; direct
portal-to-crawl4ai calls are refused.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import structlog

from app.services import knowledge_ingest_client
from app.services.source_extractors._url_validator import validate_url
from app.services.source_extractors.exceptions import SourceFetchError

logger = structlog.get_logger()

_TITLE_MAX_CHARS = 120

# First ATX-style H1 in the markdown — greedy match on the heading line only.
_H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)

# Unrendered client-side template token: ``{{item.Name}}``, ``{{ x }}``.
_MUSTACHE_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")
_MD_LINK_URL_RE = re.compile(r"\]\([^)]*\)")


def strip_unrendered_template_lines(markdown: str) -> str:
    """Drop lines that are (almost) entirely unrendered ``{{...}}`` tokens.

    AngularJS/Vue/Handlebars pages whose app fails to render in the crawler
    browser leave literal template tokens in the DOM; those lines are markup
    residue, never content. A line is removed only when, after taking out
    template tokens and markdown link URLs, at most two words remain. Lines
    with real prose are kept unchanged, as is fenced code.

    Keep in sync with the same-named helper in
    ``klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.py``.
    """
    if "{{" not in markdown:
        return markdown
    kept: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            kept.append(line)
            continue
        if in_fence or "{{" not in line:
            kept.append(line)
            continue
        remainder = _MUSTACHE_TOKEN_RE.sub(" ", _MD_LINK_URL_RE.sub("]", line))
        words = re.findall(r"\w+", remainder, flags=re.UNICODE)
        if len(words) <= 2:
            continue
        kept.append(line)
    return "\n".join(kept)


def _derive_title(markdown: str, hostname: str | None) -> str:
    """Title from: first H1 > first non-empty line (<=120 chars) > hostname."""
    if markdown:
        h1 = _H1_PATTERN.search(markdown)
        if h1:
            return h1.group(1).strip()[:_TITLE_MAX_CHARS]

        for line in markdown.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped[:_TITLE_MAX_CHARS]

    return hostname or "Untitled page"


async def extract_url(url: str, org_id: str) -> tuple[str, str, str]:
    """Extract ``url`` via knowledge-ingest preview → (title, markdown, source_ref).

    ``org_id`` is the requesting tenant's Zitadel org id, forwarded verbatim:
    it is the identity knowledge-ingest scopes the crawl under, and a
    portal-local numeric id is not that identity.

    Raises:
        InvalidUrlError: URL malformed or disallowed scheme.
        SSRFBlockedError: URL resolves to a blocked IP range / docker host.
        SourceFetchError: the preview came back without usable content — an
            empty ``fit_markdown``, or the ``unknown`` classification the
            ingest client reports for a failed upstream call. Content is never
            ingested empty; a degraded-but-non-empty preview (any other
            classification) is kept, as before.
    """
    canonical = await validate_url(url)
    hostname = urlparse(canonical).hostname

    preview = await knowledge_ingest_client.preview_crawl(
        canonical,
        org_id=org_id,
        single_page_source=True,
    )

    markdown = strip_unrendered_template_lines(str(preview.get("fit_markdown") or ""))
    classification = preview.get("classification")
    if not markdown.strip() or classification == "unknown":
        logger.warning(
            "preview_unusable",
            hostname=hostname,
            classification=classification,
            reason=preview.get("classification_reason"),
        )
        raise SourceFetchError(f"knowledge-ingest preview returned no usable content ({classification})")

    title = _derive_title(markdown, hostname)
    return title, markdown, canonical
