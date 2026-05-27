"""Pure-function auth-wall classifier shared across three call-sites.

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-2.

@MX:ANCHOR fan_in=3 — invariant. Three call-sites depend on the contract of
``classify_auth_wall``:

1. ``routes/crawl.py::auth_probe`` — REQ-2 step-4 wizard validation.
2. ``routes/crawl.py::preview_crawl`` — REQ-3 step-5 selector validation
   (calls the classifier alongside link-density to decide success vs
   ``auth_wall_detected``).
3. ``adapters/crawler.py`` post-fetch guard — REQ-4 sync-time defense in
   depth.

Reason: collapsing the three would silently re-introduce the failure
mode that produced this SPEC (a connector ingesting nav stubs because
the front door checked one thing and the back door checked another).
The classifier is the single source of truth.

@MX:NOTE — ``AUTH_WALL_END_OF_BODY_MARKERS`` is a localised marker list.
Adding DE/FR is a one-line append. Do not refactor the regex shape
without checking every entry — markers are written to match end-of-body
patterns; reshaping them risks false positives in nav menus (D-13).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

__all__ = [
    "AUTH_WALL_EMBEDDED_GATE_MARKERS",
    "AUTH_WALL_END_OF_BODY_MARKERS",
    "LOGIN_PATH_REGEX",
    "SESSION_COOKIE_REGEX",
    "AuthWallClassification",
    "classify_auth_wall",
]


# ---------------------------------------------------------------------------
# Module-level constants (D-12, D-13)
# ---------------------------------------------------------------------------

# REQ-2 D-12: NL + EN initial coverage. DE/FR can be appended without
# restructuring. Each pattern is a regex matched case-insensitively at the
# tail of ``fit_markdown``.
AUTH_WALL_END_OF_BODY_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # English "log/sign in to <verb>" — trailing "this|more" is optional
        # (e.g. "log in to continue" without trailing object).
        r"(log|sign) in to (read|view|continue|see)(?:\s+(this|more))?",
        # English imperative — "please log in", "even sign in"
        r"(please|even) (log|sign) in",
        # English long-form
        r"log in when you want to (read|view) this article",
        # Dutch — "even inloggen" / "even aanmelden" (informal "please log in")
        r"(even|graag)\s+(inloggen|aanmelden)",
        # Dutch — full clause "inloggen om verder te lezen"
        r"inloggen om (verder|dit) (te lezen|te bekijken)",
        r"aanmelden om (verder|dit) (te lezen|te bekijken)",
    )
)

# Embedded login gates. These may appear once in the middle of an otherwise
# large page when a public page contains one protected section/tab. To avoid
# false positives from nav links or articles about logging in, the marker
# requires a real login link/URL plus a nearby "read/view/access content"
# directive inside the same short block.
AUTH_WALL_EMBEDDED_GATE_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\[(?:[^\]]{0,80})?"
        r"(?:log\s*in|sign\s*in|login|signin|inloggen|aanmelden|anmelden|connexion|accedi)"
        r"[^\]]{0,80}\]\([^)]{0,240}"
        r"(?:login|signin|sign-?in|inloggen|aanmelden|anmelden|connexion|accedi)"
        r"[^)]{0,240}\)[\s\S]{0,500}"
        r"(?:read|view|continue|access|see|open|lezen|bekijken|verder|toegang)"
        r"[\s\S]{0,120}"
        r"(?:article|page|content|document|artikel|pagina|inhoud|document)",
    )
)

# Match a login path component anywhere in a redirect URL path. Allows
# leading path segments (``/users/login``) and trailing query/fragment
# (``/login?return_to=...``). Boundaries prevent matching prefixes like
# ``/blogin`` or substrings like ``/sublogin/foo``.
LOGIN_PATH_REGEX: re.Pattern[str] = re.compile(
    r"(?:^|/)(login|signin|sign-?in|aanmelden|inloggen|anmelden|connexion|accedi)(?:/|$|\?|#)",
    re.IGNORECASE,
)

# A "session cookie" is anything whose name contains session/sid/auth_token.
# Matched against the FULL ``Set-Cookie`` header; the header is not parsed
# semantically because we only need a boolean signal.
SESSION_COOKIE_REGEX: re.Pattern[str] = re.compile(
    r"(?:^|;\s*)([\w\-]*(session|sid|auth_?token)[\w\-]*)\s*=", re.IGNORECASE
)

# REQ-2: shared with ``routes/crawl.py`` (kept in-sync; that module already
# defines its own ``_MIN_WORD_COUNT = 100`` per D-5 rationale).
_MIN_WORD_COUNT_FOR_AUTH_HEURISTIC = 100

# REQ-2 D-13: only inspect the END of the body for login markers. A nav
# header containing "Log in" anywhere on the page must NOT trip the
# heuristic. 200 chars is roughly two short paragraphs of trailing text.
_END_OF_BODY_WINDOW_CHARS = 200

# Password-form HTML signature. Lower-cased substring search is fine — we
# only care about the presence of a password input on a near-empty page.
_PASSWORD_INPUT_PATTERN = re.compile(r'<input[^>]*type\s*=\s*["\']?password["\']?', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthWallClassification:
    """Outcome of a single auth-wall classification.

    Attributes:
        is_walled: True when ANY sub-rule matched.
        match_reasons: Tuple of sub-rule identifiers that matched. Returned
            even when ``is_walled`` is True so callers can render specific
            user feedback ("session cookie + minimal body" vs "401 status").
    """

    is_walled: bool
    match_reasons: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_auth_wall(
    *,
    response_status_code: int | None,
    redirect_target_url: str | None,
    set_cookie_header: str | None,
    word_count: int,
    fit_markdown: str,
    raw_html: str,
) -> AuthWallClassification:
    """Apply auth-wall sub-rules and return the aggregate classification.

    Sub-rules (any match flips ``is_walled`` to True):

    1. ``http_unauthenticated`` — HTTP 401 / 403.
    2. ``redirect_to_login`` — HTTP 30x where ``Location`` path matches
       ``LOGIN_PATH_REGEX`` (login, signin, aanmelden, inloggen,
       anmelden, connexion, accedi).
    3. ``session_cookie_minimal_body`` — ``Set-Cookie`` matches
       ``SESSION_COOKIE_REGEX`` AND ``word_count`` is below the auth
       heuristic threshold.
    4. ``end_of_body_login_marker`` — last
       ``_END_OF_BODY_WINDOW_CHARS`` of ``fit_markdown`` matches any
       pattern in ``AUTH_WALL_END_OF_BODY_MARKERS``.
    5. ``embedded_login_gate`` — ``fit_markdown`` contains an isolated
       login link/URL with a nearby read/view/access-content directive.
    6. ``password_form_minimal_body`` — ``raw_html`` contains an
       ``<input type="password">`` AND ``word_count`` is below the
       threshold.

    Args:
        response_status_code: HTTP status of the original page request.
            ``None`` when the upstream did not record a status (e.g. raw
            connection error). Treated as "no signal".
        redirect_target_url: Full URL of the final redirect target if any
            (the page request followed a 30x). ``None`` when no redirect
            occurred.
        set_cookie_header: Raw ``Set-Cookie`` response header value as a
            single string. Multiple cookies separated by ``,`` or ``;``
            are treated as concatenated text — we only need a regex hit.
        word_count: Word count of the page after content extraction.
        fit_markdown: ``fit_markdown`` from the crawl result (post
            ``PruningContentFilter``).
        raw_html: Raw HTML body. Used only for the password-form
            sub-rule.

    Returns:
        ``AuthWallClassification`` with all matching reasons.
    """
    reasons: list[str] = []

    # Rule 1 — HTTP unauthenticated
    if response_status_code in (401, 403):
        reasons.append("http_unauthenticated")

    # Rule 2 — redirect to login path
    if redirect_target_url:
        try:
            parsed = urlparse(redirect_target_url)
            if parsed.path and LOGIN_PATH_REGEX.search(parsed.path):
                reasons.append("redirect_to_login")
        except (ValueError, AttributeError):
            # Malformed URL — never raise from a classifier; just don't
            # match this rule.
            pass

    # Rule 3 — session cookie + minimal body
    if (
        set_cookie_header
        and SESSION_COOKIE_REGEX.search(set_cookie_header)
        and word_count < _MIN_WORD_COUNT_FOR_AUTH_HEURISTIC
    ):
        reasons.append("session_cookie_minimal_body")

    # Rule 4 — end-of-body login marker (D-13: tail-only, not anywhere).
    # Single marker at end of body = teaser article. Pinned to tail so a
    # nav-header "Log in" link doesn't false-positive long articles.
    if fit_markdown:
        tail = fit_markdown[-_END_OF_BODY_WINDOW_CHARS:]
        for pattern in AUTH_WALL_END_OF_BODY_MARKERS:
            if pattern.search(tail):
                reasons.append("end_of_body_login_marker")
                break

    # Rule 4b — repeated marker anywhere in body. Real production case
    # (wiki.redcactus.cloud article pages with multiple tabs, 2026-05-07):
    # the auth-wall message "Log in when you want to read this article"
    # appears ONCE PER TAB, in the middle of the body — never at the end.
    # D-13 alone misses these. But a single phrase repeating 2+ times in
    # the body is essentially never a false positive (legitimate articles
    # do not repeat that exact stub clause).
    if fit_markdown:
        marker_hits = sum(
            len(pattern.findall(fit_markdown)) for pattern in AUTH_WALL_END_OF_BODY_MARKERS
        )
        if marker_hits >= 2 and "end_of_body_login_marker" not in reasons:
            reasons.append("repeated_login_marker_in_body")

    # Rule 4c — embedded protected section anywhere in body. A single protected
    # section can sit in the middle of otherwise-long extracted content.
    # Tail-only and repeated-marker checks miss this; requiring a login link
    # plus a nearby content-access directive keeps the rule generic and tight.
    if fit_markdown:
        for pattern in AUTH_WALL_EMBEDDED_GATE_MARKERS:
            if pattern.search(fit_markdown):
                reasons.append("embedded_login_gate")
                break

    # Rule 5 — password form on a thin page
    if (
        raw_html
        and word_count < _MIN_WORD_COUNT_FOR_AUTH_HEURISTIC
        and _PASSWORD_INPUT_PATTERN.search(raw_html)
    ):
        reasons.append("password_form_minimal_body")

    return AuthWallClassification(
        is_walled=bool(reasons),
        match_reasons=tuple(reasons),
    )
