"""Tests for knowledge_ingest/content_profiles.py"""
from knowledge_ingest.content_profiles import PROFILES, ContentTypeProfile, get_profile


def test_get_profile_kb_article():
    profile = get_profile("kb_article")
    assert isinstance(profile, ContentTypeProfile)
    assert profile.content_type == "kb_article"
    assert profile.context_strategy == "first_n"


def test_get_profile_meeting_transcript():
    profile = get_profile("meeting_transcript")
    assert isinstance(profile, ContentTypeProfile)
    assert profile.content_type == "meeting_transcript"
    assert profile.context_strategy == "rolling_window"


def test_get_profile_unknown():
    profile = get_profile("unknown")
    assert isinstance(profile, ContentTypeProfile)
    assert profile.content_type == "unknown"


def test_get_profile_nonexistent_falls_back_to_unknown():
    profile = get_profile("nonexistent_type")
    assert profile.content_type == "unknown"
    assert profile is PROFILES["unknown"]


def test_hype_conditional_kb_article_depth_0_enabled():
    profile = get_profile("kb_article")
    assert profile.hype_enabled(0) is True


def test_hype_conditional_kb_article_depth_3_disabled():
    profile = get_profile("kb_article")
    assert profile.hype_enabled(3) is False


def test_hype_always_meeting_transcript_depth_0():
    profile = get_profile("meeting_transcript")
    assert profile.hype_enabled(0) is True


def test_hype_always_meeting_transcript_depth_4():
    profile = get_profile("meeting_transcript")
    assert profile.hype_enabled(4) is True


def test_hype_never_unknown_depth_0():
    profile = get_profile("unknown")
    assert profile.hype_enabled(0) is False


def test_hype_never_unknown_depth_99():
    profile = get_profile("unknown")
    assert profile.hype_enabled(99) is False


def test_all_six_content_types_defined():
    expected = {
        "kb_article",
        "meeting_transcript",
        "1on1_transcript",
        "email_thread",
        "pdf_document",
        "unknown",
    }
    assert set(PROFILES.keys()) == expected


def test_all_profiles_have_valid_context_strategy():
    from knowledge_ingest.context_strategies import STRATEGIES
    for name, profile in PROFILES.items():
        assert profile.context_strategy in STRATEGIES, (
            f"{name} has unknown strategy: {profile.context_strategy}"
        )
