from retrieval_api.api.retrieve import _apply_page_context_boost


def test_page_context_boost_promotes_exact_source_url_match():
    chunks = [
        {
            "chunk_id": "other",
            "source_url": "https://example.com/docs/other",
            "score": 0.9,
            "reranker_score": 0.9,
        },
        {
            "chunk_id": "current",
            "source_url": "https://example.com/docs/widget#section",
            "score": 0.85,
            "reranker_score": 0.85,
        },
    ]

    boosted, boosted_count = _apply_page_context_boost(
        chunks,
        {"url": "https://example.com/docs/widget"},
    )

    assert boosted_count == 1
    assert boosted[0]["chunk_id"] == "current"
    assert boosted[0]["_page_context_boosted"] is True


def test_page_context_boost_ignores_different_host():
    chunks = [
        {
            "chunk_id": "external",
            "source_url": "https://other.example.com/docs/widget",
            "score": 0.85,
            "reranker_score": 0.85,
        }
    ]

    boosted, boosted_count = _apply_page_context_boost(
        chunks,
        {"url": "https://example.com/docs/widget"},
    )

    assert boosted_count == 0
    assert boosted == chunks
    assert "_page_context_boosted" not in boosted[0]


def test_page_context_candidate_boost_can_run_before_final_marking():
    chunks = [
        {
            "chunk_id": "current",
            "source_url": "https://example.com/docs/widget?tracking=1",
            "score": 0.5,
        }
    ]

    boosted, boosted_count = _apply_page_context_boost(
        chunks,
        {"url": "https://example.com/docs/widget"},
        mark=False,
    )

    assert boosted_count == 1
    assert boosted[0]["score"] > 0.5
    assert "_page_context_boosted" not in boosted[0]
