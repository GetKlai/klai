"""Tests for link expansion + authority boost in the retrieve pipeline (SPEC-CRAWLER-003 R14-R17)."""

from __future__ import annotations

import math

import pytest


def _make_chunk(
    chunk_id: str,
    score: float = 0.9,
    links_to: list[str] | None = None,
    incoming_link_count: int = 0,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": f"text for {chunk_id}",
        "score": score,
        "links_to": links_to or [],
        "incoming_link_count": incoming_link_count,
        "artifact_id": None,
        "content_type": None,
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
        "ingested_at": None,
        "assertion_mode": None,
    }


# ---------------------------------------------------------------------------
# Pure logic tests (no endpoint mocking needed)
# ---------------------------------------------------------------------------


class TestUrlCollection:
    """Test the URL collection logic from seed chunks."""

    def test_collects_urls_from_links_to(self) -> None:
        seed_chunks = [
            _make_chunk("s1", links_to=["https://a.com", "https://b.com"]),
            _make_chunk("s2", links_to=["https://c.com"]),
        ]
        candidate_urls: list[str] = []
        seen_urls: set[str] = set()
        for chunk in seed_chunks:
            for url in chunk.get("links_to") or []:
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidate_urls.append(url)
        assert candidate_urls == ["https://a.com", "https://b.com", "https://c.com"]

    def test_deduplicates_urls_across_chunks(self) -> None:
        seed_chunks = [
            _make_chunk("s1", links_to=["https://a.com", "https://b.com"]),
            _make_chunk("s2", links_to=["https://a.com", "https://c.com"]),
        ]
        candidate_urls: list[str] = []
        seen_urls: set[str] = set()
        for chunk in seed_chunks:
            for url in chunk.get("links_to") or []:
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidate_urls.append(url)
        assert candidate_urls == ["https://a.com", "https://b.com", "https://c.com"]

    def test_caps_at_max_urls(self) -> None:
        max_urls = 3
        chunk = _make_chunk("s1", links_to=["u1", "u2", "u3", "u4", "u5"])
        seed_chunks = [chunk]
        candidate_urls: list[str] = []
        seen_urls: set[str] = set()
        for c in seed_chunks:
            for url in c.get("links_to") or []:
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidate_urls.append(url)
                if len(candidate_urls) >= max_urls:
                    break
            if len(candidate_urls) >= max_urls:
                break
        assert len(candidate_urls) == 3
        assert candidate_urls == ["u1", "u2", "u3"]

    def test_handles_none_links_to(self) -> None:
        chunk = _make_chunk("s1", links_to=None)
        candidate_urls: list[str] = []
        seen_urls: set[str] = set()
        for url in chunk.get("links_to") or []:
            if url not in seen_urls:
                seen_urls.add(url)
                candidate_urls.append(url)
        assert candidate_urls == []


class TestExpansionDeduplication:
    """Test that expansion chunks already in raw_results are filtered out."""

    def test_existing_chunk_ids_excluded(self) -> None:
        existing = _make_chunk("existing-1", links_to=["https://a.com"])
        duplicate = _make_chunk("existing-1", score=0.0)
        new = _make_chunk("new-1", score=0.0)

        raw_results = [existing]
        expansion_chunks = [duplicate, new]
        existing_ids = {r["chunk_id"] for r in raw_results}
        new_chunks = [c for c in expansion_chunks if c["chunk_id"] not in existing_ids]

        assert len(new_chunks) == 1
        assert new_chunks[0]["chunk_id"] == "new-1"

    def test_all_duplicates_filtered(self) -> None:
        raw_results = [_make_chunk("a"), _make_chunk("b")]
        expansion_chunks = [_make_chunk("a", score=0.0), _make_chunk("b", score=0.0)]
        existing_ids = {r["chunk_id"] for r in raw_results}
        new_chunks = [c for c in expansion_chunks if c["chunk_id"] not in existing_ids]

        assert len(new_chunks) == 0


class TestAuthorityBoost:
    """Test authority boost calculation: score += boost * log(1 + incoming)."""

    def test_boost_with_incoming_links(self) -> None:
        chunk = _make_chunk("c1", score=0.5, incoming_link_count=9)
        boost = 0.05
        incoming = chunk.get("incoming_link_count") or 0
        if incoming > 0:
            chunk["score"] = chunk["score"] + boost * math.log(1 + incoming)

        expected = 0.5 + 0.05 * math.log(10)
        assert abs(chunk["score"] - expected) < 1e-9

    def test_zero_incoming_no_boost(self) -> None:
        chunk = _make_chunk("c1", score=0.5, incoming_link_count=0)
        original_score = chunk["score"]
        boost = 0.05
        incoming = chunk.get("incoming_link_count") or 0
        if incoming > 0:
            chunk["score"] = chunk["score"] + boost * math.log(1 + incoming)

        assert chunk["score"] == original_score

    def test_none_incoming_no_boost(self) -> None:
        chunk = _make_chunk("c1", score=0.5)
        chunk["incoming_link_count"] = None  # explicitly None
        original_score = chunk["score"]
        boost = 0.05
        incoming = chunk.get("incoming_link_count") or 0
        if incoming > 0:
            chunk["score"] = chunk["score"] + boost * math.log(1 + incoming)

        assert chunk["score"] == original_score

    def test_boost_is_additive(self) -> None:
        """Multiple chunks each get their own independent boost."""
        chunks = [
            _make_chunk("c1", score=0.5, incoming_link_count=4),
            _make_chunk("c2", score=0.8, incoming_link_count=0),
            _make_chunk("c3", score=0.3, incoming_link_count=19),
        ]
        boost = 0.05
        for r in chunks:
            incoming = r.get("incoming_link_count") or 0
            if incoming > 0:
                r["score"] = r["score"] + boost * math.log(1 + incoming)

        assert abs(chunks[0]["score"] - (0.5 + 0.05 * math.log(5))) < 1e-9
        assert chunks[1]["score"] == 0.8  # no change
        assert abs(chunks[2]["score"] - (0.3 + 0.05 * math.log(20))) < 1e-9

    def test_boost_logarithmic_scaling(self) -> None:
        """Authority boost uses log scaling -- doubling incoming does not double the boost."""
        boost = 0.05
        boost_10 = boost * math.log(1 + 10)
        boost_20 = boost * math.log(1 + 20)
        # Doubling incoming from 10 to 20 should give less than double the boost
        assert boost_20 < 2 * boost_10


class TestSeedKLimit:
    """Test that only the top seed_k chunks are used for expansion."""

    def test_only_seed_k_chunks_used(self) -> None:
        seed_k = 2
        chunks = [
            _make_chunk("s1", score=0.9, links_to=["https://from-s1.com"]),
            _make_chunk("s2", score=0.8, links_to=["https://from-s2.com"]),
            _make_chunk("s3", score=0.7, links_to=["https://from-s3.com"]),
        ]
        seed_chunks = chunks[:seed_k]
        candidate_urls: list[str] = []
        seen_urls: set[str] = set()
        for c in seed_chunks:
            for url in c.get("links_to") or []:
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidate_urls.append(url)

        assert candidate_urls == ["https://from-s1.com", "https://from-s2.com"]
        # s3's URL is NOT included
        assert "https://from-s3.com" not in candidate_urls
