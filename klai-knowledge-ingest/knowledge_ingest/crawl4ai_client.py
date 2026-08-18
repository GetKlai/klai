"""HTTP client for the Crawl4AI REST API (shared Docker container).

Replaces direct crawl4ai Python library usage.  All crawl requests go through
the REST API at ``settings.crawl4ai_api_url`` so knowledge-ingest does not need
the crawl4ai package (or a local Chromium install) as a dependency.
"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urldefrag, urlparse, urlunparse

import httpx
import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.domain_rate_limit_control import MIN_DOMAIN_RATE_LIMIT
from knowledge_ingest.reason_codes import FetchReasonCode

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Shape of the per-URL outcome captured for crawl_jobs.fetch_outcomes JSONB.
# Keys match the migration shape: {"url", "reason_code", "status_code",
# "content_length"}. ``reason_code`` MUST be a FetchReasonCode value.
# ---------------------------------------------------------------------------

FetchOutcome = dict[str, Any]

# ---------------------------------------------------------------------------
# JS scripts — single source of truth for content filtering
# ---------------------------------------------------------------------------

# crawl4ai >= 0.9 rejects ``js_code`` / ``js_code_before_wait`` on every
# network request (the untrusted-config boundary that fixes CVE-2026-57572),
# with no trusted-caller escape hatch. ``wait_for`` JS remains allowlisted,
# so the one-time DOM preparation that used to live in those fields now runs
# inside the wait_for predicate: the first poll mutates the DOM (guarded by a
# dataset marker so repeated polling never re-runs it), later polls evaluate
# readiness after a 300ms settle — preserving the old js_code sleep that let
# expanded toggles render. If upstream ever locks down wait_for JS too, the
# fallback is computing these steps server-side from the returned HTML.

# Strip nav chrome so the word-count condition fires only when article
# content is present. Semantic selectors only — never class/id substring
# selectors (see pitfalls/backend.md).
JS_PREP_REMOVE_CHROME = (
    "['nav','header','footer','aside',"
    "'[role=\"navigation\"]','[role=\"banner\"]','[role=\"contentinfo\"]',"
    "'[role=\"complementary\"]','[role=\"search\"]'"
    "].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));"
)

# Open collapsed toggles (Notion / <details>) so lazy content renders.
JS_PREP_EXPAND_TOGGLES = (
    "document.querySelectorAll('details:not([open])')"
    ".forEach(d => d.setAttribute('open', ''));"
    "document.querySelectorAll('.notion-toggle__summary, "
    '[data-block-type="toggle"] > *:first-child\')'
    ".forEach(s => s.click());"
)


def build_wait_for(
    *,
    strip_chrome: bool,
    ready_condition: str,
) -> str:
    """Compose the crawl4ai ``wait_for`` predicate with one-time DOM prep.

    ``ready_condition`` is a JS boolean expression evaluated on every poll
    after the prep + 300ms settle. The prep block runs exactly once per page
    (``data-klai-prep-ts`` marker on <html>).
    """
    prep = (JS_PREP_REMOVE_CHROME if strip_chrome else "") + JS_PREP_EXPAND_TOGGLES
    return (
        "js:() => {"
        " const d = document.documentElement.dataset;"
        " if (!d.klaiPrepTs) { " + prep + " d.klaiPrepTs = String(Date.now()); return false; }"
        " if (Date.now() - Number(d.klaiPrepTs) < 300) return false;"
        " return " + ready_condition + ";"
        " }"
    )


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CrawlResult:
    """Normalised result from a single-page crawl."""

    url: str
    fit_markdown: str
    raw_markdown: str
    html: str
    word_count: int
    success: bool
    links: dict[str, list[dict]] = field(default_factory=dict)
    # SPEC-CRAWLER-004 Fase A: crawl4ai populates ``media.images`` with dicts
    # shaped like ``{"src": "...", "alt": "...", "score": N}``. Other keys
    # (``videos``, ``audios``) exist but knowledge-ingest currently ignores them.
    media: dict[str, list[dict]] = field(default_factory=dict)
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    response_headers: dict[str, str] | None = None
    # 2026-08-14: the real HTTP status code behind a failed fetch, when
    # known — either from crawl4ai's page-result shape (``page["status_code"]``)
    # or from a raised ``httpx.HTTPStatusError`` (see
    # ``_status_code_from_exception``). Lets downstream classification
    # (``_classify_fetch_outcome``) map a failure to HTTP_4XX/HTTP_5XX
    # honestly instead of falling through to UNKNOWN_EXCEPTION — see the
    # bulk-5xx sequential-recovery rework in ``crawl_site`` / adapters/crawler.py.
    status_code: int | None = None
    # 2026-08-18 (SPEC-CRAWLER-FAILURE-EVIDENCE): populated ONLY by the
    # single-page fetch paths (``_fetch_seed_page`` / ``_crawl_page_with_config``)
    # when they catch a raised Python exception, BEFORE it is collapsed into
    # the bare ``str(exc)`` stored in ``error_message`` above. Lets the
    # fetch_outcomes evidence builder (``_evidence_for_crawl_result``) recover
    # the real exception type + response body (which may carry crawl4ai's
    # ``correlation_id``) instead of only the flattened string. Both additive
    # and None for every other CrawlResult (success, page-level failure with
    # no raised exception, bulk-path results) — never read outside the
    # evidence builder.
    error_type: str | None = None
    raw_error_text: str | None = None


DiscoverySourceKind = Literal["start", "sitemap", "page_link"]
DiscoveryStatus = Literal["queued", "fetched", "omitted"]


@dataclass
class DiscoveredUrl:
    """One URL in Klai's crawl frontier ledger.

    Crawl4AI renders pages and extracts links; Klai owns the crawl plan.
    The ledger lets us explain every in-scope discovered URL as fetched or
    deliberately omitted instead of silently losing URLs inside a third-party
    BFS frontier.
    """

    url: str
    canonical_url: str
    depth: int
    discovered_from: str | None
    source_kind: DiscoverySourceKind
    priority: int
    order: int
    status: DiscoveryStatus = "queued"
    reason_code: str | None = None


_PRIORITY_START = 0
_PRIORITY_SITEMAP = 10
_PRIORITY_SECTION_ROOT = 20
_PRIORITY_LISTING_CHILD = 25
_PRIORITY_PAGE_LINK = 50
_LISTING_LINK_THRESHOLD = 50
_THIN_CONTENT_WORD_COUNT = 100

# 2026-08-18 (intermedia.com incident) — a website crawl exists to fetch
# HTML pages, not documents. crawl4ai's browser tries to *navigate* to
# every discovered same-domain link; pointed at a PDF (or any other
# document/archive/media/binary), the browser starts a download instead
# of rendering, ``Page.goto`` raises, and the WHOLE bulk chunk that URL
# happened to share fails with an opaque HTTP 500 — poisoning every
# innocent HTML URL alongside it (see ``_is_recoverable_bulk_failure``'s
# docstring for the measured evidence: 123 navigation failures + 90
# download failures vs. only 58 real 429s on that crawl).
#
# This is not a workaround for a crawler limitation: Klai already has a
# dedicated document connector for exactly this content class. The web
# crawler's job is web pages; PDFs, spreadsheets, archives and media
# belong to that other connector, not to this one's frontier. Filtering
# them out here, before a single network request is made, costs nothing
# and is deterministic — unlike a HEAD request or content-type sniff.
_NON_HTML_PATH_EXTENSIONS = frozenset(
    {
        # documents
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "odt",
        # archives
        "zip",
        "tar",
        "gz",
        "rar",
        "7z",
        # media
        "jpg",
        "jpeg",
        "png",
        "gif",
        "svg",
        "webp",
        "ico",
        "mp4",
        "mp3",
        "avi",
        "mov",
        "wav",
        # binaries
        "exe",
        "dmg",
        "pkg",
        "deb",
    }
)


def _url_has_non_html_extension(url: str) -> bool:
    """True when ``url``'s PATH (query params ignored) ends in a
    document/archive/media/binary extension — see
    ``_NON_HTML_PATH_EXTENSIONS`` above.

    ``.../rapport.pdf?download=1`` matches: only ``urlparse(url).path`` is
    inspected, so a query string can never hide the extension or trigger a
    false positive on a path segment that merely contains a dot.
    """
    last_segment = urlparse(url).path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return False
    extension = last_segment.rsplit(".", 1)[-1].lower()
    return extension in _NON_HTML_PATH_EXTENSIONS


class _HTMLTextCounter(HTMLParser):
    """Cheap rendered-HTML text signal for deciding whether a crawl was over-pruned."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],  # noqa: ARG002 — stdlib HTMLParser override signature
    ) -> None:
        if tag.lower() in {"script", "style", "noscript", "template", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def _html_text_word_count(html: str) -> int:
    if not html:
        return 0
    parser = _HTMLTextCounter()
    try:
        parser.feed(html)
    except Exception:
        return 0
    return len(unescape(" ".join(parser.parts)).split())


def _should_retry_relaxed_for_thin_content(result: CrawlResult) -> bool:
    return (
        result.success
        and result.word_count < _THIN_CONTENT_WORD_COUNT
        and _html_text_word_count(result.html) >= _THIN_CONTENT_WORD_COUNT
    )


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def build_crawl_config(
    selector: str | None,
    login_indicator_selector: str | None = None,
) -> dict[str, Any]:
    """Build a CrawlerRunConfig-compatible JSON payload.

    Pipeline switching (SPEC-CRAWL-001 / R-1):
    - No selector  → full pipeline (JS chrome removal, excluded_tags, PruningContentFilter)
    - Selector      → trusted pipeline (no JS removal, no excluded_tags, PruningContentFilter)

    Login indicator (SPEC-CRAWLER-004 Fase B / REQ-02.3):
    - When *login_indicator_selector* is set, the caller's base ``wait_for``
      is negated with ``&& !document.querySelector('<selector>')``. If the
      selector matches, the page never satisfies ``wait_for`` and crawl4ai
      returns ``success=False`` after ``page_timeout``. The caller can then
      treat that failure as an auth-wall event.

    No rate-limiting knobs (2026-08-18, corrected after a 2026-08-17 fix
    attempt that added ``semaphore_count`` + ``mean_delay`` here believing
    they were real crawl4ai-enforced pacing). Measured live against the
    running crawl4ai REST server (``/app/api.py:681``): it builds its OWN
    ``MemoryAdaptiveDispatcher`` and passes THAT to ``arun_many`` — it never
    reads ``semaphore_count`` or ``mean_delay`` off ``CrawlerRunConfig``.
    8 URLs configured with ``mean_delay=2.0`` finished in 3.2s instead of
    the predicted 16s, and ``semaphore_count`` 1 vs 8 made no measurable
    difference. Both keys pass crawl4ai's untrusted-config boundary
    (HTTP 200), which is why the original fix looked like it worked in
    testing — the server silently accepts and discards them. Real pacing
    happens client-side in ``_chunked_bulk_fetch`` (burst size via
    ``_burst_size_for`` + inter-chunk sleep), not here.
    """
    md_gen: dict[str, Any] = {
        "type": "DefaultMarkdownGenerator",
        "params": {
            "content_filter": {
                "type": "PruningContentFilter",
                "params": {"threshold": 0.45, "threshold_type": "dynamic"},
            },
            "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
        },
    }

    ready_condition = "(document.body.innerText.trim().split(/\\s+/).length > 50)"
    if login_indicator_selector:
        # Escape quotes/backslashes to prevent JS injection from a stored selector.
        selector_escaped = login_indicator_selector.replace("\\", "\\\\").replace("'", "\\'")
        # Negate: page is only "ready" when base condition is met AND the
        # login indicator is NOT present. When the indicator IS present the
        # wait_for times out and crawl4ai returns success=False.
        ready_condition += f" && !document.querySelector('{selector_escaped}')"

    params: dict[str, Any] = {
        "cache_mode": "bypass",
        "word_count_threshold": 10,
        # Chrome stripping runs inside wait_for's one-time prep (0.9's
        # untrusted-config boundary forbids js_code_before_wait), and only in
        # the no-selector pipeline — with a selector the caller vouches for
        # the content scope, matching the old trusted-pipeline behaviour.
        "wait_for": build_wait_for(strip_chrome=selector is None, ready_condition=ready_condition),
        "remove_consent_popups": True,
        "remove_overlay_elements": True,
        "page_timeout": 30000,
        "markdown_generator": md_gen,
    }

    if selector:
        # Use target_elements instead of css_selector so BFS link discovery still
        # sees the full page DOM. css_selector shrinks the raw HTML before any
        # processing, which also hides sidebar/nav links from BFS — breaking
        # site crawls on wikis where the main <nav> holds all category links
        # (e.g. wiki.redcactus.cloud /nl/ pages = 1 instead of 30+).
        # target_elements only narrows the markdown/extraction pass; the BFS
        # strategy still discovers links from the full HTML.
        params["target_elements"] = [selector]
        params["excluded_tags"] = []
    else:
        # Chrome stripping itself lives in wait_for's prep (see build_wait_for).
        params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]

    return params


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    if settings.crawl4ai_api_key:
        return {"Authorization": f"Bearer {settings.crawl4ai_api_key}"}
    return {}


async def _fetch_sitemap_urls(base_url: str) -> list[str]:
    """Fetch same-site URLs from sitemap.xml or sitemap-index.xml.

    Best-effort — returns [] on any error (sitemap is optional). The
    caller decides whether absent sitemap is fatal or fallback-worthy
    (SPEC-INGEST-RECONCILE-001 AC-3 requires fallback to BFS-only,
    not crawl failure).

    ``base_url`` may be a scoped start URL such as ``/blog``. Sitemaps live
    at the site root, so discovery probes the origin root rather than
    ``{start_url}/sitemap.xml``. Sitemap indexes are followed one level
    recursively. Apex/www variants are treated as the same site and returned
    URLs are coerced back to the configured base host, preserving connector
    identity and avoiding duplicate no-www/www artifacts.
    """
    parsed = urlparse(base_url)
    base_domain = parsed.netloc.lower()
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")
    sitemap_urls = [f"{origin}/sitemap.xml", f"{origin}/sitemap-index.xml"]
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            seen: set[str] = set()
            for sitemap_url in sitemap_urls:
                urls = await _fetch_sitemap_document(
                    client,
                    sitemap_url=sitemap_url,
                    base_domain=base_domain,
                    seen_sitemaps=seen,
                    depth=0,
                )
                if urls:
                    return urls
            return []
    except Exception as exc:
        # AC-3: log unavailability at warning level; caller falls back.
        logger.warning(
            "crawl_discovery_sitemap_unavailable",
            sitemap_url=",".join(sitemap_urls),
            error=str(exc),
        )
        return []


async def _fetch_sitemap_document(
    client: httpx.AsyncClient,
    *,
    sitemap_url: str,
    base_domain: str,
    seen_sitemaps: set[str],
    depth: int,
) -> list[str]:
    """Fetch one sitemap document and return same-site URL entries."""
    if depth > 2:
        return []
    canonical_sitemap = _canonicalise_url(sitemap_url)
    if canonical_sitemap in seen_sitemaps:
        return []
    seen_sitemaps.add(canonical_sitemap)

    try:
        resp = await client.get(sitemap_url, headers=_auth_headers())
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "crawl_discovery_sitemap_unavailable",
            sitemap_url=sitemap_url,
            error=str(exc),
        )
        return []

    kind, locs = _parse_sitemap_locs(resp.text)
    if kind == "sitemapindex":
        urls: list[str] = []
        for loc in locs[:50]:
            if not _same_site_domain(urlparse(loc).netloc.lower(), base_domain):
                continue
            urls.extend(
                await _fetch_sitemap_document(
                    client,
                    sitemap_url=loc,
                    base_domain=base_domain,
                    seen_sitemaps=seen_sitemaps,
                    depth=depth + 1,
                )
            )
        return urls

    return [
        _coerce_same_site_url_to_base_host(loc, base_domain)
        for loc in locs
        if _same_site_domain(urlparse(loc).netloc.lower(), base_domain)
    ]


def _parse_sitemap_locs(xml_text: str) -> tuple[str, list[str]]:
    """Parse a sitemap or sitemap-index XML document."""
    root_match = re.search(r"<\s*([A-Za-z_:][\w:.-]*)\b", xml_text or "")
    root_name = root_match.group(1).split(":")[-1].lower() if root_match else "urlset"
    kind = "sitemapindex" if root_name == "sitemapindex" else "urlset"
    locs = [unescape(u.strip()) for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text or "")]
    return kind, [u for u in locs if u]


# SPEC-INGEST-RECONCILE-001 — URL canonicalisation for set-union dedup.
# Trailing slash + fragment + scheme/host casing are common false-negatives
# in the previous "exact-string match" supplement loop dedup (Bug A defect 3).
def _canonicalise_url(url: str) -> str:
    """Normalise a URL for set-union dedup.

    - Lowercase scheme + host
    - Strip URL fragment (#section)
    - Strip trailing slash from path (but keep root "/" as-is)

    Query string is preserved — different ?foo=bar variants are
    different pages by convention.
    """
    if not url:
        return url
    defragged, _ = urldefrag(url)
    parsed = urlparse(defragged)
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            parsed.query,
            "",  # fragment
        )
    )


def _host_key(host: str) -> str:
    """Return a comparison key that treats apex and www as the same site."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site_domain(host: str, base_domain: str) -> bool:
    return _host_key(host) == _host_key(base_domain)


def _coerce_same_site_url_to_base_host(url: str, base_domain: str) -> str:
    """Use the connector's configured host for apex/www sitemap variants."""
    parsed = urlparse(url)
    if not _same_site_domain(parsed.netloc.lower(), base_domain):
        return url
    return urlunparse(
        (
            parsed.scheme,
            base_domain,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


_ARTICLE_OG_TYPES = {"article"}
_ARTICLE_JSONLD_TYPES = {
    "Article",
    "BlogPosting",
    "NewsArticle",
    "TechArticle",
    "Report",
    "ScholarlyArticle",
}
_LISTING_JSONLD_TYPES = {
    "CollectionPage",
    "SearchResultsPage",
    "ItemList",
    "Blog",
}
_ARCHIVE_PATH_SEGMENTS = {
    "tag",
    "tags",
    "category",
    "categories",
    "author",
    "authors",
    "archive",
    "archives",
    "search",
}


def _meta_contents(html_text: str, *, attr_name: str, attr_value: str) -> list[str]:
    """Extract meta tag content values by name/property/http-equiv."""
    values: list[str] = []
    for match in re.finditer(r"<meta\s+[^>]*>", html_text or "", flags=re.IGNORECASE):
        tag = match.group(0)
        attrs = {
            key.lower(): unescape(value)
            for key, _quote, value in re.findall(
                r"([:\w-]+)\s*=\s*(['\"])(.*?)\2",
                tag,
                flags=re.IGNORECASE | re.DOTALL,
            )
        }
        if attrs.get(attr_name.lower(), "").lower() == attr_value.lower():
            content = attrs.get("content", "").strip()
            if content:
                values.append(content)
    return values


def _og_type(result: CrawlResult) -> str | None:
    metadata = result.metadata or {}
    for key in ("og:type", "og_type"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    values = _meta_contents(result.html, attr_name="property", attr_value="og:type")
    return values[0].strip().lower() if values else None


def _robots_values(result: CrawlResult) -> list[str]:
    values: list[str] = []
    headers = result.response_headers or {}
    for key, value in headers.items():
        if key.lower() == "x-robots-tag" and value:
            values.append(str(value))
    metadata = result.metadata or {}
    robots = metadata.get("robots")
    if isinstance(robots, str) and robots.strip():
        values.append(robots)
    values.extend(_meta_contents(result.html, attr_name="name", attr_value="robots"))
    return values


def _has_noindex(result: CrawlResult) -> bool:
    for value in _robots_values(result):
        directives = {part.strip().lower() for part in value.split(",")}
        if "noindex" in directives:
            return True
    return False


def _jsonld_types(result: CrawlResult) -> set[str]:
    types: set[str] = set()
    scripts = re.findall(
        r"<script\b[^>]*type\s*=\s*['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
        result.html or "",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _collect(node: Any) -> None:
        if isinstance(node, dict):
            raw_type = node.get("@type")
            if isinstance(raw_type, str):
                types.add(raw_type)
            elif isinstance(raw_type, list):
                types.update(t for t in raw_type if isinstance(t, str))
            for value in node.values():
                _collect(value)
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    for script in scripts:
        try:
            _collect(json.loads(unescape(script).strip()))
        except Exception as exc:
            logger.debug("crawl_jsonld_parse_failed", url=result.url, error=str(exc))
            continue
    return types


def _has_article_metadata(result: CrawlResult) -> bool:
    if _og_type(result) in _ARTICLE_OG_TYPES:
        return True
    return bool(_jsonld_types(result) & _ARTICLE_JSONLD_TYPES)


def _path_segments(url: str) -> list[str]:
    return [segment for segment in urlparse(url).path.split("/") if segment]


def _looks_like_archive_url(url: str) -> bool:
    return bool(set(_path_segments(url)) & _ARCHIVE_PATH_SEGMENTS)


def _looks_like_link_listing(result: CrawlResult) -> bool:
    if _og_type(result) != "website":
        return False
    internal_links = (result.links or {}).get("internal") or []
    if len(internal_links) < 12:
        return False
    segments = _path_segments(result.url)
    if _looks_like_archive_url(result.url):
        return True
    # Section roots such as /blog or /resources that mostly list children.
    return len(segments) == 1 and len(internal_links) >= 20


def _is_non_content_listing_page(result: CrawlResult) -> bool:
    """Return True for crawlable pages that should not become KB documents."""
    if not result.success:
        return False
    if _has_noindex(result):
        return True
    if _has_article_metadata(result):
        return False
    jsonld_types = _jsonld_types(result)
    if jsonld_types & _LISTING_JSONLD_TYPES:
        return True
    return _looks_like_link_listing(result)


def _classify_fetch_outcome(
    page_result: dict[str, Any] | None,
    *,
    error: BaseException | None = None,
) -> str:
    """Map a crawl4ai per-URL result (or transport exception) to FetchReasonCode.

    Stable mapping — used to populate ``crawl_jobs.fetch_outcomes`` JSONB
    keyed by FetchReasonCode value (AC-4, AC-11). New cases here MUST
    correspond to an existing enum member; if none fits, add one to
    ``reason_codes.py`` AND extend the migration's allowed set first.
    """
    if error is not None:
        # 2026-08-14 (intermedia.com): a raised httpx.HTTPStatusError is
        # classified by its HTTP status code, honestly — never guessed via
        # a "blocked by anti-bot protection" body match. That match lived
        # here from PR #945 until this fix and never actually fired in
        # production: crawl4ai's real bulk-request 500 body is opaque
        # (``{"error":"Internal server error","correlation_id":"..."}"``),
        # so the anti-bot cause was never diagnosable client-side. See
        # ``_is_recoverable_bulk_failure`` for the cause-independent trigger
        # that replaced this, and ``_is_antibot_block_error`` (kept only for
        # ``_is_minimal_content_antibot_error``) for the retired approach.
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            # 2026-08-17 (intermedia.com rate-limit incident): crawl4ai can
            # wrap a target-site 429 inside its own wrapper status/body —
            # e.g. "Blocked by anti-bot protection: HTTP 429 Too Many
            # Requests" — regardless of the wrapper's own transport status
            # code. Checked before the status-code branches below so a
            # rate-limit signal is honoured even when the wrapper reports a
            # different status, and drives the sequential-recovery breaker
            # (see _recover_bulk_5xx_batch) to stop instead of continuing
            # to hammer a site that already told us to back off. This is
            # narrower than the retired generic anti-bot body match above
            # (which never fired in production) — it only matches the
            # specific 429 / "too many requests" signal, not any anti-bot
            # wording.
            body = error.response.text.lower()
            if "429" in body or "too many requests" in body:
                return FetchReasonCode.RATE_LIMITED.value
            if status_code == 429:
                return FetchReasonCode.RATE_LIMITED.value
            if status_code in (401, 403):
                return FetchReasonCode.AUTH_ERROR.value
            if 400 <= status_code < 500:
                return FetchReasonCode.HTTP_4XX.value
            if 500 <= status_code < 600:
                return FetchReasonCode.HTTP_5XX.value
        msg = str(error).lower()
        if isinstance(error, httpx.TimeoutException) or "timeout" in msg:
            return FetchReasonCode.TIMEOUT.value
        if isinstance(error, httpx.ConnectError) or "connection" in msg:
            return FetchReasonCode.CONNECTION_ERROR.value
        if "name or service not known" in msg or "dns" in msg or "nodename" in msg:
            return FetchReasonCode.DNS_ERROR.value
        return FetchReasonCode.UNKNOWN_EXCEPTION.value

    if not page_result:
        return FetchReasonCode.UNKNOWN_EXCEPTION.value

    if page_result.get("success"):
        return FetchReasonCode.SUCCESS.value

    status = page_result.get("status_code")
    err_msg = (page_result.get("error_message") or "").lower()

    # 2026-08-17 (intermedia.com rate-limit incident): crawl4ai wraps a real
    # 429 inside the SAME "Blocked by anti-bot protection: ..." prefix used
    # for genuine anti-bot challenges — e.g. "Blocked by anti-bot
    # protection: HTTP 429 Too Many Requests". Checked BEFORE the generic
    # anti-bot marker below so a rate-limit signal drives the
    # sequential-recovery breaker (see _recover_bulk_5xx_batch) instead of
    # being treated as an ordinary anti-bot block worth retrying.
    if "429" in err_msg or "too many requests" in err_msg:
        return FetchReasonCode.RATE_LIMITED.value

    # Checked before the status-code branches: crawl4ai can return HTTP 200
    # with results[i].success=false and this marker inline in error_message —
    # a real, confirmed shape (unlike the transport-exception body match
    # retired above 2026-08-14). Applies to the bulk per-page result shape
    # and the seed/single-page synthesized shape built in
    # _build_outcome_from_result.
    if "blocked by anti-bot protection" in err_msg:
        return FetchReasonCode.BLOCKED_ANTI_BOT.value

    if status == 429:
        return FetchReasonCode.RATE_LIMITED.value
    if status in (401, 403):
        return FetchReasonCode.AUTH_ERROR.value
    if isinstance(status, int) and 400 <= status < 500:
        return FetchReasonCode.HTTP_4XX.value
    if isinstance(status, int) and 500 <= status < 600:
        return FetchReasonCode.HTTP_5XX.value

    if "timeout" in err_msg:
        return FetchReasonCode.TIMEOUT.value
    if "dns" in err_msg or "name or service not known" in err_msg:
        return FetchReasonCode.DNS_ERROR.value
    if "connection" in err_msg:
        return FetchReasonCode.CONNECTION_ERROR.value
    if "parse" in err_msg or "decode" in err_msg or "html" in err_msg:
        return FetchReasonCode.PARSE_ERROR.value

    return FetchReasonCode.UNKNOWN_EXCEPTION.value


# ---------------------------------------------------------------------------
# Fetch-failure evidence (SPEC-CRAWLER-FAILURE-EVIDENCE)
#
# ``reason_code`` alone collapses every fetch failure that doesn't map to a
# recognised status/keyword straight into UNKNOWN_EXCEPTION — production's
# single largest failure category (349 occurrences across 23 jobs, all-time —
# more jobs than any other non-success reason). These helpers attach the
# underlying exception type, a truncated+sanitised error message, and a
# crawl4ai ``correlation_id`` (when the failure body carries one) to every
# outcome, so an UNKNOWN_EXCEPTION entry is diagnosable instead of a dead
# end. Purely additive to the existing ``{url, reason_code, status_code,
# content_length}`` shape — see ``_with_evidence``.
# ---------------------------------------------------------------------------

# Hard cap on how much raw error text is persisted per outcome into
# crawl_jobs.fetch_outcomes JSONB. Bounds storage/log volume and rules out
# ever writing an entire HTML error page (or a URL carrying an auth token)
# into the database unbounded. 300 chars keeps an exception message or a
# crawl4ai error body legible while staying well clear of "content".
_ERROR_MESSAGE_MAX_LEN = 300
_TRUNCATION_MARKER = "…[truncated]"

# Redact common secret-bearing query-param VALUES before anything is
# truncated and persisted. A failing URL can be echoed back verbatim inside
# crawl4ai's own error body, and that URL may carry an auth/session token —
# fetch_outcomes goes into the database and into logs, so the token must
# never survive into either.
_SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"(?i)\b(token|access_token|api[_-]?key|secret|password|auth)=([^&\s\"'<>]+)"
)

# crawl4ai's opaque bulk-5xx body is
# ``{"error": "Internal server error", "correlation_id": "188834187d7d"}``
# (see the module comment on ``_classify_fetch_outcome``) — the only handle
# operators have to cross-reference a failure against crawl4ai's own logs.
_CORRELATION_ID_RE = re.compile(r'correlation_id"?\s*[:=]\s*"?([a-zA-Z0-9_-]+)"?', re.IGNORECASE)

_NO_EVIDENCE: dict[str, Any] = {
    "error_type": None,
    "error_message": None,
    "correlation_id": None,
}


def _mask_sensitive_query_params(text: str) -> str:
    """Redact token/secret/password/api-key/auth query-param VALUES."""
    return _SENSITIVE_QUERY_PARAM_RE.sub(lambda m: f"{m.group(1)}=***", text)


def _truncate_error_message(text: str, *, max_len: int = _ERROR_MESSAGE_MAX_LEN) -> str:
    """Sanitise then bound raw error text before it is persisted.

    Masking runs BEFORE truncation so a secret sitting near the cut point
    cannot survive partially exposed.
    """
    masked = _mask_sensitive_query_params(text.strip())
    if len(masked) <= max_len:
        return masked
    keep = max(max_len - len(_TRUNCATION_MARKER), 0)
    return masked[:keep].rstrip() + _TRUNCATION_MARKER


def _extract_correlation_id(text: str) -> str | None:
    """Pull crawl4ai's ``correlation_id`` out of an error body, when present."""
    match = _CORRELATION_ID_RE.search(text)
    return match.group(1) if match else None


def _error_type_name(error: BaseException) -> str:
    """Stable ``module.ClassName`` label — e.g. ``httpx.ReadTimeout``.

    Builtin exceptions (``RuntimeError``, ...) report just the class name,
    since ``builtins.RuntimeError`` adds nothing a reader doesn't already
    know.
    """
    cls = type(error)
    module = cls.__module__
    if module in ("builtins", "__main__"):
        return cls.__qualname__
    return f"{module}.{cls.__qualname__}"


def _raw_error_text(error: BaseException) -> str:
    """Prefer the HTTP response body over ``str(error)``.

    crawl4ai's own error detail (including ``correlation_id``) lives in the
    response body, not in httpx's exception message — ``str(HTTPStatusError)``
    is just ``"crawl4ai failed"`` regardless of what the server actually said.
    """
    if isinstance(error, httpx.HTTPStatusError):
        try:
            body = error.response.text
        except Exception:
            body = ""
        if body:
            return body
    return str(error)


def _evidence_from_exception_text(*, error_type: str | None, raw_text: str) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "error_message": _truncate_error_message(raw_text) if raw_text else None,
        "correlation_id": _extract_correlation_id(raw_text) if raw_text else None,
    }


def _evidence_from_exception(error: BaseException) -> dict[str, Any]:
    """Evidence bundle for a fetch whose Python exception is directly in hand."""
    return _evidence_from_exception_text(
        error_type=_error_type_name(error), raw_text=_raw_error_text(error)
    )


def _evidence_from_page_result(
    page_result: dict[str, Any] | None, *, reason_code: str
) -> dict[str, Any]:
    """Evidence for a crawl4ai page-level failure (``success: false``) with
    no raised Python exception — ``error_type`` falls back to the
    crawl4ai-side failure category (its own ``reason_code``) since there is
    no exception class to report. A page carrying ``success: true`` (even
    when ``reason_code`` was overridden to NON_CONTENT_LISTING_PAGE) is not
    a failure — no evidence to report."""
    if not page_result or page_result.get("success"):
        return dict(_NO_EVIDENCE)
    raw_text = str(page_result.get("error_message") or "")
    if not raw_text:
        return {
            "error_type": f"crawl4ai:{reason_code}",
            "error_message": None,
            "correlation_id": None,
        }
    return {
        "error_type": f"crawl4ai:{reason_code}",
        "error_message": _truncate_error_message(raw_text),
        "correlation_id": _extract_correlation_id(raw_text),
    }


def _evidence_for_crawl_result(result: CrawlResult, *, reason_code: str) -> dict[str, Any]:
    """Evidence for a CrawlResult from the single-page fetch path
    (``_fetch_seed_page`` / ``_crawl_page_with_config``) — shared by the seed
    fetch and the sequential bulk-5xx/timeout recovery loop.

    Prefers ``result.raw_error_text``/``result.error_type`` (the exception
    caught at the transport boundary, before it was collapsed into the bare
    ``error_message`` string) when present; falls back to classifying
    crawl4ai's own reported ``error_message`` otherwise.
    """
    if result.success:
        return dict(_NO_EVIDENCE)
    if result.raw_error_text is not None:
        return _evidence_from_exception_text(
            error_type=result.error_type, raw_text=result.raw_error_text
        )
    return _evidence_from_page_result(
        {"error_message": result.error_message or ""}, reason_code=reason_code
    )


def _with_evidence(
    outcome: FetchOutcome, evidence: dict[str, Any], *, observed: bool
) -> FetchOutcome:
    """Merge evidence fields into an outcome dict — additive to the existing
    ``{url, reason_code, status_code, content_length}`` shape.

    ``observed`` answers one narrow question: for THIS url, did we get our
    own individual result back, or is the outcome a label we assigned
    without one?

    ``True`` — a per-URL result exists for this exact URL: a page-level
    response from a successfully transported bulk request (even a failed
    one — 404, 500, ...), or a single-page fetch via
    ``_crawl_page_with_config``/the seed path.

    ``False`` — no per-URL result exists. This includes a chunk-level
    transport failure attributed to every URL in that chunk: the CHUNK's
    request genuinely failed, but that is one observation about the
    request, not N observations about N URLs. Also false for anything we
    never individually sent (budget/circuit-breaker/deadline abandonment,
    a previous rate-limit stop signal) and for a bulk response with no
    matching entry for this candidate.

    Why this matters: the field exists so a question like "how many times
    did intermedia.com actually give us a timeout?" has a real answer.
    Counting a chunk of 20 URLs that failed together as 20 observations
    inflates that count 20x — exactly the polluted-counter failure mode
    this field was added to prevent. Do not "simplify" this to
    `has an error is observed` without re-reading this comment; the whole
    point is that a URL can have reason_code=TIMEOUT and evidence attached
    while still being unobserved, because the evidence describes the
    request, not a confirmed per-URL result.
    """
    outcome.update(evidence)
    outcome["observed"] = observed
    return outcome


async def _crawl_sync(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit a crawl to POST /crawl and return the synchronous response.

    POST /crawl is a synchronous endpoint — it blocks until crawling is
    complete and returns results directly (no task_id, no polling needed).
    """
    resp = await client.post(
        f"{settings.crawl4ai_api_url}/crawl",
        json=payload,
        headers=_auth_headers(),
    )
    resp.raise_for_status()
    return resp.json()


# Unrendered client-side template token: ``{{item.Name}}``, ``{{ x }}``.
# Left behind by AngularJS/Vue/Handlebars pages whose app failed to render
# in the crawler browser — the tokens are markup residue, never content.
_MUSTACHE_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")
_MD_LINK_URL_RE = re.compile(r"\]\([^)]*\)")


def strip_unrendered_template_lines(markdown: str) -> str:
    """Drop lines that are (almost) entirely unrendered ``{{...}}`` tokens.

    Conservative by design: a line is removed only when, after taking out
    template tokens and markdown link URLs, at most two words remain — i.e.
    the line carries no prose of its own. Lines with real prose that merely
    mention a token are kept UNCHANGED (think documentation about
    templating), as is everything inside fenced code blocks.

    Keep in sync with the same-named helper in
    ``klai-portal/backend/app/services/source_extractors/url.py``.
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
        # Words that remain once tokens and link URLs are gone — the text a
        # human would actually read on this line.
        remainder = _MUSTACHE_TOKEN_RE.sub(" ", _MD_LINK_URL_RE.sub("]", line))
        words = re.findall(r"\w+", remainder, flags=re.UNICODE)
        if len(words) <= 2:
            continue
        kept.append(line)
    return "\n".join(kept)


def _extract_result(url: str, page: dict[str, Any]) -> CrawlResult:
    """Parse a single page result from the REST API response."""
    md = page.get("markdown", "")
    if isinstance(md, dict):
        fit = md.get("fit_markdown", "") or ""
        raw = md.get("raw_markdown", "") or ""
    else:
        fit = ""
        raw = md or ""

    md_v2 = page.get("markdown_v2", {})
    if not fit:
        fit = md_v2.get("fit_markdown", "") or ""
    if not raw:
        raw = md_v2.get("raw_markdown", "") or ""

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
        links=page.get("links", {}),
        media=page.get("media") or {},
        error_message=page.get("error_message"),
        metadata=page.get("metadata"),
        response_headers=page.get("response_headers"),
        status_code=page.get("status_code"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_browser_config_with_cookies(
    cookies: list[dict[str, Any]] | None,
    *,
    stealth: bool = False,
) -> dict[str, Any] | None:
    """Build a BrowserConfig payload that injects cookies natively at browser
    context creation.

    ``stealth=True`` additionally turns on crawl4ai's own ``enable_stealth``
    and a randomised user agent. Both are shipped crawl4ai features and both
    pass its untrusted-config boundary (``magic``, ``simulate_user`` and
    ``override_navigator`` do NOT — the server rejects those with HTTP 400).
    Reserved for the escalation path in ``crawl_site``: it is not the default
    because a randomised UA can change what a site serves, and every crawl
    that works today does so without it.

    Why not the ``on_page_context_created`` hook we used before? The hook
    pattern has known timing issues — Playwright #26786 (cookies added before
    goto can appear empty after load) and crawl4ai #322 (hook actions don't
    always propagate to ``crawler.arun``). crawl4ai's own docs explicitly
    recommend identity-based crawling over hooks for "robust auth":

      "Run your initial login steps in a separate, well-defined process,
      then feed that session to your main crawl — rather than shoehorning
      complex authentication into early hooks."

    crawl4ai 0.8.x's ``BrowserConfig`` accepts a ``cookies`` list directly
    (async_configs.py line 634). The Docker REST API server deserializes it
    via ``BrowserConfig.load`` (api.py line 567), so the same payload shape
    works in our deployed setup.

    Returns ``None`` when no cookies are provided, so callers can do:

        bc = _build_browser_config_with_cookies(cookies)
        if bc:
            payload["browser_config"] = bc
    """
    params: dict[str, Any] = {}
    if cookies:
        params["cookies"] = cookies
    if stealth:
        params["enable_stealth"] = True
        params["user_agent_mode"] = "random"
    if not params:
        return None
    return {
        "type": "BrowserConfig",
        "params": params,
    }


async def crawl_page(
    url: str,
    selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
    retry_relaxed_on_thin: bool = False,
) -> CrawlResult:
    """Crawl a single page via the Crawl4AI REST API.

    Uses the same pipeline switching as klai-connector (SPEC-CRAWL-001).
    When cookies are provided, they are injected natively via
    ``BrowserConfig.cookies`` so Playwright's BrowserContext receives them
    before the page navigation starts.
    """
    config = build_crawl_config(selector)
    result = await _crawl_page_with_config(
        url,
        config,
        cookies=cookies,
        selector=selector,
    )
    if (
        retry_relaxed_on_thin
        and selector is None
        and _should_retry_relaxed_for_thin_content(result)
    ):
        logger.info(
            "crawl_page_retry_relaxed_config",
            url=url,
            word_count=result.word_count,
            html_words=_html_text_word_count(result.html),
        )
        relaxed_result = await _crawl_page_with_config(
            url,
            _relax_seed_crawl_config(config),
            cookies=cookies,
            selector=selector,
            relaxed=True,
        )
        if relaxed_result.word_count > result.word_count:
            return relaxed_result
    return result


async def _crawl_page_with_config(
    url: str,
    crawler_config: dict[str, Any],
    *,
    cookies: list[dict[str, Any]] | None,
    selector: str | None,
    relaxed: bool = False,
    stealth: bool = False,
    timeout: float = 90.0,
) -> CrawlResult:
    """Fetch a single page via ``POST /crawl``.

    ``timeout`` defaults to 90.0s, the right ceiling for the seed/single-page
    callers (``crawl_page``). The sequential bulk-5xx recovery path
    (``_recover_bulk_5xx_batch``) passes a longer, configurable timeout
    (``settings.crawl_sequential_recovery_timeout_seconds``) — see that
    setting's docstring in config.py for why 90s is too short there.
    """
    payload: dict[str, Any] = {
        "urls": [url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_config},
    }
    bc = _build_browser_config_with_cookies(cookies, stealth=stealth)
    if bc:
        payload["browser_config"] = bc

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            data = await _crawl_sync(client, payload)
        except Exception as exc:
            logger.warning("crawl4ai_request_failed", url=url, error=str(exc))
            return CrawlResult(
                url=url,
                fit_markdown="",
                raw_markdown="",
                html="",
                word_count=0,
                success=False,
                error_message=str(exc),
                status_code=_status_code_from_exception(exc),
                error_type=_error_type_name(exc),
                raw_error_text=_raw_error_text(exc),
            )

    results = data.get("results", [])
    if isinstance(results, dict):
        results = [results]

    if not results:
        return CrawlResult(
            url=url,
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="No results returned",
        )

    result = _extract_result(url, results[0])
    logger.info(
        "crawl4ai_page_result",
        url=url,
        selector=selector,
        relaxed=relaxed,
        fit_words=len(result.fit_markdown.split()),
        raw_words=len(result.raw_markdown.split()),
    )
    return result


class CrawlLedger:
    """Deterministic crawl frontier + audit ledger."""

    def __init__(
        self,
        *,
        start_url: str,
        base_domain: str,
        include_patterns: list[str] | None,
        exclude_patterns: list[str] | None,
        max_depth: int,
    ) -> None:
        self.start_url = start_url
        self.base_domain = base_domain
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns
        self.max_depth = max_depth
        self._by_canonical: dict[str, DiscoveredUrl] = {}
        self._order = 0

    @property
    def discovered_count(self) -> int:
        return len(self._by_canonical)

    def add_start(self) -> None:
        self.add(
            self.start_url,
            depth=0,
            discovered_from=None,
            source_kind="start",
            priority=_PRIORITY_START,
            force=True,
        )

    def add_sitemap_urls(self, urls: list[str]) -> None:
        for url in urls:
            self.add(
                url,
                depth=1,
                discovered_from=None,
                source_kind="sitemap",
                priority=_PRIORITY_SITEMAP,
            )

    def add_links_from_result(self, result: CrawlResult, *, source_depth: int) -> None:
        internal = (result.links or {}).get("internal") or []
        source_listing = _is_listing_source(result)
        for entry in internal:
            href = entry.get("href") if isinstance(entry, dict) else None
            if not href:
                continue
            priority = (
                _PRIORITY_LISTING_CHILD if source_listing else _priority_for_discovered_url(href)
            )
            self.add(
                href,
                depth=source_depth + 1,
                discovered_from=result.url,
                source_kind="page_link",
                priority=priority,
            )

    def add(
        self,
        url: str,
        *,
        depth: int,
        discovered_from: str | None,
        source_kind: DiscoverySourceKind,
        priority: int,
        force: bool = False,
    ) -> bool:
        if not url:
            return False
        if not _same_site_domain(urlparse(url).netloc.lower(), self.base_domain):
            return False
        if _url_has_non_html_extension(url):
            # Deliberately produces NO outcome at all (same contract as the
            # domain check above) — never a ``not_fetched_*`` reason code.
            # A URL we correctly never wanted to crawl is not incomplete
            # coverage; see _build_crawl_outcome_warning / _crawl_fully_fetched
            # in adapters/crawler.py, which both key off any not_fetched_*
            # prefix and would otherwise mark the whole crawl failed_partial
            # over one PDF link.
            return False
        url = _coerce_same_site_url_to_base_host(url, self.base_domain)
        if not force:
            if not _url_matches_include_patterns(url, self.include_patterns):
                return False
            if _url_matches_patterns(url, self.exclude_patterns):
                return False
        canonical = _canonicalise_url(url)
        existing = self._by_canonical.get(canonical)
        if existing is not None:
            if depth < existing.depth:
                existing.depth = depth
                existing.discovered_from = discovered_from
                existing.source_kind = source_kind
            existing.priority = min(existing.priority, priority)
            return False
        self._order += 1
        self._by_canonical[canonical] = DiscoveredUrl(
            url=url,
            canonical_url=canonical,
            depth=depth,
            discovered_from=discovered_from,
            source_kind=source_kind,
            priority=priority,
            order=self._order,
        )
        return True

    def next_batch(self, *, remaining_budget: int) -> list[str]:
        if remaining_budget <= 0:
            return []
        queued = [
            item
            for item in self._by_canonical.values()
            if item.status == "queued" and item.depth <= self.max_depth
        ]
        queued.sort(key=lambda i: (i.priority, i.depth, i.order))
        return [item.url for item in queued[:remaining_budget]]

    def mark_outcome(self, outcome: FetchOutcome) -> None:
        item = self._by_canonical.get(_canonicalise_url(str(outcome.get("url") or "")))
        if item is None:
            return
        item.status = "fetched"
        item.reason_code = str(outcome.get("reason_code") or "")

    def depth_for_url(self, url: str) -> int | None:
        item = self._by_canonical.get(_canonicalise_url(url))
        return item.depth if item else None

    def mark_unfetched(self, *, fetched_count: int, max_pages: int) -> None:
        budget_exhausted = fetched_count >= max_pages
        for item in self._by_canonical.values():
            if item.status != "queued":
                continue
            item.status = "omitted"
            if item.depth > self.max_depth:
                item.reason_code = FetchReasonCode.NOT_FETCHED_DEPTH_LIMIT.value
            elif budget_exhausted:
                item.reason_code = FetchReasonCode.NOT_FETCHED_BUDGET_EXHAUSTED.value
            else:
                item.reason_code = FetchReasonCode.NOT_FETCHED_DISCOVERY_LIMIT.value

    def omitted_outcomes(self) -> list[FetchOutcome]:
        omitted = [
            item
            for item in self._by_canonical.values()
            if item.status == "omitted" and item.reason_code
        ]
        omitted.sort(key=lambda i: (i.priority, i.depth, i.order))
        # Deliberate scheduling omissions (budget/depth/discovery limit) —
        # never attempted over the network, so never observed.
        return [
            _with_evidence(
                {
                    "url": item.url,
                    "reason_code": item.reason_code,
                    "status_code": None,
                    "content_length": 0,
                },
                dict(_NO_EVIDENCE),
                observed=False,
            )
            for item in omitted
        ]


def _priority_for_discovered_url(url: str) -> int:
    segments = _path_segments(url)
    if 1 < len(segments) <= 2:
        return _PRIORITY_SECTION_ROOT
    return _PRIORITY_PAGE_LINK


def _is_listing_source(result: CrawlResult) -> bool:
    internal = (result.links or {}).get("internal") or []
    if len(internal) >= _LISTING_LINK_THRESHOLD:
        return True
    return _is_non_content_listing_page(result)


def _crawl_results_from_raw_results(
    raw_results: list[dict[str, Any]],
    *,
    base_domain: str,
) -> list[CrawlResult]:
    """Parse successful same-domain responses for follow-up link discovery."""
    results: list[CrawlResult] = []
    for page in raw_results:
        if not page:
            continue
        result = _extract_result(page.get("url") or "", page)
        if result.success and _same_site_domain(urlparse(result.url).netloc.lower(), base_domain):
            results.append(result)
    return results


def _is_antibot_block_error(exc: BaseException) -> bool:
    """True when a crawl4ai transport failure's body reports an anti-bot block.

    NOTE (2026-08-14, intermedia.com): this body-text match essentially
    never fires against a real crawl4ai deployment — a live bulk-request
    500 was proven to carry an opaque body
    (``{"error":"Internal server error","correlation_id":"..."}"``), never
    the "blocked by anti-bot protection" marker this function looks for.
    It is kept ONLY because :func:`_is_minimal_content_antibot_error` (a
    narrower, pre-existing single-page/seed thin-content heuristic — see
    its own docstring) still calls it; that call site was out of scope for
    the 2026-08-14 fix. Do NOT use this function for terminal-status or
    sequential-recovery decisions in ``crawl_site`` — see
    :func:`_is_recoverable_bulk_failure` for the cause-independent
    replacement that actually fires in production.
    """
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code != 500:
        return False
    return "blocked by anti-bot protection" in exc.response.text.lower()


def _is_minimal_content_antibot_error(exc: Exception) -> bool:
    """True when a seed-page 500 reports both an anti-bot block AND a
    thin-content marker — used only to decide whether ``_fetch_seed_page``
    should retry the SEED fetch with a relaxed config.

    CAVEAT (2026-08-14): like :func:`_is_antibot_block_error` above, this
    leans on a body-text match that real crawl4ai 500 responses do not
    appear to carry in practice (see the bulk-request evidence there).
    This call site was out of scope for the 2026-08-14 fix and is left
    as-is — flagged here so the next reader does not assume it reliably
    fires either.
    """
    if not isinstance(exc, httpx.HTTPStatusError) or not _is_antibot_block_error(exc):
        return False
    body = exc.response.text.lower()
    return "minimal_text" in body or "no_content_elements" in body or "0 chars visible" in body


def _is_recoverable_bulk_failure(exc: BaseException) -> bool:
    """True when a bulk-crawl transport failure is worth a one-URL-at-a-time
    sequential retry (see ``_recover_bulk_5xx_batch``) — cause-independent
    trigger covering two cases that share the same property: the client-side
    cause is UNKNOWABLE from the transport response alone, so the per-URL
    retry is simultaneously the mitigation and the diagnosis.

    Case 1 — HTTP 5xx (originally the only trigger, named
    ``_is_bulk_5xx_error``). Evidence 2026-08-14 (intermedia.com): crawl4ai's
    bulk ``arun_many`` endpoint fails the WHOLE concurrent batch with one 500
    the moment ANY url in it hits an anti-bot challenge — but the response
    body is opaque
    (``{"error":"Internal server error","correlation_id":"188834187d7d"}"``),
    never a diagnosable reason. ``crawl_site`` treats every bulk-level 5xx as
    worth a one-URL-at-a-time sequential retry: the retry is simultaneously
    the mitigation (some URLs recover — the same intermedia.com incident saw
    ``/products/unite`` succeed seconds after the bulk 500 while
    ``/products/ai`` stayed blocked, via the same single-page path) and the
    diagnosis (the per-URL retry result tells us the REAL per-URL
    reason_code, honestly, instead of a guessed BLOCKED_ANTI_BOT).

    Case 2 — ``httpx.TimeoutException`` (added 2026-08-18,
    fix/bulk-timeout-scales-with-pacing). crawl4ai's own RateLimiter retries
    a 429 up to 3 times server-side with a backoff capped at 60s per retry
    (see ``_CRAWL4AI_RATE_LIMIT_BACKOFF_CEILING_SECONDS`` = 180s worst
    case) before it returns the real result to us — so a bulk-request
    read-timeout can legitimately mean "the server was still working
    through its own 429 backoff when the client gave up" rather than a
    real permanent failure. Evidence 2026-08-17 (intermedia.com, rate_limit=0.5): the
    crawl4ai container log recorded 254 real ``HTTP 429 Too Many Requests``
    responses, while ``fetch_outcomes`` recorded 146 ``timeout`` and 0
    ``rate_limited`` — the fixed bulk httpx timeout fired before crawl4ai's
    own 429 signal could come back, and because a timeout is not an
    ``httpx.HTTPStatusError`` at all, the pre-fix ``_is_bulk_5xx_error``
    check never triggered recovery for it — the whole chunk was written off
    as a bare ``timeout`` batch-wide with no per-URL diagnosis attempted.
    Same remedy as case 1: retry sequentially, and let the per-URL result
    (including a genuine ``RATE_LIMITED``, which correctly stops the
    recovery loop early — see the rate-limit branch in
    ``_recover_bulk_5xx_batch``) tell us the honest reason instead of
    guessing.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return isinstance(exc, httpx.TimeoutException)


def _bulk_failure_trigger_reason_code(exc: BaseException) -> str:
    """Honest default abandon-reason for ``_recover_bulk_5xx_batch``, matching
    what ``crawl_site`` actually observed on the bulk request that triggered
    recovery — never a guessed value. Only meaningful for exceptions that
    pass ``_is_recoverable_bulk_failure``; every other transport failure is
    classified directly by ``_classify_fetch_outcome`` without ever reaching
    the sequential-recovery path.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return FetchReasonCode.HTTP_5XX.value
    return FetchReasonCode.TIMEOUT.value


def _status_code_from_exception(exc: BaseException) -> int | None:
    """Extract the HTTP status code from a transport exception, when known."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def _relax_seed_crawl_config(crawler_config: dict[str, Any]) -> dict[str, Any]:
    relaxed = copy.deepcopy(crawler_config)
    # Some personal/portfolio sites put real content in <header>. If strict
    # chrome stripping leaves no visible text, retry the seed before treating
    # the page as anti-bot. Chrome stripping lives inside wait_for's prep, so
    # dropping wait_for drops the stripping with it.
    relaxed.pop("wait_for", None)
    if relaxed.get("excluded_tags"):
        relaxed["excluded_tags"] = ["script", "style"]
    return relaxed


@dataclass
class LinkedPageSample:
    """Outcome of sampling pages linked from an already-crawled page."""

    pages_crawled: int
    pages_usable: int


def _is_unrendered_template_href(url: str) -> bool:
    """True when an ``<a href>`` is a literal, unrendered template token.

    A client-rendered site whose framework fails to bootstrap in the crawler
    browser leaves its link targets as the template expression itself —
    ``.../{{item.URL}}`` on Oracle RightNow (support.ascendcloud.com), the
    Vue/Handlebars equivalents elsewhere. Those never resolve, so sampling
    them wastes the time budget on URLs that 404 or hang.

    Scoped to the ``{{`` / ``}}`` double-brace token shape on purpose: that is
    specifically a templating marker, not a generic bracket. A lone ``{`` in a
    URL (rare, but it happens) is left alone. Mirrors the double-brace shape
    that ``strip_unrendered_template_lines`` strips from markdown.
    """
    return "{{" in url or "}}" in url


def _sample_candidates(seed: CrawlResult, *, base_domain: str, limit: int) -> list[str]:
    """Pick which linked URLs to sample, deepest-looking pages first.

    A hub links to both sibling hubs (``/support``) and leaf articles
    (``/articles/detail/a_id/123``). Leaves answer "does this site hold real
    content?" far better than more hubs, and they are what a sync would
    actually index — so prefer more path segments, then declaration order for
    a stable, testable result.

    Unfetchable links (unrendered template tokens) are dropped: sampling them
    only burns the time budget and returns nothing.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for href in _extract_bfs_seeds(seed, base_domain=base_domain):
        if _is_unrendered_template_href(href):
            continue
        canonical = _canonicalise_url(href)
        if canonical in seen or canonical == _canonicalise_url(seed.url):
            continue
        seen.add(canonical)
        candidates.append(href)
    candidates.sort(key=lambda u: -len(_path_segments(u)))
    return candidates[:limit]


async def sample_linked_pages(
    seed: CrawlResult,
    *,
    selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
    max_pages: int = 5,
) -> LinkedPageSample:
    """Fetch pages linked from ``seed`` and count how many carry real content.

    Answers "is this SITE worth crawling?" when the seed page alone cannot —
    a navigation hub is thin by nature, yet the pages behind it are exactly
    what a sync would index.

    Deliberately reuses ``seed``'s already-extracted links and issues a
    SINGLE bulk request. Routing this through :func:`crawl_site` instead
    would re-crawl the seed and probe sitemaps first — three sequential
    network phases for a question one parallel batch answers.

    Never raises: a transport failure yields a zero sample so callers can
    fall back to their single-page verdict.
    """
    base_domain = urlparse(seed.url).netloc.lower()
    candidates = _sample_candidates(seed, base_domain=base_domain, limit=max_pages)
    if not candidates:
        return LinkedPageSample(0, 0)

    fetch = await _chunked_bulk_fetch(
        urls=candidates,
        crawler_config=build_crawl_config(selector),
        cookies=cookies,
    )
    # Only candidates whose OWN chunk actually transported are worth
    # combining — a candidate whose chunk failed or was never attempted
    # (A1/A2) has no raw result to match against and must not be
    # misclassified as UNKNOWN_EXCEPTION via a phantom "whole batch failed"
    # transport_error the way the old single-error contract forced.
    ok_candidates = [
        c for c in candidates if c not in fetch.failed and c not in fetch.not_attempted
    ]
    results, _outcomes = _combine_bulk_responses(
        candidates=ok_candidates,
        raw_results=fetch.raw_results,
        transport_error=None,
        base_domain=base_domain,
    )
    usable = sum(1 for r in results if r.word_count >= _THIN_CONTENT_WORD_COUNT)
    logger.info(
        "crawl_sample_linked_pages",
        seed_url=seed.url,
        candidates=len(candidates),
        fetched=len(results),
        usable=usable,
    )
    return LinkedPageSample(pages_crawled=len(results), pages_usable=usable)


async def crawl_site(
    start_url: str,
    selector: str | None = None,
    max_depth: int = 2,
    max_pages: int = 200,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    login_indicator_selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
    rate_limit: float | None = None,
) -> tuple[list[CrawlResult], list[FetchOutcome]]:
    """Crawl a site with a Klai-owned deterministic frontier.

    Crawl4AI renders pages and extracts links; Klai owns URL scheduling.
    Every in-scope discovered URL is either fetched or emitted as a
    ``not_fetched_*`` outcome, so page-budget/depth limits fail loudly.

    ``rate_limit`` (requests/second, optional) paces the bulk-fetch chunks
    client-side — see ``_chunked_bulk_fetch`` / ``_burst_size_for`` for the
    mechanism, and ``build_crawl_config``'s docstring for why crawl4ai's own
    ``CrawlerRunConfig`` pacing knobs (``semaphore_count`` / ``mean_delay``)
    cannot do this: the REST server ignores them.

    Returns ``(crawl_results, outcomes)``:
    - ``crawl_results``: same-domain pages with non-empty markdown.
    - ``outcomes``: per-URL ``{"url", "reason_code", "status_code",
      "content_length"}`` records written to ``crawl_jobs.fetch_outcomes``
      JSONB so operators can answer "where did the missing pages go?"
      without log forensics (RECONCILE's good addition, retained).
    """
    parsed = urlparse(start_url)
    base_domain = parsed.netloc.lower()
    crawler_config = build_crawl_config(
        selector,
        login_indicator_selector=login_indicator_selector,
    )
    ledger = CrawlLedger(
        start_url=start_url,
        base_domain=base_domain,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        max_depth=max_depth,
    )
    ledger.add_start()

    sitemap_urls = await _fetch_sitemap_urls(start_url)
    ledger.add_sitemap_urls(sitemap_urls)

    crawl_results: list[CrawlResult] = []
    outcomes: list[FetchOutcome] = []
    fetched_count = 0
    # Crawl-job-wide budgets for the bulk-5xx sequential-recovery fallback
    # (see _recover_bulk_5xx_batch / _MAX_SEQUENTIAL_RECOVERY). BOTH are
    # shared across every batch iteration below, not reset per batch — a
    # per-batch clock would let a many-batch crawl spend its full wall-clock
    # allowance again and again, which is exactly the "one job holds a worker
    # slot for over an hour" case the budget exists to prevent.
    #
    # sequential_recovery_time_remaining tracks TIME ACTUALLY SPENT INSIDE
    # RECOVERY, not wall-clock time since crawl_site started. A single
    # deadline computed once here (as this used to do) would also count
    # every second spent on unrelated work — client-side rate_limit pacing
    # between bulk chunks (_chunked_bulk_fetch) chief among them — against
    # the recovery budget for free. At a low rate_limit that pacing alone
    # can exceed crawl_sequential_recovery_max_seconds before a single
    # batch has even failed, making recovery structurally unavailable on
    # exactly the low-rate_limit (fragile) sites that need it most. The
    # deadline passed to each _recover_bulk_5xx_batch call below is
    # therefore derived from this remaining budget immediately before that
    # call, and the remaining budget is debited only by the wall-clock time
    # that specific call actually consumed.
    sequential_recovery_budget = _MAX_SEQUENTIAL_RECOVERY
    sequential_recovery_time_remaining = settings.crawl_sequential_recovery_max_seconds

    # Deel B (2026-08-18) — the in-job rate_limit actually used for the NEXT
    # ``_chunked_bulk_fetch`` call. Starts at the caller's ``rate_limit`` and
    # only ever moves DOWN, via ``_lower_rate_limit_for_slowdown``, after an
    # observed RATE_LIMITED stop signal — never persisted, never raised back
    # up within this call (the persisted domain-level AIMD controller in
    # ``domain_rate_limit_control`` owns raising it back up, once, after the
    # job completes, for the NEXT crawl). ``consecutive_rate_limit_slowdowns``
    # bounds how many times in a row that can happen before giving up — see
    # ``_MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS``.
    current_rate_limit = rate_limit
    consecutive_rate_limit_slowdowns = 0

    start_result = await _fetch_seed_page(
        start_url=start_url,
        crawler_config=crawler_config,
        cookies=cookies,
    )
    start_outcome = _build_outcome_from_result(start_url, start_result)
    ledger.mark_outcome(start_outcome)
    outcomes.append(start_outcome)
    fetched_count += 1
    if _result_is_ingestable(
        start_result, base_domain=base_domain
    ) and not _is_non_content_listing_page(start_result):
        crawl_results.append(start_result)
    if start_result.success:
        ledger.add_links_from_result(start_result, source_depth=0)

    while fetched_count < max_pages:
        batch = ledger.next_batch(remaining_budget=max_pages - fetched_count)
        if not batch:
            break

        fetch = await _chunked_bulk_fetch(
            urls=batch,
            crawler_config=crawler_config,
            cookies=cookies,
            rate_limit=current_rate_limit,
        )
        fetched_count += len(batch)

        # A1: URLs whose chunk was never even sent because an earlier chunk
        # in THIS attempt already saw a RATE_LIMITED/BLOCKED_ANTI_BOT
        # signal. Carried through the stealth retry below (which can ALSO
        # stop early on its own chunking) — never retried, never counted
        # as a failure.
        not_attempted_urls: list[str] = list(fetch.not_attempted)
        stop_trigger_reason_code = fetch.stop_trigger_reason_code

        # A2: only the subset of `batch` whose OWN chunk failed to
        # transport is worth a stealth retry — everything else already has
        # a real per-URL result in fetch.raw_results (or was intentionally
        # skipped above) and must not be re-fetched or reclassified.
        retry_urls = [u for u, exc in fetch.failed.items() if _is_recoverable_bulk_failure(exc)]
        if retry_urls:
            # Escalation step 1: retry ONLY the still-failing subset with
            # crawl4ai's stealth mode + a randomised UA. Measured on
            # intermedia.com 2026-08-15: the plain bulk request 500s
            # wholesale, the identical batch with stealth returns 200 with
            # 5 of 6 pages — seconds, not the ~20 minutes the sequential
            # path costs. Stealth is not the default because a randomised
            # UA can change what a site serves, and every crawl that works
            # today works without it; earning it via a failure keeps
            # healthy sites untouched.
            logger.info("crawl_bulk_5xx_stealth_retry", urls=len(retry_urls))
            stealth_fetch = await _chunked_bulk_fetch(
                urls=retry_urls,
                crawler_config=crawler_config,
                cookies=cookies,
                stealth=True,
                rate_limit=current_rate_limit,
            )
            fetch.raw_results.extend(stealth_fetch.raw_results)
            for retried_url in retry_urls:
                fetch.failed.pop(retried_url, None)
            fetch.failed.update(stealth_fetch.failed)
            not_attempted_urls.extend(stealth_fetch.not_attempted)
            if stealth_fetch.stop_trigger_reason_code == FetchReasonCode.BLOCKED_ANTI_BOT.value:
                stop_trigger_reason_code = FetchReasonCode.BLOCKED_ANTI_BOT.value
            elif stop_trigger_reason_code is None:
                stop_trigger_reason_code = stealth_fetch.stop_trigger_reason_code

        # Deel B (2026-08-18, "a stop-signal should slow you down, not give
        # up") — a RATE_LIMITED stop means "you're going too fast", not
        # "give up on these URLs forever". They are left QUEUED in the
        # ledger (never finalised as not_fetched below) so a LATER batch in
        # this same job retries them, at a lowered ``current_rate_limit``,
        # after a short cooldown. BLOCKED_ANTI_BOT is the opposite: no rate
        # exists that fixes a block, so it keeps the old stop-and-give-up
        # behaviour, immediately. A site that keeps rate-limiting through
        # ``_MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS`` halvings in a row is
        # finally given up on too — see that constant's docstring.
        carried_over_urls: list[str] = []
        stop_crawl_after_this_batch = False
        if not_attempted_urls:
            if stop_trigger_reason_code == FetchReasonCode.BLOCKED_ANTI_BOT.value:
                stop_crawl_after_this_batch = True
            elif consecutive_rate_limit_slowdowns < _MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS:
                consecutive_rate_limit_slowdowns += 1
                current_rate_limit = _lower_rate_limit_for_slowdown(current_rate_limit)
                carried_over_urls = not_attempted_urls
                not_attempted_urls = []
                fetched_count -= len(carried_over_urls)
                logger.warning(
                    "crawl_rate_limit_slowdown_retry",
                    carried_over_urls=len(carried_over_urls),
                    new_rate_limit=current_rate_limit,
                    slowdown_count=consecutive_rate_limit_slowdowns,
                )
                await _slowdown_sleep(settings.crawl_rate_limit_slowdown_cooldown_seconds)
            else:
                stop_crawl_after_this_batch = True
                logger.warning(
                    "crawl_rate_limit_slowdown_exhausted",
                    max_slowdowns=_MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS,
                    abandoned_urls=len(not_attempted_urls),
                )
        elif consecutive_rate_limit_slowdowns:
            # A clean batch (no stop signal at all) proves the site
            # recovered — give a LATER rate-limit hit in this job a fresh
            # budget instead of counting toward the give-up threshold
            # forever.
            consecutive_rate_limit_slowdowns = 0

        recovered_results: list[CrawlResult] = []
        recovered_link_source_results: list[CrawlResult] = []
        recovered_outcomes: list[FetchOutcome] = []
        sequential_recovery_urls: list[str] = []
        recoverable_failed = {
            u: exc for u, exc in fetch.failed.items() if _is_recoverable_bulk_failure(exc)
        }
        if recoverable_failed:
            # crawl4ai's bulk endpoint still failed this subset after the
            # stealth retry — either an opaque 5xx or a read-timeout
            # (evidence + budget: see _MAX_SEQUENTIAL_RECOVERY,
            # _is_recoverable_bulk_failure). The client-side cause is
            # unknowable from the transport response alone, so we don't
            # guess it — recover what we can by retrying one URL at a time
            # via the single-page path, which tells us the REAL per-URL
            # reason_code, instead of writing off every URL as
            # unknown_exception (or guessing blocked_anti_bot without
            # evidence). Only the URLs STILL unresolved go here — not the
            # whole original batch (A2): everything the bulk path already
            # resolved, successfully or via a non-recoverable failure, is
            # never touched again.
            #
            # The deadline handed to this call is derived from the
            # job-wide time REMAINING, evaluated right now — not a
            # deadline fixed at crawl_site's start — so that only the time
            # this call itself spends recovering is ever charged against
            # the budget (see sequential_recovery_time_remaining above).
            sequential_recovery_urls = list(recoverable_failed.keys())
            recovery_started_at = _recovery_monotonic()
            (
                recovered_results,
                recovered_link_source_results,
                recovered_outcomes,
                recovery_attempted,
            ) = await _recover_bulk_5xx_batch(
                sequential_recovery_urls,
                crawler_config=crawler_config,
                cookies=cookies,
                base_domain=base_domain,
                recovery_budget=sequential_recovery_budget,
                deadline=recovery_started_at + sequential_recovery_time_remaining,
                # Stealth already earned by two consecutive bulk failures;
                # the per-URL retries get it too. Measured: with stealth a
                # failure no longer poisons the session (3 of 5 succeeded
                # back-to-back with no cooldown at all), which is exactly
                # what the cooldown was working around.
                stealth=True,
                # Honest abandon-reason if the recovery budget/deadline/
                # breaker caps out mid-batch: whatever this trigger actually
                # was (5xx or timeout), not a hardcoded 5xx-flavoured guess.
                # `recoverable_failed` may hold more than one distinct
                # exception across its URLs (A2) — this picks the first
                # one, in submission order, as the representative trigger.
                trigger_reason_code=_bulk_failure_trigger_reason_code(
                    next(iter(recoverable_failed.values()))
                ),
            )
            sequential_recovery_budget -= recovery_attempted
            # Debit only the wall-clock time THIS call actually spent
            # recovering — never below zero, so a call that overshoots
            # (the deadline is only checked before each attempt starts,
            # not after) cannot hand the next batch a negative allowance.
            sequential_recovery_time_remaining = max(
                sequential_recovery_time_remaining - (_recovery_monotonic() - recovery_started_at),
                0.0,
            )
            # These URLs' fate is now fully captured in recovered_outcomes
            # (success, still-failed, or abandoned-mid-recovery) — remove
            # them from fetch.failed so they are not ALSO reclassified
            # below from their now-stale pre-recovery exception.
            for recovered_url in sequential_recovery_urls:
                fetch.failed.pop(recovered_url, None)

        # Everything NOT sent to sequential recovery, not abandoned as
        # not-attempted (A1), not carried over for a slower retry (Deel B),
        # and not still failed (non-recoverable transport errors, e.g.
        # DNS/connection/4xx-wrapper) has a real per-URL result sitting in
        # fetch.raw_results.
        excluded_from_combine = (
            set(fetch.failed)
            | set(not_attempted_urls)
            | set(sequential_recovery_urls)
            | set(carried_over_urls)
        )
        ok_urls = [u for u in batch if u not in excluded_from_combine]
        batch_results, batch_outcomes = _combine_bulk_responses(
            candidates=ok_urls,
            raw_results=fetch.raw_results,
            transport_error=None,
            base_domain=base_domain,
        )
        link_source_results = _crawl_results_from_raw_results(
            fetch.raw_results, base_domain=base_domain
        )
        # Non-recoverable failures (DNS/connection/4xx-wrapper errors etc.)
        # never entered the stealth retry or sequential recovery —
        # classify them directly from the exception that failed THEIR
        # chunk (A2: each keeps its own exception, never borrowed).
        for failed_url, failed_exc in fetch.failed.items():
            batch_outcomes.append(_outcome_for_failed_url(failed_url, failed_exc))
        # A1: URLs whose chunk was deliberately never sent.
        for skipped_url in not_attempted_urls:
            batch_outcomes.append(_outcome_for_not_attempted_url(skipped_url))

        batch_results.extend(recovered_results)
        batch_outcomes.extend(recovered_outcomes)
        link_source_results.extend(recovered_link_source_results)
        # Preview↔ingest parity: the single-page preview recovers thin-but-rich
        # pages via a relaxed retry; do the same for BFS pages so site ingest
        # does not silently store them thin. Only when no selector is active
        # (a selector means the thin result is the intended scope).
        if selector is None:
            batch_results = await _recover_thin_bulk_results(
                batch_results,
                crawler_config=crawler_config,
                cookies=cookies,
                base_domain=base_domain,
                rate_limit=current_rate_limit,
            )
        for outcome in batch_outcomes:
            ledger.mark_outcome(outcome)
        outcomes.extend(batch_outcomes)
        crawl_results.extend(batch_results)

        recovered_by_canonical = {_canonicalise_url(r.url): r for r in batch_results}
        for result in link_source_results:
            result = recovered_by_canonical.get(_canonicalise_url(result.url), result)
            source_depth = ledger.depth_for_url(result.url)
            if source_depth is not None and source_depth < max_depth:
                ledger.add_links_from_result(result, source_depth=source_depth)

        if stop_crawl_after_this_batch:
            # BLOCKED_ANTI_BOT, or a RATE_LIMITED signal that survived
            # _MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS halvings — further
            # batches would either not help (a block) or keep paying a
            # cooldown for no progress (an unrecoverable rate limit).
            # Remaining queued URLs get their honest reason via
            # ledger.mark_unfetched below, same as any other early stop.
            break

    ledger.mark_unfetched(fetched_count=fetched_count, max_pages=max_pages)
    omitted_outcomes = ledger.omitted_outcomes()
    outcomes.extend(omitted_outcomes)

    success_count = sum(1 for o in outcomes if o["reason_code"] == FetchReasonCode.SUCCESS.value)
    logger.info(
        "crawl_site_complete",
        start_url=start_url,
        candidates=len(outcomes),
        results=len(crawl_results),
        success_outcomes=success_count,
        non_success_outcomes=len(outcomes) - success_count,
        discovered_urls=ledger.discovered_count,
        omitted_urls=len(omitted_outcomes),
        max_pages=max_pages,
    )

    return crawl_results, outcomes


# ---------------------------------------------------------------------------
# crawl_site internals — kept module-private for testability.
# ---------------------------------------------------------------------------


async def _fetch_seed_page(
    *,
    start_url: str,
    crawler_config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
) -> CrawlResult:
    """Fetch ``start_url`` once with the SAME config the bulk path uses.

    Distinct from ``crawl_page`` because crawl_page rebuilds config from
    ``selector`` only — it has no knob for ``login_indicator_selector``,
    so reusing it for the seed would silently strip auth-wall detection
    and let the seed succeed on a login page (extracting login-form
    anchors as BFS seeds, then auth-failing every URL in the bulk).
    """
    payload: dict[str, Any] = {
        "urls": [start_url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_config},
    }
    bc = _build_browser_config_with_cookies(cookies)
    if bc:
        payload["browser_config"] = bc

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            try:
                data = await _crawl_sync(client, payload)
            except Exception as exc:
                if not _is_minimal_content_antibot_error(exc):
                    raise
                relaxed_payload = {
                    **payload,
                    "crawler_config": {
                        "type": "CrawlerRunConfig",
                        "params": _relax_seed_crawl_config(crawler_config),
                    },
                }
                logger.info("crawl_site_seed_retry_relaxed_config", start_url=start_url)
                data = await _crawl_sync(client, relaxed_payload)
    except Exception as exc:
        logger.warning(
            "crawl_site_seed_request_failed",
            start_url=start_url,
            error=str(exc),
        )
        return CrawlResult(
            url=start_url,
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message=str(exc),
            status_code=_status_code_from_exception(exc),
            error_type=_error_type_name(exc),
            raw_error_text=_raw_error_text(exc),
        )

    pages = _normalise_results_block(data)
    if not pages:
        logger.warning("crawl_site_seed_empty_response", start_url=start_url)
        return CrawlResult(
            url=start_url,
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="empty response",
        )

    result = _extract_result(start_url, pages[0])
    # Only retry relaxed when no content selector is active: a selector-scoped
    # config keeps its target_elements through _relax_seed_crawl_config, so a
    # retry would re-run the same scoped extraction and just waste a crawl. This
    # mirrors the ``selector is None`` gate in crawl_page.
    if not crawler_config.get("target_elements") and _should_retry_relaxed_for_thin_content(result):
        relaxed_payload = {
            **payload,
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": _relax_seed_crawl_config(crawler_config),
            },
        }
        logger.info(
            "crawl_site_seed_retry_relaxed_config",
            start_url=start_url,
            word_count=result.word_count,
            html_words=_html_text_word_count(result.html),
        )
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                relaxed_data = await _crawl_sync(client, relaxed_payload)
            relaxed_pages = _normalise_results_block(relaxed_data)
            if relaxed_pages:
                relaxed_result = _extract_result(start_url, relaxed_pages[0])
                if relaxed_result.word_count > result.word_count:
                    return relaxed_result
        except Exception as exc:
            logger.warning(
                "crawl_site_seed_relaxed_retry_failed",
                start_url=start_url,
                error=str(exc),
            )
    return result


def _extract_bfs_seeds(seed_result: CrawlResult, *, base_domain: str) -> list[str]:
    """Extract same-domain internal links from the seed page.

    Returns an empty list when the seed itself failed (auth wall, 5xx,
    etc.) — the candidate set then degrades cleanly to sitemap-only.
    """
    if not seed_result.success:
        return []
    internal = (seed_result.links or {}).get("internal") or []
    seeds: list[str] = []
    for entry in internal:
        href = entry.get("href") if isinstance(entry, dict) else None
        if not href:
            continue
        if not _same_site_domain(urlparse(href).netloc.lower(), base_domain):
            continue
        seeds.append(_coerce_same_site_url_to_base_host(href, base_domain))
    return seeds


def _build_outcome_from_result(url: str, result: CrawlResult) -> FetchOutcome:
    """Map a CrawlResult (the seed path) to a fetch_outcomes JSONB entry."""
    if result.success:
        reason_code = (
            FetchReasonCode.NON_CONTENT_LISTING_PAGE.value
            if _is_non_content_listing_page(result)
            else FetchReasonCode.SUCCESS.value
        )
    else:
        # Synthesise a page-shape dict for the classifier so the same
        # error_message → FetchReasonCode mapping applies as for the
        # bulk path. ``result.status_code`` (2026-08-14) carries the real
        # HTTP status when the seed fetch raised an httpx.HTTPStatusError,
        # so a seed 5xx classifies HTTP_5XX instead of falling through to
        # unknown_exception.
        reason_code = _classify_fetch_outcome(
            {
                "success": False,
                "status_code": result.status_code,
                "error_message": result.error_message or "",
            },
        )
    outcome: FetchOutcome = {
        "url": url,
        "reason_code": reason_code,
        "status_code": result.status_code,
        "content_length": len(result.html or ""),
    }
    evidence = _evidence_for_crawl_result(result, reason_code=reason_code)
    # A seed fetch is always individually attempted — it is the ONE URL
    # _fetch_seed_page ever sends, never a batch fallout label.
    return _with_evidence(outcome, evidence, observed=True)


def _result_is_ingestable(result: CrawlResult, *, base_domain: str) -> bool:
    """Same-domain + non-empty + success — the legacy ingest-loop contract."""
    if not result.success:
        return False
    if not (result.fit_markdown or result.raw_markdown):
        return False
    return _same_site_domain(urlparse(result.url).netloc.lower(), base_domain)


async def _recover_bulk_5xx_batch(
    urls: list[str],
    *,
    crawler_config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
    base_domain: str,
    recovery_budget: int,
    deadline: float | None = None,
    stealth: bool = False,
    trigger_reason_code: str = FetchReasonCode.HTTP_5XX.value,
) -> tuple[list[CrawlResult], list[CrawlResult], list[FetchOutcome], int]:
    """Sequentially re-fetch a batch that failed WHOLESALE via bulk fetch.

    Called from ``crawl_site`` only when ``_chunked_bulk_fetch`` returned a
    ``transport_error`` that is a recoverable bulk-level failure — either a
    5xx or a read-timeout (see ``_is_recoverable_bulk_failure`` — the cause
    is unknowable from the transport response alone, so this retry is both
    the mitigation and the diagnosis). Reuses the single-page fetch path
    (``_crawl_page_with_config``, same one ``crawl_page`` uses) one URL at a
    time instead of duplicating fetch logic — see module-level comment on
    ``_MAX_SEQUENTIAL_RECOVERY`` for the supporting evidence.

    Bounded by ``recovery_budget`` (the crawl-job-wide remainder of
    ``_MAX_SEQUENTIAL_RECOVERY``). Once exhausted, remaining URLs in this
    batch are marked with ``trigger_reason_code`` — honestly, matching the
    actual signal we have (the bulk request failed with THIS reason; we
    simply ran out of budget to verify these particular URLs individually)
    rather than guessing ``BLOCKED_ANTI_BOT`` without evidence, and rather
    than always defaulting to ``HTTP_5XX`` even when the trigger was a
    timeout — without a network call, and the cap is logged once via
    ``crawl_bulk_5xx_recovery_capped``. Defaults to ``HTTP_5XX`` for direct
    callers (e.g. tests) that don't pass one explicitly.

    Returns ``(crawl_results, link_source_results, outcomes, attempted)``:

    - ``crawl_results``: ingestable, non-listing pages — mirrors
      ``batch_results`` from the bulk-success path (``_combine_bulk_responses``).
    - ``link_source_results``: every successfully fetched same-domain page,
      including listing pages — feeds BFS link discovery, mirrors
      ``_crawl_results_from_raw_results(raw_results, ...)`` from the
      bulk-success path so the frontier grows the same way it would have if
      the bulk request itself had not failed.
    - ``attempted``: URLs actually re-fetched over the network (counts
      against ``recovery_budget`` and against the caller's ``max_pages``
      budget, same as any other fetch attempt).
    """
    crawl_results: list[CrawlResult] = []
    link_source_results: list[CrawlResult] = []
    outcomes: list[FetchOutcome] = []
    attempted = 0
    recovered = 0
    still_failing = 0
    consecutive_failures = 0

    cooldown = settings.crawl_sequential_recovery_cooldown_seconds
    max_consecutive = settings.crawl_sequential_recovery_max_consecutive_failures
    time_budget = settings.crawl_sequential_recovery_max_seconds
    # ``deadline`` is the crawl-job-wide clock passed by ``crawl_site`` so a
    # many-batch crawl cannot spend the full allowance once per batch. Direct
    # callers (tests) may omit it and get a fresh per-call budget.
    started_at = _recovery_monotonic()
    if deadline is None:
        deadline = started_at + time_budget

    logger.info(
        "crawl_sequential_recovery_started",
        urls=len(urls),
        budget=recovery_budget,
        cooldown_seconds=cooldown,
        max_consecutive_failures=max_consecutive,
        max_seconds=time_budget,
    )

    def _abandon_remaining(index: int, *, reason_code: str = trigger_reason_code) -> int:
        """Mark every not-yet-attempted URL with ``reason_code``; return how many.

        Default is ``trigger_reason_code`` — the honest reason recovery was
        entered in the first place (HTTP_5XX or TIMEOUT). The RATE_LIMITED
        abandon branch below overrides it with
        ``NOT_FETCHED_RATE_LIMIT_STOP`` (A1) — never with RATE_LIMITED
        itself, which stays reserved for the one URL that was actually
        attempted and got that answer.
        """
        for leftover_url in urls[index:]:
            outcomes.append(
                _with_evidence(
                    {
                        "url": leftover_url,
                        "reason_code": reason_code,
                        "status_code": None,
                        "content_length": 0,
                    },
                    dict(_NO_EVIDENCE),
                    observed=False,
                )
            )
        return len(urls) - index

    for i, url in enumerate(urls):
        if attempted >= recovery_budget:
            remaining = _abandon_remaining(i)
            still_failing += remaining
            logger.warning(
                "crawl_bulk_5xx_recovery_capped",
                recovered=recovered,
                still_failing=still_failing,
                capped_at=_MAX_SEQUENTIAL_RECOVERY,
                remaining=remaining,
            )
            break

        # Circuit breaker: a run of failures despite the fresh session the
        # cooldown buys means the site is not recoverable, so stop burning a
        # cooldown per remaining URL.
        #
        # It only applies while NOTHING has been recovered yet. One success
        # proves the site IS reachable, and from then on failures are just the
        # intermittency this whole path exists for. Measured on intermedia.com
        # 2026-08-14: one run recovered 7 of 12 because early successes kept
        # resetting the counter, while the next run lost all 17 because its
        # first three attempts happened to fail. Same site, same code — only
        # luck differed, and the breaker turned that luck into the outcome.
        # Attempts and the job-wide deadline still bound the patient case.
        if consecutive_failures >= max_consecutive and recovered == 0:
            remaining = _abandon_remaining(i)
            still_failing += remaining
            logger.warning(
                "crawl_sequential_recovery_circuit_open",
                consecutive_failures=consecutive_failures,
                recovered=recovered,
                still_failing=still_failing,
                remaining=remaining,
            )
            break

        now = _recovery_monotonic()
        if now >= deadline:
            remaining = _abandon_remaining(i)
            still_failing += remaining
            logger.warning(
                "crawl_sequential_recovery_time_budget_exhausted",
                elapsed_seconds=round(now - started_at, 1),
                recovered=recovered,
                still_failing=still_failing,
                remaining=remaining,
            )
            break

        # Before EVERY attempt, including the first: the bulk burst that just
        # failed is exactly what left crawl4ai's browser flagged, so attempt
        # one needs the recycle window as much as the rest.
        await _recovery_sleep(cooldown)

        attempted += 1
        result = await _crawl_page_with_config(
            url,
            crawler_config,
            cookies=cookies,
            selector=None,
            stealth=stealth,
            # See settings.crawl_sequential_recovery_timeout_seconds
            # docstring: must exceed crawl4ai's internal 429-backoff
            # ceiling or we cut the request off before the real 429
            # comes back (2026-08-17 intermedia.com incident).
            timeout=settings.crawl_sequential_recovery_timeout_seconds,
        )
        reason_code = _classify_fetch_outcome(
            {
                "success": result.success,
                "status_code": result.status_code,
                "error_message": result.error_message or "",
            }
        )
        is_non_content_listing = result.success and _is_non_content_listing_page(result)
        if reason_code == FetchReasonCode.SUCCESS.value and is_non_content_listing:
            reason_code = FetchReasonCode.NON_CONTENT_LISTING_PAGE.value
        # This URL was individually attempted over the network (unlike the
        # abandoned/not-yet-attempted URLs above) — a real observation.
        outcomes.append(
            _with_evidence(
                {
                    "url": url,
                    "reason_code": reason_code,
                    "status_code": result.status_code,
                    "content_length": len(result.html or ""),
                },
                _evidence_for_crawl_result(result, reason_code=reason_code),
                observed=True,
            )
        )

        # A confirmed rate-limit (429, including crawl4ai's "Blocked by
        # anti-bot protection: HTTP 429 Too Many Requests" wrapping — see
        # _classify_fetch_outcome) means the target site has explicitly
        # told us to back off. Continuing the sequential retry — each one
        # separated only by the anti-bot cooldown, not a rate-limit-aware
        # backoff — provably makes the site's throttling worse instead of
        # recovering pages. Stop this batch's recovery immediately rather
        # than burning the remaining budget/cooldown on attempts that will
        # only add to the 429 pressure.
        #
        # ``url`` itself keeps the real, observed RATE_LIMITED outcome
        # appended above. The REST of the batch (index i+1 onward) was
        # never attempted at all — labelling those with RATE_LIMITED too
        # (the pre-A1 behaviour) inflates "how many times did this domain
        # actually reject us" with URLs we deliberately chose not to ask.
        # NOT_FETCHED_RATE_LIMIT_STOP keeps the one real observation
        # distinct from every abandoned-without-a-network-call URL (A1).
        if reason_code == FetchReasonCode.RATE_LIMITED.value:
            remaining = _abandon_remaining(
                i + 1, reason_code=FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
            )
            still_failing += 1 + remaining
            logger.warning(
                "crawl_sequential_recovery_rate_limited",
                url=url,
                recovered=recovered,
                still_failing=still_failing,
                remaining=remaining,
            )
            break

        if not result.success:
            still_failing += 1
            consecutive_failures += 1
            continue

        consecutive_failures = 0
        if _same_site_domain(urlparse(result.url).netloc.lower(), base_domain):
            link_source_results.append(result)
        if _result_is_ingestable(result, base_domain=base_domain) and not is_non_content_listing:
            crawl_results.append(result)
            recovered += 1

    return crawl_results, link_source_results, outcomes, attempted


def _combine_bulk_responses(
    *,
    candidates: list[str],
    raw_results: list[dict[str, Any]],
    transport_error: BaseException | None,
    base_domain: str,
) -> tuple[list[CrawlResult], list[FetchOutcome]]:
    """Match bulk candidates to crawl4ai responses; produce results + outcomes.

    Matching uses canonical-URL lookup first. For unmatched candidates we
    fall back to positional alignment with the response list — crawl4ai's
    bulk endpoint preserves submission order, and a redirect (``/old-page``
    → ``/new-page``) is the only common reason for a candidate's canonical
    to disappear from the response set. The fallback only claims a
    positional response that has not already been canonical-matched to a
    different candidate, so a redirect doesn't silently shadow a legitimate
    direct hit.
    """
    crawl_results: list[CrawlResult] = []
    outcomes: list[FetchOutcome] = []

    if not candidates:
        return crawl_results, outcomes

    # Canonical-URL → response, populated from the bulk response body.
    by_canonical: dict[str, dict[str, Any]] = {}
    for page in raw_results:
        if not page:
            continue
        result_url = page.get("url") or ""
        if not result_url:
            continue
        by_canonical[_canonicalise_url(result_url)] = page

    candidate_canonicals = [_canonicalise_url(c) for c in candidates]
    canonical_to_idx: dict[str, int] = {c: i for i, c in enumerate(candidate_canonicals)}
    # Track which positional response has already been claimed by canonical
    # match. Prevents the redirect-fallback from re-claiming.
    claimed_response_indices: set[int] = set()
    for canonical in candidate_canonicals:
        page = by_canonical.get(canonical)
        if page is None:
            continue
        # Locate the positional index of this matched response so the
        # redirect fallback won't claim it again.
        for j, raw in enumerate(raw_results):
            if raw is page:
                claimed_response_indices.add(j)
                break

    for i, url in enumerate(candidates):
        if transport_error is not None:
            # The whole-batch request failed transport-wide — this
            # candidate's own outcome was never individually confirmed, even
            # though the same exception genuinely applies to the batch.
            outcomes.append(
                _with_evidence(
                    {
                        "url": url,
                        "reason_code": _classify_fetch_outcome(None, error=transport_error),
                        "status_code": None,
                        "content_length": 0,
                    },
                    _evidence_from_exception(transport_error),
                    observed=False,
                )
            )
            continue

        page = by_canonical.get(candidate_canonicals[i])

        # Redirect fallback: positional alignment when canonical match
        # missed AND the response at this index hasn't been claimed AND
        # its URL doesn't canonical-match a DIFFERENT candidate (which
        # would be ambiguous) AND the response stays on the same origin
        # domain. The same-domain guard makes the fallback safe even if
        # crawl4ai's MemoryAdaptiveDispatcher reorders the response list
        # under load — at worst we miss a redirect classification and
        # surface UNKNOWN_EXCEPTION (visible to operators) rather than
        # silently mislabel an unrelated URL.
        if (
            page is None
            and len(raw_results) == len(candidates)
            and i < len(raw_results)
            and i not in claimed_response_indices
        ):
            positional = raw_results[i]
            if positional and positional.get("url"):
                pos_url = positional["url"]
                pos_canonical = _canonicalise_url(pos_url)
                pos_owner_idx = canonical_to_idx.get(pos_canonical)
                pos_domain = urlparse(pos_url).netloc.lower()
                if (pos_owner_idx is None or pos_owner_idx == i) and _same_site_domain(
                    pos_domain, base_domain
                ):
                    page = positional
                    claimed_response_indices.add(i)

        if page is None:
            # The bulk request itself transported fine, but no response in
            # it matches this candidate (and the redirect fallback above
            # didn't resolve it either) — this URL's own outcome was never
            # confirmed.
            outcomes.append(
                _with_evidence(
                    {
                        "url": url,
                        "reason_code": _classify_fetch_outcome(page),
                        "status_code": None,
                        "content_length": 0,
                    },
                    dict(_NO_EVIDENCE),
                    observed=False,
                )
            )
            continue

        result = _extract_result(url, page)
        reason_code = _classify_fetch_outcome(page)
        is_non_content_listing = _is_non_content_listing_page(result)
        if reason_code == FetchReasonCode.SUCCESS.value and is_non_content_listing:
            reason_code = FetchReasonCode.NON_CONTENT_LISTING_PAGE.value
        # A real per-URL page result came back from crawl4ai's bulk response.
        outcomes.append(
            _with_evidence(
                {
                    "url": url,
                    "reason_code": reason_code,
                    "status_code": page.get("status_code"),
                    "content_length": len(page.get("html", "") or ""),
                },
                _evidence_from_page_result(page, reason_code=reason_code),
                observed=True,
            )
        )

        if _result_is_ingestable(result, base_domain=base_domain) and not is_non_content_listing:
            crawl_results.append(result)

    return crawl_results, outcomes


def _outcome_for_failed_url(url: str, error: BaseException) -> FetchOutcome:
    """Outcome for a URL whose OWN chunk failed to transport (A2).

    Classified from THAT chunk's actual exception — never borrowed from a
    different chunk's failure, and never a guess. ``observed=False``: the
    exception is a genuine observation about the CHUNK's request (which may
    have bundled many URLs), not a confirmed per-URL result — we do not
    know whether the site would have failed this specific URL if it had
    been sent alone. Attributing the chunk's single failure as `observed`
    for every URL in it would inflate "how many times did this domain
    actually fail" by the chunk size. See ``_with_evidence``'s docstring
    for the full rationale.
    """
    outcome: FetchOutcome = {
        "url": url,
        "reason_code": _classify_fetch_outcome(None, error=error),
        "status_code": _status_code_from_exception(error),
        "content_length": 0,
    }
    return _with_evidence(outcome, _evidence_from_exception(error), observed=False)


def _outcome_for_not_attempted_url(url: str) -> FetchOutcome:
    """Outcome for a URL whose chunk was never even sent (A1).

    An earlier chunk already observed RATE_LIMITED/BLOCKED_ANTI_BOT, so
    ``_chunked_bulk_fetch`` stopped before this URL's chunk went out.
    Deliberately NOT ``FetchReasonCode.RATE_LIMITED`` — that value stays
    reserved for a URL we actually asked and got that answer from, so a
    "how many times did this domain really reject us" count is not
    inflated by URLs we chose not to send. ``observed=False``: no network
    call was ever made for this URL, so there is nothing to report as
    evidence.
    """
    outcome: FetchOutcome = {
        "url": url,
        "reason_code": FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value,
        "status_code": None,
        "content_length": 0,
    }
    return _with_evidence(outcome, dict(_NO_EVIDENCE), observed=False)


async def _recover_thin_bulk_results(
    batch_results: list[CrawlResult],
    *,
    crawler_config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
    base_domain: str,
    rate_limit: float | None = None,
) -> list[CrawlResult]:
    """Re-crawl thin-but-rich-HTML bulk pages with the relaxed config.

    Mirrors the single-page preview / seed relaxed retry so BFS pages do not
    diverge from what the preview shows. Bounded by design:

    - only pages whose strict result is thin yet whose HTML clearly holds
      content (``_should_retry_relaxed_for_thin_content``) are retried, so a
      well-extracted site pays nothing;
    - a single extra bulk request covers the whole thin subset;
    - a relaxed result replaces the strict one only when it has more words.

    Worst case (a site that is thin under strict config on every page) is one
    extra bulk crawl of those pages — the precise cost of recovering content
    the strict pipeline over-pruned. Logged so that cost is visible.

    ``rate_limit`` (2026-08-18 fix, previously missing here): this is still a
    bulk request against the same domain crawl_site's main batch just fetched
    — it MUST inherit the same client-side pacing, or a rate-limited site
    gets an unpaced burst on every batch that has thin results, defeating
    the pacing this whole mechanism exists for.
    """
    thin = [r for r in batch_results if _should_retry_relaxed_for_thin_content(r)]
    if not thin:
        return batch_results

    thin_urls = [r.url for r in thin]
    logger.info(
        "crawl_site_bulk_retry_relaxed_config",
        thin_count=len(thin_urls),
        batch_size=len(batch_results),
    )
    relaxed_fetch = await _chunked_bulk_fetch(
        urls=thin_urls,
        crawler_config=_relax_seed_crawl_config(crawler_config),
        cookies=cookies,
        rate_limit=rate_limit,
    )
    # Only the subset that actually transported can be compared against the
    # strict result — a URL whose relaxed chunk failed or was never
    # attempted (A1/A2) simply keeps its strict result below (no relaxed
    # candidate found for it), rather than every candidate in this batch
    # being treated as failed via a single shared transport_error.
    relaxed_ok_urls = [
        u
        for u in thin_urls
        if u not in relaxed_fetch.failed and u not in relaxed_fetch.not_attempted
    ]
    relaxed_results, _ = _combine_bulk_responses(
        candidates=relaxed_ok_urls,
        raw_results=relaxed_fetch.raw_results,
        transport_error=None,
        base_domain=base_domain,
    )
    relaxed_by_canonical = {_canonicalise_url(r.url): r for r in relaxed_results}

    recovered: list[CrawlResult] = []
    recovered_pages: list[dict[str, Any]] = []
    for result in batch_results:
        better = relaxed_by_canonical.get(_canonicalise_url(result.url))
        if better is not None and better.word_count > result.word_count:
            recovered.append(better)
            recovered_pages.append(
                {
                    "url": result.url,
                    "strict_word_count": result.word_count,
                    "relaxed_word_count": better.word_count,
                }
            )
        else:
            recovered.append(result)
    logger.info(
        "crawl_site_bulk_retry_relaxed_result",
        thin_count=len(thin_urls),
        recovered_count=len(recovered_pages),
        recovered_pages=recovered_pages[:10],
    )
    return recovered


# httpx timeout for one bulk ``/crawl`` chunk request — see
# settings.crawl_bulk_base_timeout_seconds (config.py) for the tunable
# value and the margin it keeps over crawl4ai's server-side
# ``timeouts.batch_process`` (300.0s, deploy/crawl4ai config). With
# client-side pacing (fix/client-side-crawl-pacing) the wait happens
# BEFORE a chunk request starts, in ``_chunked_bulk_fetch``'s inter-chunk
# sleep — not inside the request itself — so a chunk's own duration is
# just the burst's fetch/render time, not the pacing gap. This timeout is
# therefore a fixed vangnet, not a per-chunk formula.
#
# crawl4ai's MemoryAdaptiveDispatcher handles in-batch concurrency
# server-side; the client just waits for the whole batch. Voys-scale
# Voys/support (~500 candidates) completes well under 90s on the production
# container.
#
# 2026-08-18 (fix/bulk-timeout-scales-with-pacing, intermedia.com incident
# #2): a prior version of this timeout scaled with ``mean_delay`` on the
# belief that crawl4ai's own RateLimiter enforced our ``rate_limit``
# server-side. Measured 2026-08-18 (see build_crawl_config's docstring):
# it does not — ``mean_delay``/``semaphore_count`` are silently ignored by
# the REST server. That formula is removed; pacing lives entirely in
# ``_chunked_bulk_fetch`` now.


# crawl4ai 0.8 server enforces ``List should have at most 100 items after
# validation`` on POST /crawl ``urls``. The constant lives here so it can
# be raised in lock-step with any future crawl4ai schema relaxation.
# Discovered live on help.voys.nl (208 sitemap entries → 422 → 1 ingested).
_BULK_CHUNK_SIZE = 100

# Client-side pacing (2026-08-18, fix/client-side-crawl-pacing): measured
# live against the running crawl4ai server that ``mean_delay`` /
# ``semaphore_count`` on CrawlerRunConfig are silently ignored — the REST
# server builds its own ``MemoryAdaptiveDispatcher`` (api.py) and passes
# THAT to ``arun_many``, never reading our config. 8 URLs with
# ``mean_delay=2.0`` finished in 3.2s instead of the predicted 16s, and
# ``semaphore_count`` 1 vs 8 made no measurable difference. Separately, a
# single bulk chunk IS one burst on the target site: 16 URLs in one request
# completed within 330ms of each other. Real rate limiting can therefore
# only happen client-side, via the two knobs we actually control: how many
# URLs go in one request (the burst — see ``_burst_size_for``) and how long
# we wait between requests (the gap — see ``_chunked_bulk_fetch``).
#
# ``_BURST_WINDOW_SECONDS`` is the size of that accounting window: how much
# a site is allowed to receive within one window, at the requested
# rate_limit. A larger window produces bigger bursts with longer pauses
# between them (fewer HTTP round-trips, coarser pacing); a smaller window
# is smoother traffic but costs more round-trips for the same rate.
_BURST_WINDOW_SECONDS = 10.0


def _burst_size_for(rate_limit: float | None) -> int:
    """Translate a requests/second rate limit into a per-request burst size.

    ``rate_limit is None`` means no client-side pacing was requested: keep
    the historical fixed chunk size (``_BULK_CHUNK_SIZE``), so crawls
    without a rate_limit are completely unaffected by this function.

    Uses ``int(x + 0.5)``, deliberately NOT ``round()``. Python's
    ``round()`` uses banker's rounding to the nearest even integer —
    ``round(0.5) == 0`` and ``round(2.5) == 2`` — which already caused a
    real bug in this codebase (``build_crawl_config``'s
    ``semaphore_count = max(1, min(round(rate_limit), 8))`` collapsed
    ``rate_limit=0.5`` down to 1 instead of the intended 2). Do not
    reintroduce ``round()`` here.
    """
    if rate_limit is None:
        return _BULK_CHUNK_SIZE
    return max(1, min(_BULK_CHUNK_SIZE, int(rate_limit * _BURST_WINDOW_SECONDS + 0.5)))


# Total budget (per crawl_site call) for sequential one-URL-at-a-time
# recovery of a bulk batch that failed as a whole with an opaque 5xx (or,
# since 2026-08-18, a read-timeout — see _recover_bulk_5xx_batch,
# _is_recoverable_bulk_failure). Evidence 2026-08-14
# (intermedia.com): crawl4ai's bulk arun_many fails the ENTIRE concurrent
# batch with one opaque 500
# (``{"error":"Internal server error","correlation_id":"..."}"``, no
# diagnosable reason) the moment ANY url in it hits an anti-bot challenge,
# while the same URLs fetched one at a time via the single-page path pass
# the challenge for at least some of them (/products/unite succeeded,
# /products/ai still blocked, seconds apart) — the challenge is
# per-request intermittent, so serial retry recovers real pages AND tells
# us the real per-URL reason honestly. Capped so a site that fails every
# request cannot turn a bulk crawl into hundreds of serial round-trips;
# once exhausted, remaining URLs are marked HTTP_5XX (not a guessed
# BLOCKED_ANTI_BOT) without a network call (crawl_bulk_5xx_recovery_capped).
_MAX_SEQUENTIAL_RECOVERY = 60

# crawl4ai's own RateLimiter (inside its dispatcher, used for both the bulk
# arun_many path AND the single-page /crawl path) retries a 429 up to
# max_retries=3 times with an exponential backoff capped at max_delay=60.0
# seconds — both hardcoded in crawl4ai, not exposed via CrawlerRunConfig
# (only mean_delay / max_range / semaphore_count are accepted as config
# keys at all — and even those are silently ignored by the REST server,
# see build_crawl_config's docstring). Worst case before crawl4ai
# gives up and returns the REAL 429 result to us: 3 * 60.0 = 180s of pure
# backoff, before whatever the actual page fetch/render itself costs on
# top. Reference only — see settings.crawl_sequential_recovery_timeout_seconds
# (config.py) for the httpx timeout that must stay above this ceiling, and
# why (2026-08-17 intermedia.com incident).
_CRAWL4AI_RATE_LIMIT_BACKOFF_CEILING_SECONDS = 3 * 60.0

# Indirection so tests can drive the recovery loop's pacing without ever
# sleeping for real: a suite that honours the 75s production cooldown would
# take hours. Patch these, not asyncio/time, so nothing else is affected.
_recovery_sleep = asyncio.sleep
_recovery_monotonic = time.monotonic

# Same indirection, dedicated to the client-side bulk-chunk pacing in
# ``_chunked_bulk_fetch`` (see ``_burst_size_for`` above). Kept separate
# from ``_recovery_sleep`` / ``_recovery_monotonic`` — they pace an
# unrelated loop (sequential 5xx recovery) and patching one must not
# silently affect the other.
_pacing_sleep = asyncio.sleep
_pacing_monotonic = time.monotonic

# Deel B (2026-08-18, "a stop-signal should slow you down, not give up") —
# same test-friendly indirection as the two pairs above, dedicated to the
# explicit pause ``crawl_site`` takes after lowering its in-job rate_limit,
# before resuming with the next (slower) batch.
_slowdown_sleep = asyncio.sleep

# How many times, in a row, ``crawl_site`` will halve its rate_limit and
# retry the URLs a RATE_LIMITED stop skipped before giving up on them.
# Mirrors the domain-level AIMD controller's philosophy (halving is
# reversible, giving up permanently is not) but bounded: a site that is
# STILL rate-limiting us after three halvings (e.g. 2.0 -> 1.0 -> 0.5 ->
# 0.25 req/s, hitting MIN_DOMAIN_RATE_LIMIT's floor) is not being slow —
# something else is wrong (an aggressive WAF, not simple throttling), and
# burning the rest of the crawl-job's time budget on ever-smaller batches
# would just replace the old "hammer at full speed" bug with a new
# "hammer at a snail's pace forever" one. Resets to 0 whenever a batch
# completes with no stop signal at all, so a site that recovers gets a
# fresh three attempts if it later relapses, rather than accumulating
# toward the limit across unrelated incidents in the same job.
_MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS = 3

# Seed rate_limit when a RATE_LIMITED signal is observed on a crawl that
# was started with no explicit rate_limit at all (``rate_limit=None`` —
# unpaced, full speed). Matches the value the only production caller
# (``routes/crawl_sync.py``) already passes for every ordinary crawl, so
# "start pacing because the site just complained" begins from the same
# baseline a normal crawl would have used anyway, rather than an
# arbitrary new number.
_UNPACED_SLOWDOWN_STARTING_RATE_LIMIT = 2.0


def _lower_rate_limit_for_slowdown(current_rate_limit: float | None) -> float:
    """Halve ``current_rate_limit`` after an observed RATE_LIMITED stop.

    Reuses ``domain_rate_limit_control.MIN_DOMAIN_RATE_LIMIT`` as the floor
    instead of inventing a second, arbitrary one — keeps this ephemeral,
    in-job backoff consistent with the persisted domain-level halving that
    already uses that floor. This one is NEVER persisted: it only affects
    the rest of THIS ``crawl_site`` call, and the domain-level controller
    (applied once, after the job completes, for the NEXT crawl) is
    untouched and unaffected — see ``compute_domain_rate_limit_update``.
    """
    baseline = (
        current_rate_limit
        if current_rate_limit is not None
        else _UNPACED_SLOWDOWN_STARTING_RATE_LIMIT
    )
    return max(MIN_DOMAIN_RATE_LIMIT, baseline / 2)


# Server-side BFS deep crawl polling budget. /crawl/job is async — submit,
# get task_id, poll status. Voys-support full-depth crawl (~500 pages
# across 3 levels) completes well under 30 minutes; the cap is a safety
# net for stuck workers.
_DEEP_POLL_INTERVAL = 5.0  # seconds between status polls
_MAX_DEEP_POLL = 30 * 60  # max total seconds (30 minutes)


def _url_matches_include_patterns(u: str, include_patterns: list[str] | None) -> bool:
    """Filter URL against include_patterns using crawl4ai URLPatternFilter semantics.

    A pattern like ``/nl/*`` is a prefix glob (fnmatch on the URL path), NOT a
    substring. Plain substring match made ``/nl/*`` literally never match
    because no URL contains the asterisk — observed live on
    wiki.redcactus.cloud (29 BFS-discovered URLs, 0 retained as candidates →
    1 page ingested instead of ~150). Two contracts honoured:

      * Patterns containing ``*`` or ``?`` ⇒ ``fnmatch`` against the URL path
        (matches the old crawl4ai URLPatternFilter contract)
      * Plain patterns (no wildcard) ⇒ substring match on the URL —
        matches the original "simple substring" intent for legacy callers
    """
    if not include_patterns:
        return True
    path_with_query = urlparse(u).path
    for p in include_patterns:
        if "*" in p or "?" in p:
            if fnmatch.fnmatch(path_with_query, p):
                return True
        elif p in u:
            return True
    return False


def _url_matches_patterns(u: str, patterns: list[str] | None) -> bool:
    """Return whether ``u`` matches any path/URL pattern.

    Wildcard patterns use fnmatch against the URL path, matching
    crawl4ai's URLPatternFilter semantics. Plain patterns keep the
    legacy substring-on-URL behaviour.
    """
    if not patterns:
        return False
    path_with_query = urlparse(u).path
    for p in patterns:
        if "*" in p or "?" in p:
            if fnmatch.fnmatch(path_with_query, p):
                return True
        elif p in u:
            return True
    return False


async def _bfs_deep_crawl(
    *,
    start_url: str,
    crawler_config: dict[str, Any],
    max_depth: int,
    max_pages: int,
    include_patterns: list[str] | None,
    exclude_patterns: list[str] | None,
    cookies: list[dict[str, Any]] | None,
) -> tuple[list[CrawlResult], BaseException | None]:
    """Server-side BFS deep crawl via crawl4ai's ``/crawl/job`` endpoint.

    Submits a single crawl job that uses crawl4ai's ``BFSDeepCrawlStrategy``
    (recursive multi-level link-following) with optional ``URLPatternFilter``
    (real fnmatch glob, not the substring approximation). crawl4ai's own
    server-side ``MemoryAdaptiveDispatcher`` handles concurrency safely —
    no client-side ``asyncio.gather`` over a shared connection.

    Returns ``(results, transport_error)``:
      * ``results`` — list of ``CrawlResult`` for every URL the BFS visited
        (including the start_url itself; results may exceed ``max_pages``
        slightly because crawl4ai counts queued pages, not strictly
        finished ones).
      * ``transport_error`` — non-None when the submission/polling itself
        failed (network error, timeout, 5xx). ``results`` is empty on
        failure; the caller decides whether to fall back to sitemap-only
        or surface as an error.
    """
    deep_crawl_params: dict[str, Any] = {
        "max_depth": max_depth,
        "max_pages": max_pages,
        "include_external": False,
    }
    filters: list[dict[str, Any]] = []
    if include_patterns:
        filters.append(
            {
                "type": "URLPatternFilter",
                "params": {"patterns": include_patterns},
            }
        )
    if exclude_patterns:
        filters.append(
            {
                "type": "URLPatternFilter",
                "params": {"patterns": exclude_patterns, "reverse": True},
            }
        )
    if filters:
        # Crawl4AI 0.8.6 only reconstructs nested objects when wrapped in
        # ``{"type": "<ClassName>", "params": {...}}`` — a bare list stays a
        # list and BFSDeepCrawlStrategy crashes with
        # ``AttributeError: 'list' object has no attribute 'apply'`` the
        # moment it walks past depth 0. Pinned by tests.
        deep_crawl_params["filter_chain"] = {
            "type": "FilterChain",
            "params": {"filters": filters},
        }

    config = dict(crawler_config)
    config["deep_crawl_strategy"] = {
        "type": "BFSDeepCrawlStrategy",
        "params": deep_crawl_params,
    }

    payload: dict[str, Any] = {
        "urls": [start_url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }
    bc = _build_browser_config_with_cookies(cookies)
    if bc:
        payload["browser_config"] = bc

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{settings.crawl4ai_api_url}/crawl/job",
                json=payload,
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            task_id: str = resp.json()["task_id"]
            logger.info(
                "crawl_site_bfs_job_submitted",
                start_url=start_url,
                task_id=task_id,
                max_depth=max_depth,
                max_pages=max_pages,
            )

            elapsed = 0.0
            result_data: dict[str, Any] = {}
            while elapsed < _MAX_DEEP_POLL:
                await asyncio.sleep(_DEEP_POLL_INTERVAL)
                elapsed += _DEEP_POLL_INTERVAL
                resp = await client.get(
                    f"{settings.crawl4ai_api_url}/crawl/job/{task_id}",
                    headers=_auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                status_str = data.get("status", "").lower()
                if status_str == "completed":
                    result_data = data.get("result", {}) or {}
                    break
                if status_str == "failed":
                    err_msg = data.get("error", "unknown")
                    raise RuntimeError(f"BFS deep crawl job {task_id} failed: {err_msg}")
            else:
                raise TimeoutError(
                    f"BFS deep crawl job {task_id} did not complete within {_MAX_DEEP_POLL}s"
                )
    except Exception as exc:
        logger.warning(
            "crawl_site_bfs_failed",
            start_url=start_url,
            error=str(exc),
        )
        return [], exc

    raw_results = _normalise_results_block(result_data)
    crawl_results = [_extract_result(start_url, page) for page in raw_results if page]
    logger.info(
        "crawl_site_bfs_complete",
        start_url=start_url,
        pages=len(crawl_results),
    )
    return crawl_results, None


# Reason codes that mean "the target site just told us to back off" —
# observed either as a per-URL page result inside an otherwise-successful
# chunk, or as the whole chunk's own transport exception. Either shape MUST
# stop ``_chunked_bulk_fetch`` from sending any further chunk (A1): sending
# chunk 2..N after chunk 1 already saw this signal is precisely the
# "ignore the 429, keep hammering" bug this fixes.
_STOP_CHUNKING_REASON_CODES = frozenset(
    {FetchReasonCode.RATE_LIMITED.value, FetchReasonCode.BLOCKED_ANTI_BOT.value}
)


@dataclass
class ChunkedFetchResult:
    """Per-chunk outcome of one ``_chunked_bulk_fetch`` call.

    Replaces the old ``tuple[list[dict], BaseException | None]`` return
    shape, which had two bugs (bulk-path defects block A):

    - it kept only the LAST failing chunk's exception, silently discarding
      the exception (and the honest cause) of every earlier failed chunk
      (A2);
    - a caller that saw ``transport_error is not None`` had no way to tell
      "which URLs actually failed" from "which URLs already succeeded in
      an earlier chunk" — so a retry had to redo the WHOLE batch, and a
      combine step had to treat every candidate as failed even when most
      of them had a perfectly good result sitting in ``raw_results``.

    Attributes:
        raw_results: flattened per-URL page dicts from every chunk that
            transported successfully (an httpx-level 2xx bulk response).
            A page dict here may still carry ``success: false`` at the
            page level (404, 500, ...) — that is a normal per-URL outcome,
            classified downstream by ``_classify_fetch_outcome``, not a
            transport failure.
        failed: URL -> the transport exception raised by ITS OWN chunk. A
            dict (not a single ``transport_error``) so two different
            chunks that both fail keep their own, distinct exceptions —
            never the second silently overwriting the first (A2).
        not_attempted: URLs belonging to chunks that were never submitted
            at all because an earlier chunk's outcome (page-level
            classification, or the chunk's own transport exception)
            classified as RATE_LIMITED or BLOCKED_ANTI_BOT and the loop
            stopped sending further chunks (A1, see ``stopped_early``).
            These made ZERO network calls — kept out of ``failed`` so a
            caller never conflates "the site rejected this URL" with "we
            chose not to ask".
        stopped_early: True when ``not_attempted`` is non-empty for the
            reason described above.
        stop_trigger_reason_code: which of ``_STOP_CHUNKING_REASON_CODES``
            actually caused ``stopped_early`` — ``None`` when
            ``stopped_early`` is False. Lets a caller (``crawl_site``,
            Deel B) tell "the site asked us to slow down" (RATE_LIMITED)
            apart from "the site blocked us outright" (BLOCKED_ANTI_BOT):
            slowing down helps the former and does nothing for the
            latter. BLOCKED_ANTI_BOT wins when a single chunk somehow
            observes both (a block is the more severe signal).
    """

    raw_results: list[dict[str, Any]] = field(default_factory=list)
    failed: dict[str, BaseException] = field(default_factory=dict)
    not_attempted: list[str] = field(default_factory=list)
    stopped_early: bool = False
    stop_trigger_reason_code: str | None = None


async def _chunked_bulk_fetch(
    *,
    urls: list[str],
    crawler_config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
    stealth: bool = False,
    rate_limit: float | None = None,
) -> ChunkedFetchResult:
    """Submit ``urls`` to crawl4ai's bulk ``/crawl`` endpoint in chunks.

    crawl4ai 0.8 server enforces a 100-URL cap on the ``urls`` array.
    Submitting more in one request returns 422. We chunk client-side and
    accumulate raw results; per-chunk transport failure is recorded per URL
    (see ``ChunkedFetchResult.failed``) but does not by itself stop the
    remaining chunks from shipping — partial coverage beats zero coverage.
    The one exception is a confirmed rate-limit/anti-bot signal — see below.

    ``rate_limit`` (requests/second, optional) additionally paces the
    chunks client-side. crawl4ai's server ignores our ``mean_delay`` /
    ``semaphore_count`` config (see ``_burst_size_for``'s docstring for the
    measurement), so real rate limiting can only happen here: the chunk
    size itself becomes the burst (``_burst_size_for``), and the gap
    between the START of one chunk and the START of the next is held to
    ``chunk_size / rate_limit`` seconds — crediting whatever time the
    chunk's own request already consumed, so we never add avoidable
    latency on top of a slow response. ``rate_limit is None`` disables all
    pacing: chunk size reverts to the historical fixed ``_BULK_CHUNK_SIZE``
    and no sleep is ever inserted, matching pre-pacing behaviour exactly.

    Each chunk request uses the fixed
    ``settings.crawl_bulk_base_timeout_seconds`` httpx timeout — see that
    setting's docstring (config.py) for the margin it keeps over crawl4ai's
    server-side ``timeouts.batch_process``. The pacing gap above happens
    BEFORE a chunk's request starts, so it never eats into that timeout.

    A1 (2026-08-18): once a chunk's outcome classifies as RATE_LIMITED or
    BLOCKED_ANTI_BOT — either a per-URL page result within a successfully
    transported chunk, or the chunk's own transport exception — every
    later, not-yet-submitted chunk is skipped entirely instead of being
    sent anyway. The chunk that produced the signal still fully completes
    (its own request already went out; we cannot un-send it), only chunks
    AFTER it are affected. See ``ChunkedFetchResult.not_attempted``.
    """
    result = ChunkedFetchResult()
    if not urls:
        return result
    chunk_size = _burst_size_for(rate_limit)
    previous_chunk_start: float | None = None
    for chunk_start in range(0, len(urls), chunk_size):
        if rate_limit is not None and previous_chunk_start is not None:
            gap_seconds = chunk_size / rate_limit
            elapsed_since_previous_start = _pacing_monotonic() - previous_chunk_start
            remaining = gap_seconds - elapsed_since_previous_start
            if remaining > 0:
                await _pacing_sleep(remaining)
        previous_chunk_start = _pacing_monotonic()
        chunk_urls = urls[chunk_start : chunk_start + chunk_size]
        payload: dict[str, Any] = {
            "urls": chunk_urls,
            "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_config},
        }
        bc = _build_browser_config_with_cookies(cookies, stealth=stealth)
        if bc:
            payload["browser_config"] = bc

        chunk_reason_codes: set[str] = set()
        try:
            async with httpx.AsyncClient(
                timeout=settings.crawl_bulk_base_timeout_seconds
            ) as client:
                data = await _crawl_sync(client, payload)
            chunk_pages = _normalise_results_block(data)
            result.raw_results.extend(chunk_pages)
            for page in chunk_pages:
                chunk_reason_codes.add(_classify_fetch_outcome(page))
        except Exception as exc:
            for chunk_url in chunk_urls:
                result.failed[chunk_url] = exc
            chunk_reason_codes.add(_classify_fetch_outcome(None, error=exc))
            logger.warning(
                "crawl_site_bulk_chunk_failed",
                chunk_index=chunk_start // chunk_size,
                chunk_size=len(chunk_urls),
                error=str(exc),
            )

        if chunk_reason_codes & _STOP_CHUNKING_REASON_CODES:
            remaining_urls = urls[chunk_start + chunk_size :]
            if remaining_urls:
                result.stopped_early = True
                result.not_attempted = remaining_urls
                result.stop_trigger_reason_code = (
                    FetchReasonCode.BLOCKED_ANTI_BOT.value
                    if FetchReasonCode.BLOCKED_ANTI_BOT.value in chunk_reason_codes
                    else FetchReasonCode.RATE_LIMITED.value
                )
                logger.warning(
                    "crawl_bulk_stopped_after_rate_limit_signal",
                    sent_urls=chunk_start + len(chunk_urls),
                    not_attempted_urls=len(remaining_urls),
                    triggering_reason_codes=sorted(
                        chunk_reason_codes & _STOP_CHUNKING_REASON_CODES
                    ),
                )
            break

    return result


def _normalise_results_block(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the various shapes crawl4ai returns into a list of dicts."""
    results = data.get("results", data.get("result", []))
    if isinstance(results, dict):
        return [results]
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    return []


def _build_candidate_set(
    *,
    start_url: str,
    sitemap_urls: list[str],
    bfs_seed_urls: list[str],
    base_domain: str,
    max_pages: int,
    include_patterns: list[str] | None,
    include_start_url: bool = True,
) -> list[str]:
    """Union(sitemap, BFS-seeds), filtered to same-domain, deduped, capped.

    AC-2: when union exceeds ``max_pages``, sitemap entries take priority
    over BFS-discovered URLs. ``include_patterns`` (when set) restricts
    candidates by simple substring match on the path — same semantics as
    the old crawl4ai URLPatternFilter without the deserialisation
    fragility (SPEC-CRAWL-001 v0.8.6 issue).

    ``include_start_url`` controls whether ``start_url`` is added as the
    first candidate. ``crawl_site`` sets it to ``False`` because the seed
    is fetched separately (with full config including login_indicator) and
    re-submitting it in the bulk would be a redundant fetch. Even when
    excluded, ``start_url``'s canonical form is recorded in the dedupe
    ``seen`` set so a sitemap entry that points back to the homepage
    doesn't sneak in as a duplicate.
    """

    def _same_domain(u: str) -> bool:
        return _same_site_domain(urlparse(u).netloc.lower(), base_domain)

    def _matches_include(u: str) -> bool:
        # ``include_patterns`` carry crawl4ai URLPatternFilter semantics: a
        # value like ``/nl/*`` is a prefix glob, not a substring. Plain
        # substring match (``p in u``) makes ``/nl/*`` literally never match
        # because no URL contains the asterisk character — observed live on
        # wiki.redcactus.cloud (29 BFS-discovered URLs, 0 retained as
        # candidates → 1 page ingested instead of ~150). We honour both:
        #
        #   * Patterns containing ``*`` or ``?`` ⇒ ``fnmatch`` against the
        #     full URL path (incl. query). Same contract as the old crawl4ai
        #     URLPatternFilter.
        #   * Plain patterns (no wildcard) ⇒ substring match on the URL —
        #     matches the original "simple substring" intent.
        if not include_patterns:
            return True
        path_with_query = urlparse(u).path
        for p in include_patterns:
            if "*" in p or "?" in p:
                if fnmatch.fnmatch(path_with_query, p):
                    return True
            elif p in u:
                return True
        return False

    seen: set[str] = set()
    ordered: list[str] = []

    def _add(u: str) -> None:
        if not u:
            return
        if not _same_domain(u):
            return
        u = _coerce_same_site_url_to_base_host(u, base_domain)
        if not _matches_include(u):
            return
        canonical = _canonicalise_url(u)
        if canonical in seen:
            return
        seen.add(canonical)
        ordered.append(u)

    # Priority order: start_url first (when included), then sitemap (site-
    # owner truth), then BFS-discovered (best-effort).
    if include_start_url:
        _add(start_url)
    elif start_url:
        # Reserve start_url's canonical so dedupe excludes it without
        # adding it to the candidate list.
        seen.add(_canonicalise_url(start_url))
    for u in sitemap_urls:
        _add(u)
    for u in bfs_seed_urls:
        _add(u)

    if max_pages > 0 and len(ordered) > max_pages:
        ordered = ordered[:max_pages]
    return ordered


async def crawl_dom_summary(url: str) -> list[dict] | None:
    """Crawl a page with DOM extraction JS for AI selector detection.

    Injects JS that extracts a ranked DOM summary and captures it via
    a hidden <pre> element.
    """
    dom_js = """
(async () => {
  const skipTags = new Set(['script', 'style', 'link', 'meta', 'noscript', 'template', 'svg']);
  const els = [...document.body.querySelectorAll('*')]
    .filter(el => {
      const tag = el.tagName.toLowerCase();
      if (skipTags.has(tag)) return false;
      const style = window.getComputedStyle(el);
      if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
      return el.innerText && el.children.length < 8;
    })
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: (typeof el.className === 'string' ? el.className : null) || null,
      wordCount: el.innerText.trim().split(/\\s+/).length,
      selector: el.id
        ? '#' + el.id
        : (typeof el.className === 'string' && el.className.trim())
          ? el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).join('.')
          : el.tagName.toLowerCase()
    }))
    .sort((a, b) => b.wordCount - a.wordCount)
    .slice(0, 25);

  const pre = document.createElement('pre');
  pre.id = '__klai_dom_summary__';
  pre.style.cssText = 'position:absolute;left:-9999px;top:-9999px;';
  pre.textContent = JSON.stringify(els);
  document.body.appendChild(pre);
})();
"""

    # 0.9's untrusted-config boundary forbids js_code, so the summary
    # injection rides in the wait_for predicate: wait for load-complete,
    # inject the <pre> once, and report ready when it exists (matching the
    # old js_code-after-load timing).
    inject_wait = (
        "js:() => {"
        " if (document.readyState !== 'complete') return false;"
        " if (!document.getElementById('__klai_dom_summary__')) { " + dom_js + " return false; }"
        " return true;"
        " }"
    )
    config: dict[str, Any] = {
        "cache_mode": "bypass",
        "wait_for": inject_wait,
        "css_selector": "#__klai_dom_summary__",
        "word_count_threshold": 0,
        "page_timeout": 30000,
        "remove_consent_popups": True,
    }
    payload = {
        "urls": [url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            data = await _crawl_sync(client, payload)

        results = data.get("results", [])
        if isinstance(results, dict):
            results = [results]
        if not results:
            return None

        md = results[0].get("markdown", "")
        if isinstance(md, dict):
            raw = md.get("raw_markdown", "") or ""
        else:
            raw = md or ""

        raw = raw.strip()
        if not raw:
            return None
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("crawl4ai_dom_summary_failed", url=url, error=str(exc))
        return None
