"""Pin the ``KnowledgeFeatureResponse.personal_kb_slug`` contract.

The LiteLLM hook relies on this field to scope a "Persoonlijk"-only
retrieve call to exactly the canonical personal KB. We MUST NOT let
the slug-template diverge between this service (which provisions
personal KBs) and the LiteLLM hook (which used to reconstruct the slug
from string parts before SPEC-RAG-PERSONAL-SLUG-CONTRACT landed).

Pure schema test: builds the Pydantic model and checks that the field
exists, defaults to None, and round-trips a real slug.
"""

from __future__ import annotations

from app.api.internal import KnowledgeFeatureResponse
from app.services.default_knowledge_bases import personal_kb_slug


def test_personal_kb_slug_defaults_to_none() -> None:
    response = KnowledgeFeatureResponse(enabled=True)
    assert response.personal_kb_slug is None


def test_personal_kb_slug_round_trips_a_value() -> None:
    response = KnowledgeFeatureResponse(
        enabled=True,
        personal_kb_slug="personal-300000000000000002",
    )
    dumped = response.model_dump()
    assert dumped["personal_kb_slug"] == "personal-300000000000000002"


def test_personal_kb_slug_matches_helper_output() -> None:
    """The hook builds its filter from this exact field. The slug it
    receives MUST equal what ``personal_kb_slug(user_id)`` produces on
    the portal-api side — otherwise the filter targets a non-existent
    KB and the user gets zero results."""
    user_id = "300000000000000002"
    expected = personal_kb_slug(user_id)
    response = KnowledgeFeatureResponse(
        enabled=True,
        personal_kb_slug=expected,
    )
    assert response.personal_kb_slug == expected
