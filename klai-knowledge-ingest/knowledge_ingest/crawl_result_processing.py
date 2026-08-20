"""Pure normalization of Crawl4AI page results."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

from knowledge_ingest.crawl4ai_types import CrawlResult

THIN_CONTENT_WORD_COUNT = 100
_MUSTACHE_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")
_MD_LINK_URL_RE = re.compile(r"\]\([^)]*\)")


class _HTMLTextCounter(HTMLParser):
    """Cheap rendered-HTML text signal for detecting over-pruned results."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],  # noqa: ARG002
    ) -> None:
        if tag.lower() in {"script", "style", "noscript", "template", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def html_text_word_count(html: str) -> int:
    """Count visible HTML words without executing or rendering the document."""
    if not html:
        return 0
    parser = _HTMLTextCounter()
    try:
        parser.feed(html)
    except Exception:
        return 0
    return len(unescape(" ".join(parser.parts)).split())


def should_retry_relaxed_for_thin_content(result: CrawlResult) -> bool:
    """Return whether strict extraction hid content that is present in HTML."""
    return (
        result.success
        and result.word_count < THIN_CONTENT_WORD_COUNT
        and html_text_word_count(result.html) >= THIN_CONTENT_WORD_COUNT
    )


def strip_unrendered_template_lines(markdown: str) -> str:
    """Drop lines that are almost entirely unrendered ``{{...}}`` tokens."""
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
        if len(re.findall(r"\w+", remainder, flags=re.UNICODE)) <= 2:
            continue
        kept.append(line)
    return "\n".join(kept)


def extract_result(url: str, page: dict[str, Any]) -> CrawlResult:
    """Parse a single page result from the Crawl4AI REST response."""
    markdown = page.get("markdown", "")
    if isinstance(markdown, dict):
        fit = markdown.get("fit_markdown", "") or ""
        raw = markdown.get("raw_markdown", "") or ""
    else:
        fit = ""
        raw = markdown or ""

    markdown_v2 = page.get("markdown_v2", {})
    if not fit:
        fit = markdown_v2.get("fit_markdown", "") or ""
    if not raw:
        raw = markdown_v2.get("raw_markdown", "") or ""

    fit = strip_unrendered_template_lines(fit)
    raw = strip_unrendered_template_lines(raw)
    text = fit or raw
    return CrawlResult(
        url=page.get("url", url),
        fit_markdown=fit,
        raw_markdown=raw,
        html=page.get("html", ""),
        word_count=len(text.split()),
        success=page.get("success", True),
        requested_url=url,
        links=page.get("links", {}),
        media=page.get("media") or {},
        error_message=page.get("error_message"),
        metadata=page.get("metadata"),
        response_headers=page.get("response_headers"),
        status_code=page.get("status_code"),
    )
