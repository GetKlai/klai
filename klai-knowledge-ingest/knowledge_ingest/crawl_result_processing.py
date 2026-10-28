"""Pure normalization of Crawl4AI page results."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

from knowledge_ingest.crawl4ai_types import CrawlResult

THIN_CONTENT_WORD_COUNT = 100
_MUSTACHE_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")
_MD_LINK_URL_RE = re.compile(r"\]\([^)]*\)")
# Not an image: "![alt](src)" is not a link to restore.
_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\([^)]*\)")


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


def html_visible_text(html: str) -> str:
    """Return visible HTML text (script/style/svg stripped) without rendering."""
    if not html:
        return ""
    parser = _HTMLTextCounter()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return unescape(" ".join(parser.parts))


def html_text_word_count(html: str) -> int:
    """Count visible HTML words without executing or rendering the document."""
    return len(html_visible_text(html).split())


_COMPOUND_RE = re.compile(r"^([A-Za-z][\w-]*|\*)?((?:#[\w-]+|\.[\w-]+|\[[^\]]*\])*)$")
_COMPOUND_PART_RE = re.compile(r"#([\w-]+)|\.([\w-]+)|(\[[^\]]*\])")
_ATTR_RE = re.compile(
    r"""\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*)?\]"""
)
_ATTR_OPS: dict[str, Callable[[str, str], bool]] = {
    "=": lambda have, want: have == want,
    "^=": lambda have, want: bool(want) and have.startswith(want),
    "$=": lambda have, want: bool(want) and have.endswith(want),
    "*=": lambda have, want: bool(want) and want in have,
    "~=": lambda have, want: want in have.split(),
    "|=": lambda have, want: have == want or have.startswith(want + "-"),
}


@dataclass(frozen=True)
class _Compound:
    tag: str | None
    ids: tuple[str, ...]
    classes: tuple[str, ...]
    attrs: tuple[tuple[str, str, str], ...]

    def matches(self, tag: str, attrs: dict[str, str]) -> bool:
        if self.tag not in (None, "*", tag):
            return False
        if any(attrs.get("id") != i for i in self.ids):
            return False
        have_classes = attrs.get("class", "").split()
        if any(c not in have_classes for c in self.classes):
            return False
        return all(
            name in attrs and (not op or _ATTR_OPS[op](attrs[name], want))
            for name, op, want in self.attrs
        )


def _parse_selector(selector: str) -> list[_Compound]:
    """Parse a comma list of compound selectors (tag, #id, .class, [attr op value]).

    Combinators and pseudo-classes are refused instead of guessed at: a wall
    indicator that silently never matches would ingest the login page.
    """
    compounds: list[_Compound] = []
    for text in re.split(r",(?![^\[]*\])", selector):
        match = _COMPOUND_RE.match(text.strip())
        if not text.strip() or match is None:
            raise ValueError(f"Unsupported login indicator selector: {selector!r}")
        ids: list[str] = []
        classes: list[str] = []
        attrs: list[tuple[str, str, str]] = []
        for ident, cls, attr in _COMPOUND_PART_RE.findall(match.group(2)):
            if ident:
                ids.append(ident)
            elif cls:
                classes.append(cls)
            else:
                attr_match = _ATTR_RE.fullmatch(attr)
                if attr_match is None:
                    raise ValueError(f"Unsupported login indicator selector: {selector!r}")
                value = next((g for g in attr_match.groups()[2:] if g is not None), "")
                attrs.append((attr_match.group(1).lower(), attr_match.group(2) or "", value))
        tag = match.group(1)
        compounds.append(
            _Compound(tag.lower() if tag else None, tuple(ids), tuple(classes), tuple(attrs))
        )
    return compounds


class _ElementFinder(HTMLParser):
    def __init__(self, compounds: list[_Compound]) -> None:
        super().__init__()
        self._compounds = compounds
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.found:
            have = {name: value or "" for name, value in attrs}
            self.found = any(c.matches(tag.lower(), have) for c in self._compounds)


def parse_login_indicator(selector: str) -> None:
    """Raise ValueError when ``selector`` uses CSS the matcher does not support."""
    _parse_selector(selector)


def html_has_element(html: str, selector: str) -> bool:
    """Whether fetched HTML contains an element matching ``selector``.

    Replaces the page-side ``!document.querySelector(selector)`` readiness
    check: crawl4ai 0.9.5 refuses JavaScript in a network request, so the
    login-wall indicator is evaluated on the HTML that came back.
    """
    finder = _ElementFinder(_parse_selector(selector))
    finder.feed(html)
    return finder.found


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


def restore_pruned_link_labels(fit: str, raw: str) -> str:
    """Put back link labels that ``PruningContentFilter`` removed from ``fit``.

    The filter scores a wrapper element (``<span><a>..</a></span>``,
    ``<strong><a>..</a></strong>``) before its children and removes it when it is
    mostly link text, so fit_markdown reads "Ga naar ." where raw_markdown has
    "Ga naar [Instellingen](url)". Turning on ``preserve_tags`` for the wrappers
    instead keeps navigation chrome as well (+63% text on navigation pages,
    SPEC-RAG-INTAKE-QUALITY-001 section 11), so this restores only the proven case:
    a fit line whose words, with every link removed, equal those of exactly one raw
    line, and that line has a link. The fit line is replaced by the raw line; no
    other raw text comes back.
    """
    if not fit or not raw or fit == raw:
        return fit
    # Every raw line counts, linked or not: a fit line that could belong to a
    # linked and to an unlinked raw line is ambiguous and stays as it is.
    by_key: dict[tuple[str, ...], set[str]] = {}
    for line in raw.splitlines():
        key = _unlinked_words(line)
        if key:
            by_key.setdefault(key, set()).add(line)
    out: list[str] = []
    for line in fit.splitlines():
        candidates = by_key.get(_unlinked_words(line), ())
        match = next(iter(candidates)) if len(candidates) == 1 else None
        out.append(match if match and _MD_LINK_RE.search(match) else line)
    return "\n".join(out) + ("\n" if fit.endswith("\n") else "")


def _unlinked_words(line: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", _MD_LINK_RE.sub(" ", line)))


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
    fit = restore_pruned_link_labels(fit, raw)
    text = fit or raw
    return CrawlResult(
        url=page.get("url", url),
        fit_markdown=fit,
        raw_markdown=raw,
        html=page.get("html", ""),
        cleaned_html=page.get("cleaned_html", "") or "",
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
