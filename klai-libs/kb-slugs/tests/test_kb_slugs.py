def test_episode_name_round_trips():
    from klai_kb_slugs import episode_name, parse_episode_name

    assert parse_episode_name(episode_name("support", "yealink-dect-configuratie")) == (
        "support",
        "yealink-dect-configuratie",
    )


def test_episode_name_keeps_colons_in_the_path():
    from klai_kb_slugs import episode_name, parse_episode_name

    name = episode_name("support", "https://help.voys.nl/a:b")
    assert parse_episode_name(name) == ("support", "https://help.voys.nl/a:b")


def test_legacy_artifact_id_names_are_not_parsed():
    """Episodes created before this scheme are named after their artifact_id.

    Returning None is what tells retrieval-api to fall back to the old
    artifact_id lookup instead of inventing a kb_slug from a uuid.
    """
    from klai_kb_slugs import parse_episode_name

    assert parse_episode_name("3f4a1c2d-8b9e-4f1a-b2c3-d4e5f6a7b8c9") is None
    assert parse_episode_name("") is None
    assert parse_episode_name("doc:support") is None
    assert parse_episode_name("doc::path") is None


def test_episode_name_survives_a_colon_in_the_kb_slug():
    """Regression (Sol review): IngestRequest.kb_slug has no shape validation.

    Without encoding, episode_name("team:blue", "page") parses back as
    ("team", "blue:page"), the document lookup never matches, and because a
    document-key edge gets its artifact_id from that lookup the fact stops
    being citable at all — silent and total for the affected KB.
    """
    from klai_kb_slugs import episode_name, parse_episode_name

    assert parse_episode_name(episode_name("team:blue", "page")) == ("team:blue", "page")


def test_episode_name_survives_other_awkward_slugs():
    from klai_kb_slugs import episode_name, parse_episode_name

    for slug in ("team/blue", "team blue", "team%3Ablue", "persoonlijk-123"):
        assert parse_episode_name(episode_name(slug, "a/b:c.md")) == (slug, "a/b:c.md")
