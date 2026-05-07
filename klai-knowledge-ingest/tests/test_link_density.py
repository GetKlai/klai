"""Tests for the markdown link-density helper (REQ-3).

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-3.

Pure-function regex helper used by the preview classifier to detect
nav-dominated output ("80% of the text is links" → ``selector_required``).
"""

from __future__ import annotations

import pytest

from knowledge_ingest.utils.link_density import (
    LINK_DENSITY_THRESHOLD,
    link_density,
)


def test_no_links_returns_zero() -> None:
    md = "This is plain prose with no links at all. " * 5
    assert link_density(md) == 0.0


def test_empty_string_returns_zero() -> None:
    assert link_density("") == 0.0


def test_pure_link_navigation_returns_high_density() -> None:
    """Redcactus-style nav-only output: link density should exceed threshold.

    The fixture mimics a real failing nav-dominated page: many links with
    longer anchor texts and short URLs. Real Redcactus broken output had
    80%+ link density.
    """
    md = (
        "[Home Page](/h) [About Us](/a) [Contact Information](/c) "
        "[Our Products](/p) [Login Now](/l) [Signup Free](/s) "
        "[Documentation](/d) [Help Center](/help) [Pricing Plans](/pricing) "
        "[Customer Support](/support) [Knowledge Base](/kb) [Resources](/r) "
        "[Blog Articles](/blog) [Latest News](/news) [Career Opportunities](/jobs)"
    )
    density = link_density(md)
    assert density > LINK_DENSITY_THRESHOLD


def test_real_article_with_few_links_returns_low_density() -> None:
    """Voys-help-style article: long prose with one or two inline links.

    Calibration anchor: real articles cluster around 5-10% link density.
    """
    md = (
        "This is a long article body with many sentences explaining how to "
        "configure the system. There are detailed instructions and tips "
        "throughout. Once in a while we [link to something](/something) "
        "for context. The article continues with more prose for several "
        "paragraphs, sharing knowledge about the topic in a way that does "
        "not depend on heavy linking. " * 3
    )
    density = link_density(md)
    assert 0 < density <= 0.10


def test_threshold_is_zero_point_four() -> None:
    """REQ-3 D-6: threshold is 0.40 (40% link density), calibrated against
    Voys help (~5-10%) and Redcactus broken nav (80%+)."""
    assert LINK_DENSITY_THRESHOLD == 0.40


def test_link_density_handles_empty_anchor_text() -> None:
    """Edge: ``[](http://x)`` has zero anchor text. Must not crash; counts as
    zero contribution to link density."""
    md = "Some real article body. [](http://hidden) more body content."
    density = link_density(md)
    assert 0.0 <= density < 0.5


@pytest.mark.parametrize(
    "md",
    [
        "[a](b)",
        "Text and [link](url) and more text.",
        "[Outer](/o) wrapping [Inner](/i) bracket.",
    ],
)
def test_link_density_returns_float_in_zero_one_range(md: str) -> None:
    density = link_density(md)
    assert 0.0 <= density <= 1.0
