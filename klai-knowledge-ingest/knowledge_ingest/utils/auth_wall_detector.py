"""Anonymous-crawl login-wall detector.

SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-01, REQ-02.

A pure synchronous function that scans crawled markdown for signs that the page
is a login-walled stub captured during an anonymous (no-cookies) crawl.

Why this exists: ``SPEC-CRAWLER-004`` already covers authenticated crawls (a
DOM ``login_indicator_selector`` is injected into ``wait_for`` and ``crawl4ai``
returns ``success=False`` if the selector matches). That guard does NOT fire
on anonymous crawls of public pages that *contain* a login prompt — those
return ``success=True`` and look like normal content. Voys's support KB
contained 150 such stubs (35% of the redcactus sub-tree) before this detector
existed. See ``docs/retros/2026-05-06-redcactus-hubspot-login-walls.md``
(future).

Design notes:

- ``Condition A`` (canonical phrase) is a STRONG signal. Phrases like "you
  will have to log in with your X account" do not occur in legitimate
  tutorial / product / documentation content. A single match flags the page
  regardless of surrounding word volume — verified against the captured
  RedCactus fixtures, which contain 3243 content words alongside two canonical
  phrases.
- Conditions ``B`` / ``C`` / ``D`` (redirect density, login-link repetition,
  content-to-login ratio) are WEAK signals. A clean tutorial page about
  authentication may produce them. The false-positive guard only protects
  against false positives from these three.
- The function performs no I/O, emits no logs, has no global state, and
  returns identical output for identical input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthWallSignal:
    """Outcome of a positive detection.

    Attributes:
        pattern: One of ``canonical_phrase_en``, ``canonical_phrase_nl``,
            ``redirect_density``, ``login_link_repetition``,
            ``content_login_ratio``.
        evidence: Short human-readable strings supporting the match (matched
            substring, count, etc.). Logged for diagnostics; not user-facing.
        confidence: ``0.9`` for canonical-phrase matches (strong signal),
            ``0.7`` for weak-signal matches (B/C/D) that survive the FP-guard.
    """

    pattern: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Tunables — kept module-level constants so they can be unit-tested in
# isolation and reasoned about without re-reading the function body.
# ---------------------------------------------------------------------------

# Condition A — canonical phrase substrings (case-insensitive, single-pass).
# Each phrase has been verified against either captured production fixtures
# (RedCactus) or representative synthetic CMS fixtures (Confluence, WordPress,
# Notion). Adding a phrase requires a fixture proving it appears in the wild.
_CANONICAL_EN: tuple[str, ...] = (
    "log in to read",
    "log in to view",
    "sign in to continue",
    "sign in to view",
    "have to log in",
    "need to log in",
    "need to sign in",
    "log in with your",
    "sign in with your",
    "this article requires authentication",
    "please sign in",
    "please log in",
)

_CANONICAL_NL: tuple[str, ...] = (
    "in te loggen",  # covers "u dient in te loggen"
    "log in om",  # covers "log in om dit te lezen"
    "meld u aan",  # covers "meld u aan om verder te gaan"
    "aanmelden om",  # covers "aanmelden om verder te gaan"
)

# Condition B — substring count threshold.
_REDIRECT_TOKEN = "redirect_to="
_REDIRECT_MIN_COUNT = 5

# Condition C — same login-href repeated.
_LOGIN_HREF_PATTERN = re.compile(
    r"https?://[^\s)]+(?:/login|/sign-in|/signin|/auth/)[^\s)]*",
    flags=re.IGNORECASE,
)
_LOGIN_HREF_MIN_REPETITION = 5

# Condition D — short content + repeated login anchors.
# Markdown anchor: [text](url). We count anchors whose URL looks like a login
# endpoint, regardless of whether the URL repeats.
_MD_ANCHOR_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_LOGIN_URL_HINT = re.compile(r"/(login|sign-in|signin|auth/)", flags=re.IGNORECASE)
_CONTENT_RATIO_MAX_CONTENT_WORDS = 100
_CONTENT_RATIO_MIN_LOGIN_ANCHORS = 3

# False-positive guard thresholds.
_FP_GUARD_RAW_MIN_CONTENT_WORDS = 500
_FP_GUARD_FIT_MIN_CONTENT_WORDS = 200


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_anonymous_auth_wall(
    markdown: str,
    *,
    fit_markdown: str | None = None,
    url: str | None = None,  # reserved for future per-domain rules
) -> AuthWallSignal | None:
    """Return a signal if ``markdown`` looks like a login-walled stub.

    Returns ``None`` for clean content. The function is pure: no logging, no
    I/O, no global state mutation. See module docstring for design rationale.

    Args:
        markdown: ``raw_markdown`` from the crawl result. Includes navigation
            chrome and login redirects.
        fit_markdown: Optional ``fit_markdown`` from the crawl result. When
            available, takes precedence as the primary content view because it
            has page chrome stripped. Used both for canonical-phrase detection
            (override clean raw → dirty fit) and for FP-guard veto (clean fit
            → veto weak signals from raw).
        url: Page URL. Reserved for future per-domain heuristics (e.g.,
            wiki.redcactus.cloud-specific patterns); unused in v1.
    """
    if not markdown and not fit_markdown:
        return None

    raw = markdown or ""
    fit = fit_markdown or ""

    # Condition A — canonical phrase (STRONG signal). Look in BOTH views;
    # a phrase in fit OR raw is enough. This is the only signal that the
    # FP-guard does not override.
    canonical = _match_canonical(raw) or _match_canonical(fit)
    if canonical is not None:
        return canonical

    # Conditions B/C/D — WEAK signals. Evaluate on raw (chrome-rich), since
    # crawl4ai's fit-extractor strips most navigation/login chrome.
    weak = (
        _match_redirect_density(raw)
        or _match_login_link_repetition(raw)
        or _match_content_login_ratio(raw)
    )
    if weak is None:
        return None

    # FP-guard — protects WEAK signals only.
    if _fp_guard_vetoes(raw, fit):
        return None

    return weak


# ---------------------------------------------------------------------------
# Condition A — canonical phrase
# ---------------------------------------------------------------------------


def _match_canonical(text: str) -> AuthWallSignal | None:
    if not text:
        return None
    lower = text.lower()
    for phrase in _CANONICAL_EN:
        if phrase in lower:
            return AuthWallSignal(
                pattern="canonical_phrase_en",
                evidence=(phrase,),
                confidence=0.95,
            )
    for phrase in _CANONICAL_NL:
        if phrase in lower:
            return AuthWallSignal(
                pattern="canonical_phrase_nl",
                evidence=(phrase,),
                confidence=0.95,
            )
    return None


# ---------------------------------------------------------------------------
# Condition B — redirect_to= density
# ---------------------------------------------------------------------------


def _match_redirect_density(text: str) -> AuthWallSignal | None:
    count = text.count(_REDIRECT_TOKEN)
    if count < _REDIRECT_MIN_COUNT:
        return None
    return AuthWallSignal(
        pattern="redirect_density",
        evidence=(f"{count} {_REDIRECT_TOKEN} occurrences",),
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# Condition C — same login href repeated
# ---------------------------------------------------------------------------


def _match_login_link_repetition(text: str) -> AuthWallSignal | None:
    hrefs = _LOGIN_HREF_PATTERN.findall(text)
    if len(hrefs) < _LOGIN_HREF_MIN_REPETITION:
        return None
    # Count repetitions of the most common href.
    counts: dict[str, int] = {}
    for h in hrefs:
        counts[h] = counts.get(h, 0) + 1
    top_href, top_count = max(counts.items(), key=lambda kv: kv[1])
    if top_count < _LOGIN_HREF_MIN_REPETITION:
        # Many distinct login URLs but none repeats enough — fall through to
        # the next condition.
        return None
    return AuthWallSignal(
        pattern="login_link_repetition",
        evidence=(f"{top_href} repeated {top_count}x",),
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# Condition D — content-to-login ratio
# ---------------------------------------------------------------------------


def _match_content_login_ratio(text: str) -> AuthWallSignal | None:
    anchors = _MD_ANCHOR_PATTERN.findall(text)
    login_anchors = [
        (anchor_text, url) for anchor_text, url in anchors if _LOGIN_URL_HINT.search(url)
    ]
    if len(login_anchors) < _CONTENT_RATIO_MIN_LOGIN_ANCHORS:
        return None

    content_words = _count_non_login_content_words(text)
    if content_words >= _CONTENT_RATIO_MAX_CONTENT_WORDS:
        return None

    return AuthWallSignal(
        pattern="content_login_ratio",
        evidence=(f"{content_words} content words, {len(login_anchors)} login anchors",),
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# False-positive guard
# ---------------------------------------------------------------------------


def _fp_guard_vetoes(raw: str, fit: str) -> bool:
    """Return True if a WEAK-signal match should be vetoed.

    Two-layer veto:
      1. If fit_markdown is non-empty and clean (no canonical phrase, redirect
         count below threshold) AND has >= 200 content words → veto. The
         fit-extractor strips chrome, so a clean fit means the actual article
         body is real content even if the raw chrome is noisy.
      2. Else if raw has >= 500 non-login content words → veto. A page with
         that much content alongside weak login signals is more likely a
         real article with login chrome than a stub.
    """
    if fit:
        fit_dirty = (
            _match_canonical(fit) is not None or fit.count(_REDIRECT_TOKEN) >= _REDIRECT_MIN_COUNT
        )
        if not fit_dirty:
            fit_words = _count_non_login_content_words(fit)
            if fit_words >= _FP_GUARD_FIT_MIN_CONTENT_WORDS:
                return True

    raw_words = _count_non_login_content_words(raw)
    return raw_words >= _FP_GUARD_RAW_MIN_CONTENT_WORDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_LOGIN_ANCHOR_STRIP = re.compile(
    r"\[([^\]]+)\]\(([^)]*(?:/login|/sign-in|/signin|/auth/)[^)]*)\)",
    flags=re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ]+")


def _count_non_login_content_words(text: str) -> int:
    """Word count after removing markdown anchors that point to login URLs.

    Strips ``[text](url)`` pairs whose URL matches a login endpoint. The
    anchor's display text is removed too — we are measuring real content,
    not link labels. Then counts alphabetic word tokens via a simple regex.
    """
    if not text:
        return 0
    stripped = _LOGIN_ANCHOR_STRIP.sub(" ", text)
    return len(_WORD_RE.findall(stripped))
