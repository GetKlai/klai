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


def test_evidence_pack_keeps_low_score_sources_when_threshold_not_requested():
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "invite-user",
                "title": "Adding A New User",
                "text": "Open Admin > Users and invite the new user by email.",
                "source_url": "https://getklai.com/docs/admin/add-user",
                "score": 0.2,
                "reranker_score": 0.12,
            }
        ],
        query="Heej hoe voeg ik een nieuwe gebruiker toe?",
    )

    assert [source.title for source in pack.sources] == ["Adding A New User"]
    assert [item.chunk_id for item in pack.items] == ["invite-user"]


def test_evidence_pack_projects_citable_retrieval_sources_without_reranking():
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
        query="Heej hoe voeg ik een nieuwe gebruiker toe?",
    )

    assert [source.title for source in pack.sources] == [
        "Invite and remove people",
        "Getting started",
        "The five roles",
    ]
    assert [item.chunk_id for item in pack.items] == [
        "invite-user",
        "getting-started",
        "roles",
    ]


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


# ─── 2026-05-27: uploaded-document chunks must reach the citations panel ───
#
# Live bug: Jantine (GetKlai org, Persoonlijk KB) uploaded
# ``CV_Jantine_Doornbos.pdf``. Knowledge-ingest stored 9 chunks in
# Qdrant via the docling pipeline. Every chunk had:
#   - artifact_id      = "853797a1-3a22-4d90-872e-6a917d996c9a"
#   - source_url       = None      (uploads have no public URL)
#   - source_label     = "personal-364818484816773122"  (kb_slug fallback)
#   - text             = "WERKERVARING: Mede-oprichter bij Klai...", etc.
#
# When she asked "Wat staat er in het CV van Jantine?", retrieval-api
# returned 11 candidates (2 web_crawler + 9 CV) and reranker scored the
# CV chunks 0.04 / 0.0002 / 0.0001 etc. — but those numbers don't
# matter: ``build_evidence_pack`` filtered EVERY chunk without a
# ``source_url`` BEFORE looking at the score (old code at line 147:
# ``if not source_url or not source_key: continue``). The CV
# disappeared from the citations panel entirely, and the model
# answered "Dat staat niet in de kennisbank" from the homepage chunks
# alone — even though the CV chunks containing the actual answer were
# in the retrieval output.
#
# Fix: accept chunks with ``artifact_id`` as fallback ownership signal
# when ``source_url`` is absent. All chunks from the same upload
# collapse into one synthetic source ``artifact:<uuid>``.

def test_evidence_pack_includes_uploaded_documents_without_source_url():
    """Uploads (PDFs, pasted text) have no public URL — their chunks
    must still reach the evidence pack via the ``artifact_id``
    fallback key, otherwise the user cannot cite their own documents.
    """
    pack = build_evidence_pack(
        [
            # The CV upload — 2 chunks, same artifact, no source_url.
            {
                "chunk_id": "cv-chunk-1",
                "artifact_id": "853797a1-3a22-4d90-872e-6a917d996c9a",
                "title": "CV_Jantine_Doornbos.pdf",
                "text": "WERKERVARING\nMede-oprichter bij Klai jan 2026 - heden.",
                "source_url": None,
                "source_label": "personal-364818484816773122",
                "score": 0.4,
                "reranker_score": 0.04,
            },
            {
                "chunk_id": "cv-chunk-2",
                "artifact_id": "853797a1-3a22-4d90-872e-6a917d996c9a",
                "title": "CV_Jantine_Doornbos.pdf",
                "text": "VAARDIGHEDEN\nAI-ontwikkeling, consultancy, Azure.",
                "source_url": None,
                "source_label": "personal-364818484816773122",
                "score": 0.3,
                "reranker_score": 0.03,
            },
            # A normal web-crawled chunk that DOES have source_url —
            # both sources must coexist in the same pack.
            {
                "chunk_id": "homepage-1",
                "artifact_id": "d9382163-ef99-4ad8-b297-c300aa08cfe8",
                "title": "Hi I'm Jantine",
                "text": "Freelance digital product designer.",
                "source_url": "https://jantinedoornbos.nl/",
                "source_label": "web_crawler",
                "score": 0.9,
                "reranker_score": 0.89,
            },
        ],
    )

    # Both sources land in the pack — neither the upload nor the URL
    # source gets dropped.
    assert len(pack.sources) == 2, (
        "Both the URL source and the artifact-only upload source must "
        "appear in the evidence pack. Pre-fix bug dropped uploads."
    )
    titles = {source.title for source in pack.sources}
    assert "CV_Jantine_Doornbos.pdf" in titles
    assert "Hi I'm Jantine" in titles

    # Three items total — all 3 chunks contributed.
    assert len(pack.items) == 3
    chunk_ids = {item.chunk_id for item in pack.items}
    assert chunk_ids == {"cv-chunk-1", "cv-chunk-2", "homepage-1"}

    # The CV source (no URL) still surfaces via evidence_pack_sources_payload:
    payload = evidence_pack_sources_payload(pack)
    cv_entry = next(p for p in payload if p["title"] == "CV_Jantine_Doornbos.pdf")
    assert cv_entry["url"] is None
    assert cv_entry["artifact_id"] == "853797a1-3a22-4d90-872e-6a917d996c9a"
    # Both source rows are surfaced — the URL-less one is no longer
    # dropped by the payload helper.
    assert len(payload) == 2


def test_evidence_pack_groups_upload_chunks_by_artifact_id():
    """All chunks from one upload share an ``artifact_id`` and must
    collapse into a single source — not two parallel sources confusing
    the citations panel.
    """
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "a",
                "artifact_id": "doc-123",
                "title": "handbook.pdf",
                "text": "First section.",
                "source_url": None,
                "score": 0.5,
            },
            {
                "chunk_id": "b",
                "artifact_id": "doc-123",
                "title": "handbook.pdf",
                "text": "Second section.",
                "source_url": None,
                "score": 0.4,
            },
            {
                "chunk_id": "c",
                "artifact_id": "doc-123",
                "title": "handbook.pdf",
                "text": "Third section.",
                "source_url": None,
                "score": 0.3,
            },
        ],
    )

    assert len(pack.sources) == 1, (
        "Three chunks from the same artifact_id must collapse into "
        "one source row, not three parallel rows."
    )
    assert pack.sources[0].title == "handbook.pdf"
    assert len(pack.sources[0].evidence_ids) == 3
    assert pack.no_citable_reason is None


def test_evidence_pack_still_drops_chunks_with_no_url_and_no_artifact():
    """The fallback is artifact-id-only: a chunk with neither
    source_url nor artifact_id has nothing addressable to cite and is
    still dropped (we'd otherwise emit a phantom source).
    """
    pack = build_evidence_pack(
        [
            {
                "chunk_id": "orphan",
                "title": "Orphan",
                "text": "Some text without provenance.",
                "source_url": None,
                "artifact_id": None,
                "score": 0.5,
            },
        ],
    )
    assert pack.sources == []
    assert pack.no_citable_reason == "no_citable_sources"
