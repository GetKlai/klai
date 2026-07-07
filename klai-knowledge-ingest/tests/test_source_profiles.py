from knowledge_ingest.source_profiles import (
    DEFAULT_ASSERTION_MODES,
    resolve_source_knowledge_profile,
)


def test_default_profile_exposes_full_taxonomy_without_assigning_mode():
    profile = resolve_source_knowledge_profile(
        source_type="file",
        content_type="document",
    )

    assert profile.profile_name == "file:document"
    assert profile.allowed_assertion_modes == DEFAULT_ASSERTION_MODES
    assert profile.default_synthesis_depth == 0


def test_docs_profile_preserves_existing_synthesis_depth_default():
    profile = resolve_source_knowledge_profile(
        source_type="docs",
        content_type="kb_article",
    )

    assert profile.profile_name == "docs:kb_article"
    assert profile.default_synthesis_depth == 4


def test_valid_caller_modes_narrow_profile_and_preserve_order():
    profile = resolve_source_knowledge_profile(
        source_type="connector",
        content_type="kb_article",
        allowed_assertion_modes=["quoted", "factual", "quoted", "invalid"],
    )

    assert profile.allowed_assertion_modes == ("quoted", "factual")


def test_invalid_or_empty_caller_modes_fall_back_to_full_taxonomy():
    invalid_profile = resolve_source_knowledge_profile(
        source_type="connector",
        content_type="kb_article",
        allowed_assertion_modes=["invalid"],
    )
    empty_profile = resolve_source_knowledge_profile(
        source_type="connector",
        content_type="kb_article",
        allowed_assertion_modes=[],
    )

    assert invalid_profile.allowed_assertion_modes == DEFAULT_ASSERTION_MODES
    assert empty_profile.allowed_assertion_modes == DEFAULT_ASSERTION_MODES


def test_profile_name_combines_source_connector_and_content_type():
    profile = resolve_source_knowledge_profile(
        source_type="crawl",
        connector_type="web_crawler",
        content_type="kb_article",
    )

    assert profile.profile_name == "crawl:web_crawler:kb_article"


def test_unknown_content_type_still_includes_source_type():
    profile = resolve_source_knowledge_profile(
        source_type="crawl",
        content_type="unknown",
    )

    assert profile.profile_name == "crawl:unknown"
    assert profile.allowed_assertion_modes == DEFAULT_ASSERTION_MODES
