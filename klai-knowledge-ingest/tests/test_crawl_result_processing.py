"""html_has_element: the login-wall indicator check on fetched HTML."""

from __future__ import annotations

import pytest

from knowledge_ingest.crawl_result_processing import html_has_element

_PAGE = (
    "<html><body><main><h1>Help</h1>"
    '<form id="login-form" class="auth box" action="/signin">'
    '<input type="password" name="pw"><a title="Log in, please" href="/x">go</a>'
    "</form></main></body></html>"
)


@pytest.mark.parametrize(
    "selector",
    [
        "#login-form",
        "form.auth.box",
        "input[type=password]",
        "input[type='password']",
        'a[title="Log in, please"]',
        "form[action^='/sign']",
        "form[action$=signin]",
        "div.missing, #login-form",
        "[name]",
    ],
)
def test_selector_present_in_html_is_found(selector: str) -> None:
    assert html_has_element(_PAGE, selector) is True


@pytest.mark.parametrize(
    "selector",
    ["#signin", ".auth.other", "input[type=email]", "form[action^='/x']", "div, section.auth"],
)
def test_selector_absent_from_html_is_not_found(selector: str) -> None:
    assert html_has_element(_PAGE, selector) is False


@pytest.mark.parametrize("selector", ["main form", "form > input", "a:hover", "", "div,"])
def test_unsupported_selector_fails_loudly_instead_of_never_matching(selector: str) -> None:
    with pytest.raises(ValueError, match="Unsupported login indicator selector"):
        html_has_element(_PAGE, selector)
