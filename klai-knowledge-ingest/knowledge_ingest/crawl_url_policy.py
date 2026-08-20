"""Pure URL normalization and filtering policy for website crawls."""

from __future__ import annotations

import fnmatch
from urllib.parse import unquote, urldefrag, urlparse, urlunparse

_NON_HTML_PATH_EXTENSIONS = frozenset(
    {
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "odt",
        "zip",
        "tar",
        "gz",
        "rar",
        "7z",
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
        "exe",
        "dmg",
        "pkg",
        "deb",
    }
)

_TEMPLATE_PLACEHOLDER_PATTERNS = ("{{", "}}", "${", "<%", "%>", "{%", "%}")


def url_has_non_html_extension(url: str) -> bool:
    """Return whether the URL path ends in a document, archive, media or binary."""
    last_segment = urlparse(url).path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return False
    return last_segment.rsplit(".", 1)[-1].lower() in _NON_HTML_PATH_EXTENSIONS


def url_has_unrendered_template_syntax(url: str) -> bool:
    """Return whether a raw or decoded URL contains template placeholders."""
    decoded = unquote(url)
    return any(pattern in url or pattern in decoded for pattern in _TEMPLATE_PLACEHOLDER_PATTERNS)


def canonicalise_url(url: str) -> str:
    """Normalize scheme, host, fragment and trailing slash for URL deduplication."""
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
            "",
        )
    )


def same_site_domain(host: str, base_domain: str) -> bool:
    """Treat apex and www variants as the same site."""

    def host_key(value: str) -> str:
        normalized = (value or "").lower()
        return normalized[4:] if normalized.startswith("www.") else normalized

    return host_key(host) == host_key(base_domain)


def coerce_same_site_url_to_base_host(url: str, base_domain: str) -> str:
    """Use the connector's configured host for apex/www sitemap variants."""
    parsed = urlparse(url)
    if not same_site_domain(parsed.netloc.lower(), base_domain):
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


def url_matches_include_patterns(url: str, patterns: list[str] | None) -> bool:
    """Apply crawl4ai-compatible glob or legacy substring include patterns."""
    if not patterns:
        return True
    path = urlparse(url).path
    return any(
        fnmatch.fnmatch(path, pattern) if "*" in pattern or "?" in pattern else pattern in url
        for pattern in patterns
    )


def url_matches_patterns(url: str, patterns: list[str] | None) -> bool:
    """Apply crawl4ai-compatible glob or legacy substring exclusion patterns."""
    if not patterns:
        return False
    path = urlparse(url).path
    return any(
        fnmatch.fnmatch(path, pattern) if "*" in pattern or "?" in pattern else pattern in url
        for pattern in patterns
    )
