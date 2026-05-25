from __future__ import annotations

from retrieval_api.services.evidence_pack import (
    build_evidence_pack,
    evidence_pack_items_as_chunks,
    evidence_pack_sources_payload,
)


def test_evidence_pack_selects_citable_sources_before_generation():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "invite-1",
                "title": "Invite and remove people",
                "text": "Invite a new user from Admin > People.",
                "source_url": "https://www.getklai.com/docs/admin/invite-remove-people",
                "score": 0.9,
                "reranker_score": 0.95,
            },
            {
                "chunk_id": "sources-1",
                "title": "Add sources",
                "text": "Connect Google Drive as a source.",
                "source_url": "https://getklai.com/docs/knowledge/add-sources",
                "score": 0.2,
                "reranker_score": 0.1,
            },
        ],
        max_sources=1,
    )

    assert [source.title for source in pack.sources] == ["Invite and remove people"]
    assert [item.chunk_id for item in pack.items] == ["invite-1"]
    assert evidence_pack_sources_payload(pack)[0]["url"] == (
        "https://getklai.com/docs/admin/invite-remove-people"
    )


def test_evidence_pack_dedupes_sources_by_canonical_url():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "c1",
                "title": "The five roles",
                "text": "Admins can manage roles.",
                "source_url": "https://www.getklai.com/docs/admin/roles",
                "score": 0.5,
            },
            {
                "chunk_id": "c2",
                "title": "The five roles",
                "text": "Members can use the workspace.",
                "source_url": "https://getklai.com/docs/admin/roles",
                "score": 0.8,
            },
        ]
    )

    assert len(pack.sources) == 1
    assert pack.sources[0].evidence_ids == ["E1", "E2"]
    assert pack.sources[0].relevance_score == 0.8


def test_evidence_pack_refuses_uncitable_chunks_instead_of_fallback_sources():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "c1",
                "title": "Internal note",
                "text": "Invite users from Admin > People.",
                "score": 0.95,
            }
        ]
    )

    assert pack.items == []
    assert pack.sources == []
    assert pack.no_citable_reason == "no_citable_sources"


def test_evidence_pack_refuses_low_relevance_citable_sources():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "add-sources",
                "title": "Add sources",
                "text": "Connect Google Drive as a knowledge source.",
                "source_url": "https://getklai.com/docs/knowledge/add-sources",
                "score": 0.8,
                "reranker_score": 0.12,
            }
        ],
        min_relevance_score=0.3,
    )

    assert pack.items == []
    assert pack.sources == []
    assert pack.no_citable_reason == "below_relevance_threshold"


def test_evidence_pack_filters_sources_that_do_not_match_query_evidence():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "invite-user",
                "title": "Invite and remove people",
                "text": "Ga naar Admin > Gebruikers en nodig een nieuwe gebruiker uit.",
                "source_url": "https://getklai.com/docs/admin/invite-remove-people",
                "score": 0.9,
                "reranker_score": 0.94,
            },
            {
                "chunk_id": "getting-started",
                "title": "Getting started",
                "text": "Create your first knowledge base and ask Klai questions.",
                "source_url": "https://getklai.com/docs/getting-started",
                "score": 0.91,
                "reranker_score": 0.93,
            },
            {
                "chunk_id": "roles",
                "title": "The five roles",
                "text": "Users can have one of five roles in Klai.",
                "source_url": "https://getklai.com/docs/admin/the-five-roles",
                "score": 0.9,
                "reranker_score": 0.92,
            },
            {
                "chunk_id": "ask",
                "title": "Ask a question",
                "text": "Ask a question about your knowledge base.",
                "source_url": "https://getklai.com/docs/chat/ask-a-question",
                "score": 0.88,
                "reranker_score": 0.91,
            },
        ],
        query="Hoe voeg ik een nieuwe user toe?",
    )

    assert [source.title for source in pack.sources] == ["Invite and remove people"]
    assert [item.chunk_id for item in pack.items] == ["invite-user"]


def test_evidence_pack_can_select_role_source_for_role_query():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "roles",
                "title": "The five roles",
                "text": "Users can have one of five roles in Klai.",
                "source_url": "https://getklai.com/docs/admin/the-five-roles",
                "score": 0.9,
                "reranker_score": 0.92,
            }
        ],
        query="Welke rollen zijn er?",
    )

    assert [source.title for source in pack.sources] == ["The five roles"]


def test_evidence_pack_preserves_heading_metadata_for_prompt_context():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "c1",
                "title": "Invite and remove people",
                "heading_path": "Admin > People",
                "text": "3. Enter the email address.\n4. Select a role.",
                "source_url": "https://getklai.com/docs/admin/invite-remove-people",
                "score": 0.9,
            }
        ]
    )

    chunks = evidence_pack_items_as_chunks(pack)

    assert chunks[0]["heading_path"] == "Admin > People"
    assert chunks[0]["text"].startswith("3. Enter")
