"""Markdown link-density helper for the preview classifier (REQ-3).

SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 / D-6.

@MX:WARN — heuristic threshold (40%). Calibrated against Voys help
(~5-10% link density on real articles) and the Redcactus broken output
(80%+ link density on nav-only pages). Lower threshold means more false
positives on legitimate link-heavy index pages; higher threshold misses
real nav-leak. 40% sits cleanly between the two anchors. Adjust only
with new calibration data — do not "tune" arbitrarily.

@MX:REASON — This is the link-density signal that ``preview_crawl``
uses to decide ``selector_required`` vs ``success``. The auth-wall
classifier is a separate signal (see ``auth_wall_classifier.py``);
both run on every preview.
"""

from __future__ import annotations

import re

__all__ = ["LINK_DENSITY_THRESHOLD", "link_density"]


# REQ-3 D-6: 0.40 (40%). See module docstring for calibration anchors.
LINK_DENSITY_THRESHOLD: float = 0.40


# Markdown link: ``[anchor text](url)``. Captures the anchor text only —
# the url is discarded for density calculation.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def link_density(markdown: str) -> float:
    """Return the ratio of anchor-text characters to VISIBLE text characters.

    The numerator is the sum of all ``[anchor]`` text lengths.
    The denominator is the markdown after stripping the URL syntax — i.e.,
    the text the operator actually reads on the rendered page.

    Why visible-text and not total markdown? A nav menu of short anchors
    with long URLs (``[Home](/nl/45-bubble-api-van-derden) ...``) inflates
    ``len(markdown)`` so much that pure-nav pages score below the 40% gate.
    Real production case (wiki.redcactus.cloud/, 2026-05-07): 281 words of
    nav text scored ~28% under the old formula and was incorrectly accepted
    as ``success``. With visible-text denominator it scores ~95% → correctly
    flagged as ``selector_required``.

    Args:
        markdown: ``fit_markdown`` from a crawl result.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 for empty input.
    """
    if not markdown:
        return 0.0
    link_text_chars = sum(len(m.group(1)) for m in _MD_LINK_RE.finditer(markdown))
    visible = _MD_LINK_RE.sub(lambda m: m.group(1), markdown)
    visible_chars = len(visible)
    if visible_chars == 0:
        return 0.0
    ratio = link_text_chars / visible_chars
    if ratio > 1.0:
        return 1.0
    return ratio
