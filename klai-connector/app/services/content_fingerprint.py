"""Content fingerprinting for near-duplicate detection in webcrawler syncs.

SPEC-CRAWL-003: Layer C — Post-sync boilerplate-ratio metric.

This module provides pure functions for SimHash-based content fingerprinting.
No I/O, no logging, no DB access. Designed to be testable in isolation and
reusable by other connectors (notion, google_drive) in future SPECs.
"""

from __future__ import annotations

import re
from typing import NewType

# @MX:NOTE: trafilatura.deduplication.Simhash wraps a 64-bit SimHash over word shingles.
# @MX:SPEC: SPEC-CRAWL-003 REQ-11, REQ-12, REQ-13
# Threshold mapping: canary Layer A uses 0.80 (Hamming ≤ 12), cluster Layer C uses 0.95
# (Hamming ≤ 3, Google/Manku 2007 near-duplicate standard). See SPEC Threshold Rationale.
from trafilatura.deduplication import Simhash

# ---------------------------------------------------------------------------
# Public type alias — distinguishes fingerprint hex strings from plain strings
# in function signatures across the adapter and sync engine.
# ---------------------------------------------------------------------------
ContentFingerprint = NewType("ContentFingerprint", str)
"""16-char hex string (64-bit SimHash) or empty string for too-short pages."""

# ---------------------------------------------------------------------------
# Module-level constants — all SPEC-CRAWL-003 thresholds in one place.
# See §Threshold Rationale in the SPEC for the research backing each value.
# ---------------------------------------------------------------------------

# Minimum word count after stripping to produce a fingerprint.
# Pages with fewer words are too short for meaningful SimHash clustering.
# SPEC-CRAWL-003 REQ-11: <20 words → return "".
_MIN_WORDS = 20

# Cluster similarity threshold for Layer C boilerplate detection.
# 0.95 ≈ Hamming distance ≤ 3 on 64-bit SimHash (Google/Manku 2007 standard).
# SPEC-CRAWL-003 REQ-13, Threshold Rationale §Cluster similarity 0.95.
_DEFAULT_SIMILARITY_THRESHOLD = 0.95

# Ratio of pages a cluster must exceed to be flagged as boilerplate.
# 15% is the midpoint between legitimate layout overlap (<10%) and the
# motivating incident (37% contamination). SPEC-CRAWL-003 REQ-13.
_DEFAULT_RATIO_THRESHOLD = 0.15

# Minimum pages before Layer C runs. Below this sample size, a 15% cluster
# is too few pages to be statistically meaningful (e.g. 2 pages out of 12).
# SPEC-CRAWL-003 REQ-14, Threshold Rationale §Minimum sample size 30 pages.
LAYER_C_MIN_PAGES = 30

# Threshold above which LSH replaces pairwise comparison.
# SPEC-CRAWL-003 REQ-13: for ≤200 pages use pairwise Hamming; for >200 use LSH.
_PAIRWISE_MAX = 200

# LSH parameters: 8 bands × 8 bits per band = 64 bits total.
# With these parameters, two 64-bit SimHashes differing in ≤3 bits (similarity ≥0.95)
# share at least one identical band with probability >99.9%. This matches the
# Google/Manku 2007 standard for near-duplicate detection at scale.
_LSH_BANDS = 8
_LSH_ROWS = 8  # bits per band


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax and return plain text for word counting.

    Strips headings (#), bold/italic (*_), links [text](url), inline code (`),
    and URLs so the word count reflects content words only.
    """
    # Remove URLs
    text = re.sub(r"https?://\S+", " ", text)
    # Remove markdown syntax characters
    text = re.sub(r"[#*_`\[\]()!]", " ", text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


# @MX:ANCHOR: compute_content_fingerprint is called from webcrawler._process_results
# (per-page) and from find_boilerplate_clusters tests. Fan-in >= 3 after Layer C.
# @MX:REASON: Public API boundary — changing signature or return type breaks adapter
#             and sync engine integration points.
# @MX:SPEC: SPEC-CRAWL-003 REQ-11
def compute_content_fingerprint(markdown: str) -> ContentFingerprint:
    """Return 16-char hex SimHash of markdown content, or '' if too short.

    Args:
        markdown: Raw markdown string from the web crawler.

    Returns:
        Zero-padded 16-character hex string (64-bit SimHash), or empty string
        if the stripped text contains fewer than 20 words.

    Notes:
        - 16 hex chars = 64 bits. SPEC-CRAWL-003 REQ-11.
        - Minimum 20 words threshold prevents noise from near-empty pages.
        - Trafilatura's Simhash uses 4-word shingles over the input tokens.
    """
    if not markdown:
        return ContentFingerprint("")

    stripped = _strip_markdown(markdown)
    words = stripped.split()

    if len(words) < _MIN_WORDS:
        return ContentFingerprint("")

    # Trafilatura's Simhash accepts a string and internally tokenises it.
    # Use f-string format to ensure zero-padded 16-char hex (to_hex() may omit
    # leading zeros when the hash integer has fewer than 64 significant bits).
    sh = Simhash(stripped)
    return ContentFingerprint(f"{sh.hash:016x}")


def similarity(a_hex: ContentFingerprint | str, b_hex: ContentFingerprint | str) -> float:
    """Return SimHash similarity as 1.0 - (hamming_distance / 64).

    Args:
        a_hex: ContentFingerprint (16-char hex) or empty string.
        b_hex: ContentFingerprint (16-char hex) or empty string.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 if either argument is empty.

    Notes:
        Threshold mapping on 64-bit SimHash (SPEC-CRAWL-003 Threshold Rationale):
        - Canary (Layer A): >= 0.80 = pass (Hamming <= 12)
        - Cluster (Layer C): >= 0.95 = same cluster (Hamming <= 3)
    """
    if not a_hex or not b_hex:
        return 0.0

    a = int(a_hex, 16)
    b = int(b_hex, 16)
    hamming = bin(a ^ b).count("1")
    return 1.0 - (hamming / 64.0)


def find_boilerplate_clusters(
    fingerprints: list[tuple[str, str]],
    *,
    ratio_threshold: float = _DEFAULT_RATIO_THRESHOLD,
    similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> list[list[str]]:
    """Return URL clusters whose size exceeds ratio_threshold of total pages.

    Args:
        fingerprints: List of (url, fingerprint_hex) tuples. Empty fingerprints
            are silently skipped (too-short pages per REQ-12).
        ratio_threshold: Fraction of total pages a cluster must exceed to be
            flagged. Default 0.15 (15%). SPEC-CRAWL-003 REQ-13.
        similarity_threshold: SimHash similarity >= this value means same cluster.
            Default 0.95 (Hamming <= 3, Google/Manku 2007). SPEC-CRAWL-003 REQ-13.

    Returns:
        List of clusters (each cluster = list of URLs), sorted by size descending
        so callers can slice the top-3 for detail logging per REQ-17.

    Notes:
        For <= 200 pages: pairwise centroid comparison (O(n*k), fast for small N).
        For > 200 pages: SimHash-LSH with 8 bands × 8 bits per band narrows
        candidates before verification, reducing inner loop to O(1) amortized.

        Cluster membership: greedy centroid approach — each page joins the first
        cluster whose centroid is within the similarity threshold.
    """
    # Filter out empty fingerprints (too-short pages)
    valid = [(url, fp) for url, fp in fingerprints if fp]
    total = len(fingerprints)  # total includes empty (for ratio calculation)

    if not valid or total == 0:
        return []

    # Greedy centroid clustering: each page joins the first cluster whose
    # centroid is within the similarity threshold. For ≤200 pages, centroids
    # are checked exhaustively (O(n*k)). For >200, LSH band indexing reduces
    # candidate centroids to those sharing at least one 8-bit band — cutting
    # the inner loop from O(k) to O(candidates), typically O(1) amortized.
    # SPEC-CRAWL-003 REQ-13.
    use_lsh = len(valid) > _PAIRWISE_MAX

    clusters: list[list[str]] = []
    centroids: list[str] = []  # fingerprint hex of first page per cluster

    # LSH index: per-band dict mapping band_value → list of centroid indices.
    # Only populated when use_lsh is True.
    band_index: list[dict[int, list[int]]] = [{} for _ in range(_LSH_BANDS)] if use_lsh else []

    for url, fp in valid:
        matched_cluster = _find_matching_cluster(
            fp,
            centroids,
            similarity_threshold,
            use_lsh,
            band_index,
        )
        if matched_cluster >= 0:
            clusters[matched_cluster].append(url)
        else:
            # New cluster — register centroid and (if LSH) index its bands.
            cluster_idx = len(clusters)
            clusters.append([url])
            centroids.append(fp)
            if use_lsh:
                _index_centroid(fp, cluster_idx, band_index)

    # Filter clusters that exceed the ratio threshold
    min_cluster_size = ratio_threshold * total
    large_clusters = [c for c in clusters if len(c) > min_cluster_size]

    # Sort by size descending so callers can directly slice top-3 (REQ-17)
    large_clusters.sort(key=len, reverse=True)

    return large_clusters


def _find_matching_cluster(
    fp_hex: str,
    centroids: list[str],
    threshold: float,
    use_lsh: bool,
    band_index: list[dict[int, list[int]]],
) -> int:
    """Return the index of the first matching cluster centroid, or -1.

    When ``use_lsh`` is False, checks all centroids (pairwise).
    When True, uses band_index to narrow candidates before verifying.
    """
    if not use_lsh:
        # Pairwise: check every centroid (O(k), acceptable for ≤200 pages)
        for i, centroid in enumerate(centroids):
            if similarity(fp_hex, centroid) >= threshold:
                return i
        return -1

    # LSH path: only check centroids that share at least one 8-bit band.
    fp_int = int(fp_hex, 16)
    seen: set[int] = set()
    for b in range(_LSH_BANDS):
        band_value = (fp_int >> (b * _LSH_ROWS)) & ((1 << _LSH_ROWS) - 1)
        for ci in band_index[b].get(band_value, []):
            if ci not in seen:
                seen.add(ci)
                if similarity(fp_hex, centroids[ci]) >= threshold:
                    return ci
    return -1


def _index_centroid(
    fp_hex: str,
    cluster_idx: int,
    band_index: list[dict[int, list[int]]],
) -> None:
    """Add a centroid's band values to the LSH index."""
    fp_int = int(fp_hex, 16)
    for b in range(_LSH_BANDS):
        band_value = (fp_int >> (b * _LSH_ROWS)) & ((1 << _LSH_ROWS) - 1)
        band_index[b].setdefault(band_value, []).append(cluster_idx)
