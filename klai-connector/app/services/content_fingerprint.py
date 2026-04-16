"""Content fingerprinting for near-duplicate detection in webcrawler syncs.

SPEC-CRAWL-003: Layer C — Post-sync boilerplate-ratio metric.

This module provides pure functions for SimHash-based content fingerprinting.
No I/O, no logging, no DB access. Designed to be testable in isolation and
reusable by other connectors (notion, google_drive) in future SPECs.
"""

from __future__ import annotations

import re

# @MX:NOTE: trafilatura.deduplication.Simhash wraps a 64-bit SimHash over word shingles.
# @MX:SPEC: SPEC-CRAWL-003 REQ-11, REQ-12, REQ-13
# Threshold mapping: canary Layer A uses 0.80 (Hamming ≤ 12), cluster Layer C uses 0.95
# (Hamming ≤ 3, Google/Manku 2007 near-duplicate standard). See SPEC Threshold Rationale.
from trafilatura.deduplication import Simhash

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

# Maximum pages before switching to LSH (not yet implemented — see comment).
# SPEC-CRAWL-003 REQ-13: for ≤200 pages use pairwise Hamming; for >200 use LSH.
# @MX:TODO: Implement SimHash-LSH (band size 8, rows 8) for >200 pages.
# @MX:SPEC: SPEC-CRAWL-003 REQ-13
_PAIRWISE_MAX = 200


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
def compute_content_fingerprint(markdown: str) -> str:
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
        return ""

    stripped = _strip_markdown(markdown)
    words = stripped.split()

    if len(words) < _MIN_WORDS:
        return ""

    # Trafilatura's Simhash accepts a string and internally tokenises it.
    # Use f-string format to ensure zero-padded 16-char hex (to_hex() may omit
    # leading zeros when the hash integer has fewer than 64 significant bits).
    sh = Simhash(stripped)
    return f"{sh.hash:016x}"


def similarity(a_hex: str, b_hex: str) -> float:
    """Return SimHash similarity as 1.0 - (hamming_distance / 64).

    Args:
        a_hex: 16-char hex SimHash string (or empty string).
        b_hex: 16-char hex SimHash string (or empty string).

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
        For <= 200 pages: pairwise Hamming comparison (O(n²), acceptable for small N).
        For > 200 pages: LSH not yet implemented; falls back to pairwise with a
        warning comment. TODO: implement band-based LSH for large syncs.

        Cluster membership: union-find / greedy approach — each page joins the
        first cluster whose centroid is within the similarity threshold.
    """
    # Filter out empty fingerprints (too-short pages)
    valid = [(url, fp) for url, fp in fingerprints if fp]
    total = len(fingerprints)  # total includes empty (for ratio calculation)

    if not valid or total == 0:
        return []

    # @MX:NOTE: For >200 pages, LSH (band size 8, rows 8) is the correct approach.
    # Pairwise is O(n²) and acceptable for <=200. This is KISS for now.
    # @MX:TODO: Implement LSH for >200 pages per SPEC-CRAWL-003 REQ-13.
    # @MX:SPEC: SPEC-CRAWL-003 REQ-13

    # Greedy clustering: each page joins the cluster of the first seen page
    # within the similarity threshold (centroid = first page in cluster).
    # This is equivalent to single-linkage clustering with the first-seen centroid,
    # which is sufficient for detecting near-identical boilerplate templates.
    clusters: list[list[str]] = []
    centroids: list[str] = []  # fingerprint of the first page in each cluster

    for url, fp in valid:
        placed = False
        for i, centroid in enumerate(centroids):
            if similarity(fp, centroid) >= similarity_threshold:
                clusters[i].append(url)
                placed = True
                break
        if not placed:
            clusters.append([url])
            centroids.append(fp)

    # Filter clusters that exceed the ratio threshold
    min_cluster_size = ratio_threshold * total
    large_clusters = [c for c in clusters if len(c) > min_cluster_size]

    # Sort by size descending so callers can directly slice top-3 (REQ-17)
    large_clusters.sort(key=len, reverse=True)

    return large_clusters
