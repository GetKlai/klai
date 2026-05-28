from klai_citations import (
    build_citation_registry,
    compose_answer_with_trusted_sources,
    compose_citations,
    evidence_chunks_from_chunks,
    evidence_pack_items_as_chunks,
    format_sources_markdown,
    render_evidence_context,
    render_markdown_answer,
    render_markdown_answer_with_sources,
    render_markdown_sources,
    render_structured_answer,
    render_structured_sources,
    trusted_sources_from_evidence_pack,
)


def test_render_markdown_answer_uses_retrieved_source_urls_not_model_links() -> None:
    rendered = render_markdown_answer_with_sources(
        "Klai is steward-owned [fake](https://getklai.com/made-up).\n\nSources:\n1. Bad https://bad.example",
        [
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned.",
            }
        ],
    )

    assert "https://getklai.com/made-up" not in rendered.content
    assert "https://bad.example" not in rendered.content
    assert "Klai is steward-owned fake" in rendered.content
    assert "(1)" not in rendered.content
    assert "- [Steward ownership](https://getklai.com/docs/company/steward-ownership)" in rendered.content


def test_compose_citations_preserves_allowed_image_markdown() -> None:
    composed = compose_citations(
        "Zie ![diagram](https://getklai.getklai.com/kb-images/org/diagram.png).",
        [
            {
                "title": "Diagram",
                "source_url": "https://docs.getklai.com/diagram",
                "text": "Deze handleiding heeft een diagram.",
            }
        ],
        allowed_image_urls={"https://getklai.getklai.com/kb-images/org/diagram.png"},
    )

    assert "![diagram](https://getklai.getklai.com/kb-images/org/diagram.png)" in composed.content


def test_citation_registry_dedupes_and_excludes_invalid_urls() -> None:
    registry = build_citation_registry(
        [
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned.",
            },
            {
                "title": "Duplicate title ignored",
                "source_url": "https://getklai.com/docs/company/steward-ownership",
                "text": "Steward ownership protects the mission.",
            },
            {
                "title": "Bad",
                "source_url": "not-a-url",
                "text": "This should not become a source.",
            },
        ]
    )

    assert registry.has_sources
    assert len(registry.sources) == 1
    assert registry.sources[0].title == "Steward ownership"
    assert registry.sources[0].url == "https://getklai.com/docs/company/steward-ownership"
    assert registry.sources[0].chunk_texts == [
        "Klai is steward-owned.",
        "Steward ownership protects the mission.",
    ]


def test_registry_renderers_output_markdown_and_structured_sources() -> None:
    registry = build_citation_registry(
        [
            {
                "title": "Privacy policy",
                "source_url": "https://getklai.com/docs/legal/privacy",
                "text": "The privacy policy explains data handling.",
            }
        ]
    )

    assert render_structured_sources(registry) == [
        {
            "label": "1",
            "title": "Privacy policy",
            "url": "https://getklai.com/docs/legal/privacy",
        }
    ]
    assert render_markdown_sources(registry) == "- [Privacy policy](https://getklai.com/docs/legal/privacy)"

    structured = render_structured_answer("The privacy policy explains data handling.", registry)
    assert structured.content == "The privacy policy explains data handling."

    rendered = render_markdown_answer("The privacy policy explains data handling.", registry)

    assert "The privacy policy explains data handling." in rendered.content
    assert "(1)" not in rendered.content
    assert "- [Privacy policy](https://getklai.com/docs/legal/privacy)" in rendered.content


def test_registry_sources_are_limited_for_compact_document_level_sources() -> None:
    """Default source payload/list is compact and relevant to the final answer."""
    registry = build_citation_registry(
        [
            {
                "title": "Alpha policy",
                "source_url": "https://docs.getklai.com/alpha",
                "text": "Alpha policy explains account access.",
            },
            {
                "title": "Beta policy",
                "source_url": "https://docs.getklai.com/beta",
                "text": "Beta policy explains billing ownership.",
            },
            {
                "title": "Gamma policy",
                "source_url": "https://docs.getklai.com/gamma",
                "text": "Gamma policy explains support ownership.",
            },
            {
                "title": "Delta policy",
                "source_url": "https://docs.getklai.com/delta",
                "text": "Delta policy explains workspace ownership.",
            },
        ]
    )

    rendered = render_markdown_answer("The answer discusses account access.", registry)

    assert "account access." in rendered.content.lower()
    assert "(1)" not in rendered.content
    assert rendered.sources == [
        {"label": "1", "title": "Alpha policy", "url": "https://docs.getklai.com/alpha"},
    ]
    assert "- [Alpha policy](https://docs.getklai.com/alpha)" in rendered.content
    assert "- [Beta policy](https://docs.getklai.com/beta)" not in rendered.content
    assert "- [Delta policy](https://docs.getklai.com/delta)" not in rendered.content


def test_document_sources_filter_irrelevant_retrieved_pages() -> None:
    rendered = render_markdown_answer_with_sources(
        "Ga naar Admin en nodig een nieuwe gebruiker uit via Mensen.",
        [
            {
                "title": "Add sources",
                "source_url": "https://docs.getklai.com/add-sources",
                "text": "Connect Notion, Google Drive, websites, and other knowledge sources.",
            },
            {
                "title": "Invite and remove people",
                "source_url": "https://docs.getklai.com/invite-and-remove-people",
                "text": "Admins can invite a new user from Admin > Mensen by entering the work email address.",
            },
        ],
    )

    assert "- [Invite and remove people](https://docs.getklai.com/invite-and-remove-people)" in rendered.content
    assert "- [Add sources](https://docs.getklai.com/add-sources)" not in rendered.content


def test_document_sources_do_not_fallback_to_first_retrieved_sources() -> None:
    rendered = render_markdown_answer_with_sources(
        (
            "Je voegt een extra gebruiker toe via de admin-functie. "
            "Een admin kan nieuwe gebruikers uitnodigen of hun rol aanpassen."
        ),
        [
            {
                "title": "Add sources",
                "source_url": "https://docs.getklai.com/add-sources",
                "text": "Connect Notion, Google Drive, websites, and other knowledge sources.",
            },
            {
                "title": "Getting started",
                "source_url": "https://docs.getklai.com/getting-started",
                "text": "Create your first knowledge base and ask Klai questions.",
            },
            {
                "title": "Build a knowledge base",
                "source_url": "https://docs.getklai.com/build-a-knowledge-base",
                "text": "Add documents, websites, and integrations to improve knowledge retrieval.",
            },
        ],
    )

    assert rendered.sources == []
    assert "https://docs.getklai.com/add-sources" not in rendered.content
    assert "https://docs.getklai.com/getting-started" not in rendered.content
    assert "https://docs.getklai.com/build-a-knowledge-base" not in rendered.content


def test_rendered_answer_renumbers_copied_mid_document_steps() -> None:
    rendered = render_markdown_answer_with_sources(
        (
            "TL;DR\n"
            "Open Admin > Gebruikers en klik op Nodigen.\n"
            "Voeg stap voor stap toe:\n"
            "3. Typ het werk-emailadres van de nieuwe gebruiker.\n"
            "4. Selecteer de startrol via het rol-dropdownmenu.\n"
            "6. De gebruiker klikt op de link en is klaar."
        ),
        [
            {
                "title": "For admins",
                "source_url": "https://docs.getklai.com/for-admins",
                "text": "Admins invite users from Admin > Gebruikers with a start role.",
            }
        ],
    )

    assert "3. Typ het werk-emailadres" not in rendered.content
    assert "4. Selecteer de startrol" not in rendered.content
    assert "6. De gebruiker klikt" not in rendered.content
    assert "1. Typ het werk-emailadres" in rendered.content
    assert "2. Selecteer de startrol" in rendered.content
    assert "3. De gebruiker klikt" in rendered.content


def test_rendered_answer_renumbers_single_copied_mid_document_step() -> None:
    rendered = render_markdown_answer_with_sources(
        (
            "TL;DR: Voeg de gebruiker toe via Admin > Gebruikers.\n"
            "3. Voer het werk-emailadres van de nieuwe gebruiker in."
        ),
        [
            {
                "title": "Invite and remove people",
                "source_url": "https://docs.getklai.com/invite-remove-people",
                "text": "Admins invite users from Admin > Gebruikers.",
            }
        ],
    )

    assert "Voer het werk-emailadres" in rendered.content
    assert "1. Voer het werk-emailadres" not in rendered.content
    assert "3. Voer het werk-emailadres" not in rendered.content


def test_evidence_context_preserves_heading_as_section_metadata() -> None:
    context = render_evidence_context(
        [
            {
                "title": "Invite and remove people",
                "source_url": "https://docs.getklai.com/invite",
                "heading_path": "Admin > Mensen",
                "text": "Admin > Mensen\n\n4. Voer het werk-emailadres in.\n5. Selecteer een rol.",
                "chunk_type": "procedural",
            }
        ],
        include_source_urls=False,
    )

    assert "Evidence E1" in context
    assert "Section path: Admin > Mensen" in context
    assert "Chunk type: procedural" in context
    assert "List note: this excerpt starts mid ordered-list" in context
    assert "source_url:" not in context
    assert "Content:\n4. Voer het werk-emailadres in." in context
    assert "Admin > Mensen\n\n4." not in context


def test_evidence_chunks_infer_legacy_prepended_heading() -> None:
    evidence = evidence_chunks_from_chunks(
        [
            {
                "title": "For admins",
                "source_url": "https://docs.getklai.com/admins",
                "text": "For admins\n\nAdmins can invite new users.",
            }
        ]
    )

    assert evidence[0].section_path == ["For admins"]
    assert evidence[0].content == "Admins can invite new users."


def test_registry_sources_can_render_full_list_when_requested() -> None:
    registry = build_citation_registry(
        [
            {
                "title": "Alpha policy",
                "source_url": "https://docs.getklai.com/alpha",
                "text": "Alpha policy explains account access.",
            },
            {
                "title": "Beta policy",
                "source_url": "https://docs.getklai.com/beta",
                "text": "Beta policy explains billing ownership.",
            },
            {
                "title": "Gamma policy",
                "source_url": "https://docs.getklai.com/gamma",
                "text": "Gamma policy explains support ownership.",
            },
            {
                "title": "Delta policy",
                "source_url": "https://docs.getklai.com/delta",
                "text": "Delta policy explains workspace ownership.",
            },
        ]
    )

    assert render_structured_sources(registry, max_sources=None)[-1] == {
        "label": "4",
        "title": "Delta policy",
        "url": "https://docs.getklai.com/delta",
    }


def test_trusted_sources_are_projected_only_from_evidence_pack_sources() -> None:
    pack = {
        "items": [
            {
                "evidence_id": "E1",
                "chunk_id": "chunk-1",
                "title": "Invite people",
                "text": "Admins can invite users.",
                "source_url": "https://docs.getklai.com/invite-people",
            }
        ],
        "sources": [
            {
                "source_id": "S1",
                "title": "Invite people",
                "source_url": "https://www.docs.getklai.com/invite-people",
                "evidence_ids": ["E1"],
            }
        ],
    }

    assert trusted_sources_from_evidence_pack(pack) == [
        {
            "label": "1",
            "title": "Invite people",
            "url": "https://docs.getklai.com/invite-people",
            "source_id": "S1",
            "evidence_ids": ["E1"],
            "artifact_id": None,
            "source_label": None,
            "relevance_score": None,
        }
    ]
    assert evidence_pack_items_as_chunks(pack) == [
        {
            "chunk_id": "chunk-1",
            "evidence_id": "E1",
            "artifact_id": None,
            "content_type": None,
            "text": "Admins can invite users.",
            "title": "Invite people",
            "heading_path": None,
            "source_url": "https://docs.getklai.com/invite-people",
            "source_label": None,
            "score": None,
            "reranker_score": None,
            "final_score": None,
            "scope": None,
            "image_urls": None,
            "is_parent_text": None,
        }
    ]


def test_trusted_sources_include_uploaded_documents_without_source_url() -> None:
    pack = {
        "sources": [
            {
                "source_id": "S1",
                "title": "CV_Jantine_Doornbos.pdf",
                "source_url": None,
                "artifact_id": "853797a1-3a22-4d90-872e-6a917d996c9a",
                "evidence_ids": ["E1"],
            }
        ],
    }

    sources = trusted_sources_from_evidence_pack(pack)

    assert sources == [
        {
            "label": "1",
            "title": "CV_Jantine_Doornbos.pdf",
            "url": "",
            "source_id": "S1",
            "evidence_ids": ["E1"],
            "artifact_id": "853797a1-3a22-4d90-872e-6a917d996c9a",
            "source_label": None,
            "relevance_score": None,
        }
    ]
    assert format_sources_markdown(sources) == "- CV_Jantine_Doornbos.pdf"


def test_trusted_source_composition_supports_uploaded_documents_without_url() -> None:
    composed = compose_answer_with_trusted_sources(
        "Frank Wolters is verantwoordelijk voor Data Readiness.",
        [
            {
                "label": "1",
                "title": "CV_Jantine_Doornbos.pdf",
                "url": "",
                "artifact_id": "853797a1-3a22-4d90-872e-6a917d996c9a",
                "evidence_ids": ["E1"],
            }
        ],
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "artifact_id": "853797a1-3a22-4d90-872e-6a917d996c9a",
                "title": "CV_Jantine_Doornbos.pdf",
                "source_url": None,
                "text": "Frank Wolters is verantwoordelijk voor Data Readiness.",
            }
        ],
    )

    assert composed.sources == [
        {"label": "1", "title": "CV_Jantine_Doornbos.pdf", "url": ""}
    ]
    assert format_sources_markdown(composed.sources) == "- CV_Jantine_Doornbos.pdf"


def test_trusted_source_composition_never_reconstructs_sources_from_text_or_chunks() -> None:
    composed = compose_answer_with_trusted_sources(
        "Zie [fake](https://docs.getklai.com/fake).\n\nSources:\n1. Bad https://bad.example",
        [],
    )

    assert composed.sources == []
    assert "https://docs.getklai.com/fake" not in composed.content
    assert "https://bad.example" not in composed.content


def test_trusted_source_composition_filters_unsupported_evidence_pack_sources() -> None:
    composed = compose_answer_with_trusted_sources(
        "Ga naar Admin > Users, klik op Invite en kies een rol.",
        [
            {
                "label": "1",
                "title": "Ask a question",
                "url": "https://docs.getklai.com/ask-a-question",
                "evidence_ids": ["E1"],
            },
            {
                "label": "2",
                "title": "Invite and remove people",
                "url": "https://docs.getklai.com/invite-and-remove-people",
                "evidence_ids": ["E2"],
            },
        ],
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "source_url": "https://docs.getklai.com/ask-a-question",
                "text": "Ask Klai a question and read the answer.",
            },
            {
                "evidence_id": "E2",
                "source_url": "https://docs.getklai.com/invite-and-remove-people",
                "text": "Open Admin > Users, click Invite, enter an email, and choose a role.",
            },
        ],
    )

    assert composed.sources == [
        {
            "label": "1",
            "title": "Invite and remove people",
            "url": "https://docs.getklai.com/invite-and-remove-people",
        }
    ]


def test_trusted_source_composition_uses_query_support_not_surface_overlap() -> None:
    composed = compose_answer_with_trusted_sources(
        (
            "Open Admin > Users, click Invite, enter the work email, "
            "and pick a starting role."
        ),
        [
            {
                "label": "1",
                "title": "The five roles",
                "url": "https://getklai.getklai.com/docs/klai-help/the-five-roles",
                "evidence_ids": ["E1"],
            },
            {
                "label": "2",
                "title": "Invite and remove people",
                "url": "https://getklai.getklai.com/docs/klai-help/invite-and-remove-people",
                "evidence_ids": ["E2"],
            },
        ],
        query_text="How do I invite a colleague?",
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "source_url": "https://getklai.getklai.com/docs/klai-help/the-five-roles",
                "text": (
                    "Promoting and demoting. Open Admin > Users. "
                    "Click a user. Pick a new role from the dropdown."
                ),
            },
            {
                "evidence_id": "E2",
                "source_url": "https://getklai.getklai.com/docs/klai-help/invite-and-remove-people",
                "text": (
                    "Invite a colleague. Click Invite. Enter their work email. "
                    "Pick a starting role."
                ),
            },
        ],
    )

    assert composed.sources == [
        {
            "label": "1",
            "title": "Invite and remove people",
            "url": "https://getklai.getklai.com/docs/klai-help/invite-and-remove-people",
        }
    ]
    assert composed.decision["selected"][0]["title"] == "Invite and remove people"
    assert composed.decision["selected"][0]["reason"] == "supported"
    assert composed.decision["selected"][0]["query_score"] >= 2
    assert composed.decision["rejected"][0]["title"] == "The five roles"
    assert composed.decision["rejected"][0]["reason"] == "query_not_supported"


def test_trusted_source_composition_derives_query_support_from_candidates() -> None:
    composed = compose_answer_with_trusted_sources(
        "De privacy policy beschrijft hoe Klai data verwerkt.",
        [
            {
                "label": "1",
                "title": "Billing policy",
                "url": "https://docs.getklai.com/billing-policy",
                "evidence_ids": ["E1"],
            },
            {
                "label": "2",
                "title": "Privacy policy",
                "url": "https://docs.getklai.com/privacy-policy",
                "evidence_ids": ["E2"],
            },
        ],
        query_text="Waar vind ik de privacy policy?",
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "source_url": "https://docs.getklai.com/billing-policy",
                "text": "The billing policy explains invoices and subscriptions.",
            },
            {
                "evidence_id": "E2",
                "source_url": "https://docs.getklai.com/privacy-policy",
                "text": "The privacy policy explains how Klai processes data.",
            },
        ],
    )

    assert composed.sources == [
        {
            "label": "1",
            "title": "Privacy policy",
            "url": "https://docs.getklai.com/privacy-policy",
        }
    ]
    assert composed.decision["query_support_tokens"] == ["policy", "privacy"]
    assert composed.decision["rejected"][0]["title"] == "Billing policy"
    assert composed.decision["rejected"][0]["reason"] == "query_not_supported"


def test_trusted_source_composition_keeps_simple_answers_to_best_source() -> None:
    composed = compose_answer_with_trusted_sources(
        (
            "Ga naar Admin > Users, klik op Invite and remove people en voeg de "
            "nieuwe gebruiker toe met de juiste rol."
        ),
        [
            {
                "label": "1",
                "title": "The five roles",
                "url": "https://getklai.getklai.com/docs/klai-help/the-five-roles",
                "evidence_ids": ["E1"],
            },
            {
                "label": "2",
                "title": "Build a knowledge base",
                "url": "https://getklai.getklai.com/docs/klai-help/build-a-knowledge-base",
                "evidence_ids": ["E2"],
            },
            {
                "label": "3",
                "title": "For admins",
                "url": "https://getklai.getklai.com/docs/klai-help/for-admins",
                "evidence_ids": ["E3"],
            },
        ],
        query_text="Hoe voeg ik een nieuwe gebruiker toe?",
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "source_url": "https://getklai.getklai.com/docs/klai-help/the-five-roles",
                "text": "Admins assign roles to users from Admin > Users.",
            },
            {
                "evidence_id": "E2",
                "source_url": "https://getklai.getklai.com/docs/klai-help/build-a-knowledge-base",
                "text": "Admins can invite users after setting up a knowledge base.",
            },
            {
                "evidence_id": "E3",
                "source_url": "https://getklai.getklai.com/docs/klai-help/for-admins",
                "text": "Admins invite users from Admin > Users and assign the right role.",
            },
        ],
    )

    assert composed.sources == [
        {
            "label": "1",
            "title": "For admins",
            "url": "https://getklai.getklai.com/docs/klai-help/for-admins",
        }
    ]
    rejected_titles = {item["title"] for item in composed.decision["rejected"]}
    assert rejected_titles >= {"The five roles", "Build a knowledge base"}


def test_trusted_source_composition_allows_more_sources_for_complex_supported_answers() -> None:
    composed = compose_answer_with_trusted_sources(
        (
            "The onboarding process covers workspace setup, role assignment, privacy controls, "
            "billing ownership, audit exports, connector configuration, retention settings, "
            "and support escalation. Admins should verify the workspace policy, review the "
            "role policy, confirm the privacy policy, check billing policy ownership, and "
            "document the support policy before rollout across departments."
        ),
        [
            {
                "title": "Workspace policy",
                "url": "https://docs.getklai.com/workspace-policy",
                "evidence_ids": ["E1"],
                "relevance_score": 0.98,
            },
            {
                "title": "Role policy",
                "url": "https://docs.getklai.com/role-policy",
                "evidence_ids": ["E2"],
                "relevance_score": 0.95,
            },
            {
                "title": "Privacy policy",
                "url": "https://docs.getklai.com/privacy-policy",
                "evidence_ids": ["E3"],
                "relevance_score": 0.91,
            },
            {
                "title": "Billing policy",
                "url": "https://docs.getklai.com/billing-policy",
                "evidence_ids": ["E4"],
                "relevance_score": 0.88,
            },
            {
                "title": "Support policy",
                "url": "https://docs.getklai.com/support-policy",
                "evidence_ids": ["E5"],
                "relevance_score": 0.50,
            },
        ],
        query_text=(
            "Summarize workspace policy, role policy, privacy policy, billing policy, "
            "and support policy for onboarding."
        ),
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "source_url": "https://docs.getklai.com/workspace-policy",
                "text": "The workspace policy covers workspace setup and connector configuration.",
                "final_score": 0.98,
            },
            {
                "evidence_id": "E2",
                "source_url": "https://docs.getklai.com/role-policy",
                "text": "The role policy covers role assignment and rollout controls.",
                "final_score": 0.95,
            },
            {
                "evidence_id": "E3",
                "source_url": "https://docs.getklai.com/privacy-policy",
                "text": "The privacy policy covers privacy controls and retention settings.",
                "final_score": 0.91,
            },
            {
                "evidence_id": "E4",
                "source_url": "https://docs.getklai.com/billing-policy",
                "text": "The billing policy covers billing ownership.",
                "final_score": 0.88,
            },
            {
                "evidence_id": "E5",
                "source_url": "https://docs.getklai.com/support-policy",
                "text": "The support policy covers support escalation.",
                "final_score": 0.50,
            },
        ],
    )

    assert [source["title"] for source in composed.sources] == [
        "Workspace policy",
        "Role policy",
        "Privacy policy",
        "Billing policy",
    ]
    rejected = {item["title"]: item["reason"] for item in composed.decision["rejected"]}
    assert rejected["Support policy"] == "max_sources_exceeded"


def test_trusted_source_composition_rejects_weak_retrieval_side_source() -> None:
    composed = compose_answer_with_trusted_sources(
        (
            "Frank Wolters is eigenaar voor Organisatorische Readiness en "
            "Data Readiness, en is betrokken bij Governance & Ethiek."
        ),
        [
            {
                "title": "Verantwoordelijkheden per bouwblok.pdf",
                "url": "",
                "artifact_id": "responsibilities",
                "evidence_ids": ["E1"],
                "relevance_score": 0.10,
            },
            {
                "title": "AI-Blueprint.pdf",
                "url": "",
                "artifact_id": "blueprint",
                "evidence_ids": ["E2"],
                "relevance_score": 0.0002,
            },
        ],
        query_text="Wie is Frank?",
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "artifact_id": "responsibilities",
                "title": "Verantwoordelijkheden per bouwblok.pdf",
                "text": (
                    "Frank Wolters is eigenaar / trekker voor Organisatorische "
                    "Readiness en Data Readiness. Bij Governance & Ethiek is "
                    "Frank betrokken als ethiek- of compliance-contact."
                ),
                "final_score": 0.10,
            },
            {
                "evidence_id": "E2",
                "artifact_id": "blueprint",
                "title": "AI-Blueprint.pdf",
                "text": (
                    "Frank gebruikt AI Blueprint voor Organisatorische Readiness, "
                    "Data Readiness en Governance & Ethiek als bouwstenen."
                ),
                "final_score": 0.0002,
            },
        ],
    )

    assert composed.sources == [
        {"label": "1", "title": "Verantwoordelijkheden per bouwblok.pdf", "url": ""}
    ]
    rejected = {item["title"]: item["reason"] for item in composed.decision["rejected"]}
    assert rejected["AI-Blueprint.pdf"] == "max_sources_exceeded"


def test_trusted_source_composition_uses_evidence_items_and_strips_model_source_bullets() -> None:
    composed = compose_answer_with_trusted_sources(
        (
            "TL;DR\n"
            "Ga als admin naar Admin > Users, klik op Invite, vul het werkmailadres in "
            "en kies direct de juiste rol.\n\n"
            "- Invite and remove people\n"
            "- The five roles\n"
            "- For admins\n"
            "- Getting started"
        ),
        [
            {
                "label": "1",
                "title": "For admins",
                "url": "https://getklai.getklai.com/docs/klai-help/for-admins",
                "evidence_ids": ["E1"],
            },
            {
                "label": "2",
                "title": "Getting started",
                "url": "https://getklai.getklai.com/docs/klai-help/getting-started",
                "evidence_ids": ["E2"],
            },
        ],
        query_text="Hoe voeg ik als admin een nieuwe gebruiker toe?",
        evidence_chunks=[
            {
                "evidence_id": "E1",
                "title": "For admins",
                "source_url": "https://getklai.getklai.com/docs/klai-help/for-admins",
                "text": "Admins manage users, invite teammates, and configure roles.",
            },
            {
                "evidence_id": "E2",
                "title": "Getting started",
                "source_url": "https://getklai.getklai.com/docs/klai-help/getting-started",
                "text": "Get started by inviting users and setting up your account.",
            },
            {
                "evidence_id": "E3",
                "title": "Invite and remove people",
                "source_url": "https://getklai.getklai.com/docs/klai-help/invite-and-remove-people",
                "text": (
                    "Invite a colleague from Admin > Users. Click Invite, enter their work email, "
                    "pick a starting role, and confirm the invitation."
                ),
            },
            {
                "evidence_id": "E4",
                "title": "The five roles",
                "source_url": "https://getklai.getklai.com/docs/klai-help/the-five-roles",
                "text": "Open Admin > Users. Click a user and pick a new role from the dropdown.",
            },
        ],
    )

    assert "- Invite and remove people" not in composed.content
    assert "- The five roles" not in composed.content
    assert "- For admins" not in composed.content
    assert composed.sources == [
        {
            "label": "1",
            "title": "Invite and remove people",
            "url": "https://getklai.getklai.com/docs/klai-help/invite-and-remove-people",
        }
    ]
    assert composed.decision["rejected"]
