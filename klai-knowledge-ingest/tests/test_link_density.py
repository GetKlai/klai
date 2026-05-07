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


# ---------------------------------------------------------------------------
# Real production regression — short anchors + long URLs
# ---------------------------------------------------------------------------


def test_real_redcactus_nav_dominated_page_exceeds_threshold() -> None:
    """REGRESSION (production 2026-05-07): wiki.redcactus.cloud/ nav menu has
    short anchor text (Home, Bubble, Login) but long URLs (/nl/45-bubble-api-
    van-derden, /nl/cloudbeheerconsole). Under the OLD formula (denominator =
    full markdown length INCLUDING URL syntax), the page scored ~28% and was
    misclassified as ``success`` even though visually 80%+ of the rendered
    content is navigation links.

    Under the new formula (denominator = visible text after stripping URL
    syntax), the same page scores well above 0.40 → correctly flagged as
    ``selector_required``.
    """
    md = (
        "[Connectors](/nl/connectors) [Phone](/nl/phone) "
        "[Connectors](/nl/connectors) [Partner Portal](/nl/8-partnerportaal) "
        "Configuration [Bubble Desktop](/nl/53-webconfigurator) "
        "[Webconfigurator](/nl/53-webconfigurator) "
        "[Local Configuration](/nl/5-software) "
        "[Software Installation](/nl/5-software) "
        "[System / Network](/nl/38-systeem-netwerk) Bubble Cloud "
        "[Webconfigurator](/nl/39-webconfigurator) "
        "[Cloud Management Console](/nl/41-cloudbeheerconsole) "
        "[System / Network](/nl/42-systeem-netwerk) Applications "
        "[Bubble Desktop Pop-up](/nl/43-bubble-desktop-pop-up) "
        "[Bubble Gateway Integrations](/nl/44-bubble-gateway-integraties) "
        "[Bubble Third-Party-API](/nl/45-bubble-api-van-derden) "
        "[Bubble365 Chrome Plug-in](/nl/46-bubble365-chrome-plug-in) "
        "[Bubble365 Edge Plug-in](/nl/47-bubble365-edge-plug-in) "
        "[Bubble365 Embedded CRM Apps](/nl/49-bubble365-ingebedde-crm-apps) "
        "[Bubble365 Embedded Phone Apps](/nl/50-bubble365-ingebedde-telefoon-apps) "
        "[Bubble365 Teams App](/nl/48-bubble365-teams-app) "
        "[Bubble365 iFrame](/nl/51-bubble365-iframe) "
        "[Bubble365 Mobile App](/nl/52-bubble365-mobiele-app) "
        "[FAQ](/nl/11-faq) [Bubble web portal](https://portal.eu.redcactus.cloud/) "
        "[Status monitor](https://status.redcactus.nl/) Language: [Login](/login)"
    )
    density = link_density(md)
    # Visible text is dominated by anchor labels; small bits of plain prose
    # ("Configuration", "Bubble Cloud", "Applications", "Language:").
    assert density > LINK_DENSITY_THRESHOLD, (
        f"Real Redcactus nav-only fixture must score above {LINK_DENSITY_THRESHOLD} "
        f"to surface as selector_required; got {density:.3f}"
    )


def test_link_density_long_urls_do_not_dilute_density() -> None:
    """A page with VERY long URLs but short anchor text must still register
    as high-density. Tests the exact dilution bug from the redcactus case
    (short anchors + long URLs).

    Under the OLD formula this scored ~5% (anchor 1 char per link, total
    chars ~70 per link including URL syntax). Under the new visible-text
    formula it scores well above the 40% gate.
    """
    long_url = "/nl/very-long-slug-with-many-segments-that-could-dilute-the-formula"
    md = " ".join(f"[a]({long_url})" for _ in range(20))
    density = link_density(md)
    # Visible after sub: "a a a ... a" — link chars 20, visible chars 39
    # → density ~51% (was ~3% under old formula).
    assert density > LINK_DENSITY_THRESHOLD, (
        f"Short-anchor + long-URL nav must score above {LINK_DENSITY_THRESHOLD}; "
        f"got {density:.3f}"
    )
