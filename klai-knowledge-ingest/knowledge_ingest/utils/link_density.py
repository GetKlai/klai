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
    """Return the ratio of anchor-text characters to total markdown text.

    The numerator is the sum of all ``[anchor]`` text lengths. The
    denominator is the full markdown length (including link syntax) —
    using the anchor text for the numerator keeps the metric in [0, 1]
    while still rewarding "this page is mostly link labels".

    Args:
        markdown: ``fit_markdown`` from a crawl result.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 for empty input.
    """
    if not markdown:
        return 0.0
    link_text_chars = sum(len(m.group(1)) for m in _MD_LINK_RE.finditer(markdown))
    total_chars = len(markdown)
    if total_chars == 0:
        return 0.0
    ratio = link_text_chars / total_chars
    if ratio > 1.0:
        return 1.0
    return ratio
