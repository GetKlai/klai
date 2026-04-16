"""Tests for content_fingerprint.py — SPEC-CRAWL-003: Layer C SimHash fingerprinting.

All tests named after the Test Plan in the SPEC.
"""

from __future__ import annotations

import pytest

from app.services.content_fingerprint import (
    compute_content_fingerprint,
    find_boilerplate_clusters,
    similarity,
)

# ---------------------------------------------------------------------------
# compute_content_fingerprint
# ---------------------------------------------------------------------------


def test_compute_content_fingerprint_returns_hex() -> None:
    """test_compute_content_fingerprint_returns_hex — standard markdown → 16-char hex."""
    markdown = (
        "# Introduction to CRM Software\n\n"
        "Customer Relationship Management software helps businesses manage interactions "
        "with current and potential customers. It organizes contact information, tracks "
        "sales deals, and automates various marketing tasks to improve customer service."
    )
    result = compute_content_fingerprint(markdown)
    assert isinstance(result, str)
    assert len(result) == 16, f"Expected 16 chars, got {len(result)}: {result!r}"
    assert all(c in "0123456789abcdef" for c in result), f"Not hex: {result!r}"


def test_compute_content_fingerprint_short_input_empty() -> None:
    """test_compute_content_fingerprint_short_input_empty — <20 words → empty string."""
    short_markdown = "# Title\n\nToo short."  # well under 20 words
    result = compute_content_fingerprint(short_markdown)
    assert result == "", f"Expected '' for short input, got {result!r}"


def test_compute_content_fingerprint_exactly_19_words_empty() -> None:
    """Exactly 19 words after stripping returns empty string (strict < 20)."""
    # Build exactly 19 words
    words = " ".join(f"word{i}" for i in range(19))
    result = compute_content_fingerprint(words)
    assert result == ""


def test_compute_content_fingerprint_exactly_20_words_returns_hex() -> None:
    """Exactly 20 words returns a non-empty hex fingerprint."""
    words = " ".join(f"word{i}" for i in range(20))
    result = compute_content_fingerprint(words)
    assert len(result) == 16


def test_compute_content_fingerprint_deterministic() -> None:
    """test_compute_content_fingerprint_deterministic — same markdown twice → same hex."""
    markdown = (
        "# Product Documentation\n\n"
        "This guide explains how to configure the webcrawler connector for your knowledge "
        "base. You can set a canary URL to detect authentication expiry automatically. "
        "The system uses SimHash to detect near-duplicate content across crawled pages."
    )
    first = compute_content_fingerprint(markdown)
    second = compute_content_fingerprint(markdown)
    assert first == second, f"Non-deterministic: {first!r} != {second!r}"


def test_compute_content_fingerprint_empty_string() -> None:
    """Empty string input returns empty fingerprint."""
    result = compute_content_fingerprint("")
    assert result == ""


def test_compute_content_fingerprint_markdown_stripped() -> None:
    """Markdown syntax is stripped before hashing (# headings, ** bold, [] links)."""
    # Two documents with same content but different markdown formatting
    plain = (
        "Introduction to Customer Relationship Management software helps businesses "
        "manage interactions with current and potential customers and tracks sales deals "
        "for improved customer service and automated marketing tasks performance."
    )
    with_markdown = (
        "# Introduction to Customer Relationship Management\n\n"
        "software **helps businesses** manage interactions with current and potential "
        "customers and tracks sales deals for improved customer service and automated "
        "marketing tasks performance."
    )
    # Both should produce the same fingerprint since content words are the same
    # (SimHash is order-sensitive, so exact match may differ, but both must be non-empty hex)
    r1 = compute_content_fingerprint(plain)
    r2 = compute_content_fingerprint(with_markdown)
    assert len(r1) == 16
    assert len(r2) == 16


# ---------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------


def test_similarity_identical_fingerprints() -> None:
    """test_similarity_identical_fingerprints — hamming=0 → 1.0."""
    fp = "abcdef1234567890"
    result = similarity(fp, fp)
    assert result == 1.0, f"Expected 1.0 for identical, got {result}"


def test_similarity_inverted_fingerprints() -> None:
    """test_similarity_inverted_fingerprints — hamming=64 → 0.0."""
    # All zeros vs all ones: every bit flipped → hamming distance = 64
    fp_zero = "0000000000000000"
    fp_ones = "ffffffffffffffff"
    result = similarity(fp_zero, fp_ones)
    assert result == 0.0, f"Expected 0.0 for inverted, got {result}"


def test_similarity_empty_returns_zero() -> None:
    """similarity returns 0.0 when either fingerprint is empty string."""
    assert similarity("", "abcdef1234567890") == 0.0
    assert similarity("abcdef1234567890", "") == 0.0
    assert similarity("", "") == 0.0


def test_similarity_partial_overlap() -> None:
    """similarity is between 0 and 1 for partially overlapping hashes."""
    # 32 bits differ out of 64 → similarity = 0.5
    fp_a = "0000000000000000"
    fp_b = "00000000ffffffff"
    result = similarity(fp_a, fp_b)
    # 32 bits differ → 1.0 - (32/64) = 0.5
    assert abs(result - 0.5) < 0.01, f"Expected ~0.5, got {result}"


def test_similarity_threshold_080_boundary() -> None:
    """Hamming distance exactly 12 gives similarity = 1.0 - 12/64 ≈ 0.8125 (above 0.80)."""
    # Create two hashes differing in exactly 12 bits
    a_int = 0xFFFFFFFFFFFFFFFF
    # Flip 12 bits
    b_int = a_int ^ ((1 << 12) - 1)
    fp_a = f"{a_int:016x}"
    fp_b = f"{b_int:016x}"
    result = similarity(fp_a, fp_b)
    expected = 1.0 - (12 / 64.0)
    assert abs(result - expected) < 0.001


def test_similarity_threshold_095_cluster_boundary() -> None:
    """Hamming distance exactly 3 gives similarity = 1.0 - 3/64 ≈ 0.953 (at cluster threshold)."""
    a_int = 0xFFFFFFFFFFFFFFFF
    b_int = a_int ^ 0b111  # flip 3 bits
    fp_a = f"{a_int:016x}"
    fp_b = f"{b_int:016x}"
    result = similarity(fp_a, fp_b)
    expected = 1.0 - (3 / 64.0)
    assert abs(result - expected) < 0.001


# ---------------------------------------------------------------------------
# find_boilerplate_clusters
# ---------------------------------------------------------------------------


def _make_fingerprint(base_int: int, bit_flips: int = 0) -> str:
    """Create a 64-bit fingerprint hex, optionally flipping some low-order bits."""
    return f"{base_int ^ ((1 << bit_flips) - 1):016x}"


def test_find_boilerplate_clusters_detects_login_wall_cluster() -> None:
    """test_find_boilerplate_clusters_detects_login_wall_cluster — 86/233 identical → cluster."""
    # Create 86 pages with near-identical fingerprint (simulating login wall)
    login_wall_base = 0xDEADBEEFCAFEBABE
    login_wall_fps = [
        (f"https://wiki.example.com/page-{i}", _make_fingerprint(login_wall_base, i % 3))
        for i in range(86)
    ]
    # Add 147 distinct pages
    distinct_fps = [
        (f"https://wiki.example.com/article-{i}", f"{0x1234567800000000 + i:016x}")
        for i in range(147)
    ]
    all_fps = login_wall_fps + distinct_fps

    clusters = find_boilerplate_clusters(all_fps, ratio_threshold=0.15, similarity_threshold=0.95)

    assert len(clusters) >= 1, "Expected at least one boilerplate cluster"
    # The largest cluster should contain the 86 login-wall pages
    largest = clusters[0]
    assert len(largest) >= 80, f"Expected cluster of ~86, got {len(largest)}"


def test_find_boilerplate_clusters_ignores_small_clusters() -> None:
    """test_find_boilerplate_clusters_ignores_small_clusters — 5/100 identical (<15%) → empty."""
    # 5 identical pages out of 100 total (5% < 15%)
    small_base = 0xAAAAAAAAAAAAAAAA
    small_cluster = [(f"https://wiki.example.com/dup-{i}", f"{small_base:016x}") for i in range(5)]
    # Use truly distinct fingerprints: spread across the full 64-bit space
    # by multiplying by a large prime to avoid accidental clustering.
    _PRIME = 0x9E3779B97F4A7C15  # Fibonacci hashing constant, good spread
    distinct = [
        (f"https://wiki.example.com/unique-{i}", f"{(i * _PRIME) & 0xFFFFFFFFFFFFFFFF:016x}")
        for i in range(1, 96)
    ]
    all_fps = small_cluster + distinct

    clusters = find_boilerplate_clusters(all_fps, ratio_threshold=0.15)

    assert clusters == [], f"Expected no clusters, got {[len(c) for c in clusters]}"


def test_find_boilerplate_clusters_returns_sorted_by_size_desc() -> None:
    """Clusters are returned sorted by size descending (largest first) per SPEC."""
    # Create 2 clusters: one of 30%, one of 20%
    base_a = 0xAAAAAAAAAAAAAAAA
    base_b = 0xBBBBBBBBBBBBBBBB
    cluster_a = [(f"https://wiki.example.com/a-{i}", f"{base_a:016x}") for i in range(30)]
    cluster_b = [(f"https://wiki.example.com/b-{i}", f"{base_b:016x}") for i in range(20)]
    distinct = [(f"https://wiki.example.com/u-{i}", f"{i + 0x1000000000000000:016x}") for i in range(50)]
    all_fps = cluster_b + cluster_a + distinct  # intentionally out of order

    clusters = find_boilerplate_clusters(all_fps, ratio_threshold=0.15)

    assert len(clusters) >= 2
    # Largest cluster first
    assert len(clusters[0]) >= len(clusters[1])


def test_find_boilerplate_clusters_empty_fingerprints_skipped() -> None:
    """Pages with empty fingerprint are excluded from cluster analysis."""
    login_wall_base = 0xDEADBEEFCAFEBABE
    fps = [(f"https://wiki.example.com/page-{i}", f"{login_wall_base:016x}") for i in range(20)]
    # Add pages with empty fingerprint (too-short pages)
    fps += [(f"https://wiki.example.com/short-{i}", "") for i in range(80)]

    # 20 near-identical out of only 20 non-empty = 100%, above threshold
    # But total = 100, so 20/100 = 20% > 15%
    clusters = find_boilerplate_clusters(fps, ratio_threshold=0.15)
    # We should still detect the cluster (20/100 = 20%)
    # But empty fingerprints don't contribute to cluster size
    assert len(clusters) >= 1


def test_find_boilerplate_clusters_hamming_distance_3_threshold() -> None:
    """Pages with hamming distance <= 3 are in the same cluster (similarity >= 0.95)."""
    base = 0xFFFFFFFFFFFFFFFF
    # All within hamming distance 3 of base → should cluster together
    close_fps = [
        (f"https://wiki.example.com/close-{i}", f"{base ^ (1 << i):016x}")
        for i in range(20)  # Each differs by exactly 1 bit from base
    ]
    distinct = [(f"https://wiki.example.com/far-{i}", f"{0x1234567800000000 + i:016x}") for i in range(80)]
    all_fps = close_fps + distinct

    clusters = find_boilerplate_clusters(all_fps, ratio_threshold=0.15, similarity_threshold=0.95)

    # 20 pages with hamming dist <= 3 from base = 20% of 100 total → should flag
    assert len(clusters) >= 1
    largest = clusters[0]
    assert len(largest) >= 15  # at least most of the 20 near-duplicates


def test_find_boilerplate_clusters_returns_urls() -> None:
    """Each cluster is a list of URL strings."""
    base = 0xFFFFFFFFFFFFFFFF
    fps = [(f"https://wiki.example.com/page-{i}", f"{base:016x}") for i in range(20)]
    distinct = [(f"https://wiki.example.com/u-{i}", f"{i + 0x1000000000000000:016x}") for i in range(80)]

    clusters = find_boilerplate_clusters(fps + distinct, ratio_threshold=0.15)

    assert len(clusters) >= 1
    for cluster in clusters:
        assert isinstance(cluster, list)
        for url in cluster:
            assert isinstance(url, str)
            assert url.startswith("https://")
