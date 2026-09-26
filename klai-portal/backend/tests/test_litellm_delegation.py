from app.services.litellm_delegation import with_delegated_org, with_feature_tag


def test_with_feature_tag_adds_metadata_tags():
    body = with_feature_tag({"model": "klai-fast"}, "portal:conversation-judge")

    assert body["metadata"]["tags"] == ["portal:conversation-judge"]


def test_with_feature_tag_adds_not_replaces_existing_metadata():
    body = {"model": "klai-fast", "metadata": {"_klai_delegated_org_id": "zorg-1"}}

    body = with_feature_tag(body, "portal:answer-grounding")

    assert body["metadata"]["_klai_delegated_org_id"] == "zorg-1"
    assert body["metadata"]["tags"] == ["portal:answer-grounding"]


def test_with_feature_tag_composes_with_delegated_org_either_order():
    body = with_delegated_org({"model": "klai-fast"}, "zorg-2")
    body = with_feature_tag(body, "portal:turn-judge")

    assert body["metadata"] == {"_klai_delegated_org_id": "zorg-2", "tags": ["portal:turn-judge"]}


def test_with_feature_tag_keeps_existing_tags():
    body = with_feature_tag({"model": "klai-fast", "metadata": {"tags": ["caller:existing"]}}, "portal:turn-judge")

    assert body["metadata"]["tags"] == ["caller:existing", "portal:turn-judge"]
