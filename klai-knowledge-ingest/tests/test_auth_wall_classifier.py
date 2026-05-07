"""Tests for the pure-function auth-wall classifier (REQ-2).

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-2 / REQ-6.

The classifier is the single source of truth for "is this page authenticated
content or an auth-wall stub?" — used by:

- ``/ingest/v1/crawl/auth-probe`` (REQ-2 endpoint): step-4 wizard validation
- ``/ingest/v1/crawl/preview`` (REQ-3 endpoint): step-5 selector validation
- ``crawl_site`` post-fetch guard (REQ-4): defense-in-depth at sync time

These tests pin the contract for all three call-sites. Adding a new sub-rule
or relaxing an existing one MUST be paired with a test here.
"""

from __future__ import annotations

import pytest

from knowledge_ingest.utils.auth_wall_classifier import (
    AUTH_WALL_END_OF_BODY_MARKERS,
    AuthWallClassification,
    classify_auth_wall,
)


# ---------------------------------------------------------------------------
# HTTP-status sub-rules
# ---------------------------------------------------------------------------


def test_http_401_classified_as_walled() -> None:
    result = classify_auth_wall(
        response_status_code=401,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=0,
        fit_markdown="",
        raw_html="",
    )
    assert result.is_walled is True
    assert "http_unauthenticated" in result.match_reasons


def test_http_403_classified_as_walled() -> None:
    result = classify_auth_wall(
        response_status_code=403,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=0,
        fit_markdown="",
        raw_html="",
    )
    assert result.is_walled is True
    assert "http_unauthenticated" in result.match_reasons


def test_http_200_does_not_match_unauthenticated() -> None:
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=500,
        fit_markdown="A real article body with plenty of words.",
        raw_html="<html></html>",
    )
    assert "http_unauthenticated" not in result.match_reasons


# ---------------------------------------------------------------------------
# Redirect sub-rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "redirect_path",
    [
        "https://example.com/login",
        "https://example.com/signin",
        "https://example.com/sign-in",
        "https://example.com/aanmelden",
        "https://example.com/inloggen",
        "https://example.com/anmelden",
        "https://example.com/connexion",
        "https://example.com/accedi",
        "https://example.com/users/login?return_to=/foo",  # nested path
    ],
)
def test_redirect_to_login_path_classified_as_walled(redirect_path: str) -> None:
    result = classify_auth_wall(
        response_status_code=302,
        redirect_target_url=redirect_path,
        set_cookie_header=None,
        word_count=0,
        fit_markdown="",
        raw_html="",
    )
    assert result.is_walled is True
    assert "redirect_to_login" in result.match_reasons


def test_redirect_to_unrelated_url_does_not_match_redirect_rule() -> None:
    result = classify_auth_wall(
        response_status_code=302,
        redirect_target_url="https://example.com/articles/welcome",
        set_cookie_header=None,
        word_count=500,
        fit_markdown="A real article.",
        raw_html="",
    )
    assert "redirect_to_login" not in result.match_reasons


# ---------------------------------------------------------------------------
# Session-cookie sub-rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "set_cookie",
    [
        "prod-knowledgebase-session=abc; Path=/",
        "PHPSESSID=xyz; Path=/",
        "auth_token=xxx; Path=/",
        "sid=qqq; Path=/",
        "some-thing-session-id=abc; Path=/",
    ],
)
def test_session_cookie_with_minimal_body_classified_as_walled(set_cookie: str) -> None:
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=set_cookie,
        word_count=20,
        fit_markdown="Please log in",
        raw_html="<html></html>",
    )
    assert result.is_walled is True
    assert "session_cookie_minimal_body" in result.match_reasons


def test_session_cookie_with_full_body_does_not_match() -> None:
    """Logged-in pages also set session cookies; only flag when body is thin."""
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header="sid=xyz; Path=/",
        word_count=500,
        fit_markdown="A complete article with many words " * 30,
        raw_html="<html></html>",
    )
    assert "session_cookie_minimal_body" not in result.match_reasons


def test_non_session_cookie_does_not_match() -> None:
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header="theme=dark; Path=/",
        word_count=20,
        fit_markdown="Short page",
        raw_html="<html></html>",
    )
    assert "session_cookie_minimal_body" not in result.match_reasons


# ---------------------------------------------------------------------------
# End-of-body marker sub-rule (REQ-2 + D-13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tail",
    [
        "Log in to read this article",
        "Log in to view this",
        "log in to continue",
        "Sign in to read this",
        "sign in to view more",
        "Please log in",
        "Even inloggen",
        "Inloggen om verder te lezen",
        "Inloggen om dit te bekijken",
        "Aanmelden om verder te lezen",
        "Log in when you want to read this article",
    ],
)
def test_end_of_body_login_marker_classified_as_walled(tail: str) -> None:
    fit_markdown = "This is a teaser of an article body. " * 5 + " " + tail
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=80,  # short stub typical of teaser pages
        fit_markdown=fit_markdown,
        raw_html="<html></html>",
    )
    assert result.is_walled is True
    assert "end_of_body_login_marker" in result.match_reasons


def test_long_article_with_login_link_in_nav_does_not_match_marker() -> None:
    """D-13: marker must be at END of body, not anywhere.

    Healthy SaaS pages have a "Log in" link in the global nav header. Those
    must NOT trip the auth-wall heuristic. The marker check looks at the LAST
    ~200 chars of the body only.
    """
    body = (
        "Log in | Sign up | Help\n\n"  # nav header text up front
        + "Welcome to our article. This article is about how to use the API. "
        * 60
        + "Read more at the end of the article. The conclusion is left as an "
        + "exercise to the reader.\n"
    )
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=800,
        fit_markdown=body,
        raw_html="<html></html>",
    )
    assert "end_of_body_login_marker" not in result.match_reasons
    assert result.is_walled is False


def test_marker_in_middle_of_body_does_not_match() -> None:
    body = (
        "Article start, lots of content. "
        "log in to read this article in the middle of the body. "
        + "More content following the supposed marker, plenty of words. " * 30
    )
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=500,
        fit_markdown=body,
        raw_html="<html></html>",
    )
    # The marker MUST NOT trigger because the body continues for many words
    # after it. The end-of-body region is clean prose.
    assert "end_of_body_login_marker" not in result.match_reasons


# ---------------------------------------------------------------------------
# Password-form sub-rule
# ---------------------------------------------------------------------------


def test_password_form_with_minimal_body_classified_as_walled() -> None:
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=15,
        fit_markdown="Please enter your password",
        raw_html='<html><body><form><input type="password" name="pw"></form></body></html>',
    )
    assert result.is_walled is True
    assert "password_form_minimal_body" in result.match_reasons


def test_password_form_with_full_article_does_not_match() -> None:
    """A long article that happens to embed a password change form is not a wall."""
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=600,
        fit_markdown="A long article body. " * 80,
        raw_html='<html><body><form><input type="password" name="pw"></form></body></html>',
    )
    assert "password_form_minimal_body" not in result.match_reasons


# ---------------------------------------------------------------------------
# Negative case
# ---------------------------------------------------------------------------


def test_healthy_public_page_not_classified_as_walled() -> None:
    result = classify_auth_wall(
        response_status_code=200,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=600,
        fit_markdown=(
            "This is a real, long article body with plenty of words and no "
            "auth markers anywhere. " * 30
        ),
        raw_html="<html><body><article>...</article></body></html>",
    )
    assert result.is_walled is False
    assert result.match_reasons == ()


# ---------------------------------------------------------------------------
# Combination cases
# ---------------------------------------------------------------------------


def test_multiple_subrules_match_returns_all() -> None:
    """The classifier must return ALL matching reasons for diagnostic UI."""
    result = classify_auth_wall(
        response_status_code=401,
        redirect_target_url=None,
        set_cookie_header="sid=xxx; Path=/",
        word_count=30,
        fit_markdown="Some short text. Please log in",
        raw_html='<html><body><form><input type="password"></form></body></html>',
    )
    assert result.is_walled is True
    assert "http_unauthenticated" in result.match_reasons
    assert "session_cookie_minimal_body" in result.match_reasons
    assert "password_form_minimal_body" in result.match_reasons
    assert "end_of_body_login_marker" in result.match_reasons


def test_classification_is_immutable() -> None:
    """The result dataclass is frozen — callers cannot mutate diagnostic data."""
    result = classify_auth_wall(
        response_status_code=401,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=0,
        fit_markdown="",
        raw_html="",
    )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        result.is_walled = False  # type: ignore[misc]
    with pytest.raises(Exception):
        result.match_reasons = ()  # type: ignore[misc]


def test_empty_input_is_not_walled() -> None:
    """Edge: every input empty/None — no signal, no false positive."""
    result = classify_auth_wall(
        response_status_code=None,
        redirect_target_url=None,
        set_cookie_header=None,
        word_count=0,
        fit_markdown="",
        raw_html="",
    )
    assert result.is_walled is False
    assert result.match_reasons == ()


def test_marker_list_is_module_level_constant() -> None:
    """REQ-2 D-12: marker list MUST be a module-level constant for easy DE/FR
    addition without code restructuring."""
    assert isinstance(AUTH_WALL_END_OF_BODY_MARKERS, (list, tuple))
    assert len(AUTH_WALL_END_OF_BODY_MARKERS) >= 5  # NL + EN starter set


def test_classification_dataclass_shape() -> None:
    result = AuthWallClassification(is_walled=True, match_reasons=("http_unauthenticated",))
    assert result.is_walled is True
    assert result.match_reasons == ("http_unauthenticated",)
