from __future__ import annotations

import pytest

import klai_kb_scope_policy as policy


@pytest.mark.parametrize(
    ("feature", "action", "reason"),
    [
        ({"enabled": False, "kb_retrieval_enabled": True, "kb_narrow": False}, "general", None),
        (
            {"enabled": False, "kb_retrieval_enabled": True, "kb_narrow": True},
            "strict_no_kb",
            "kb-feature-disabled",
        ),
        ({"enabled": True, "kb_retrieval_enabled": False, "kb_narrow": False}, "general", None),
        (
            {"enabled": True, "kb_retrieval_enabled": False, "kb_narrow": True},
            "strict_no_kb",
            "kb-retrieval-disabled",
        ),
        ({"enabled": True, "kb_retrieval_enabled": True, "kb_narrow": False}, "continue", None),
        ({"enabled": True, "kb_retrieval_enabled": True, "kb_narrow": True}, "continue", None),
    ],
)
def test_feature_gate_matrix(feature, action, reason):
    decision = policy.resolve_kb_feature_gate(feature)

    assert decision.action == action
    assert decision.kb_narrow is bool(feature["kb_narrow"])
    assert decision.strict_no_kb_reason == reason


def test_feature_gate_partial_dict_fails_closed_without_key_error():
    decision = policy.resolve_kb_feature_gate({"kb_narrow": True})

    assert decision.action == "strict_no_kb"
    assert decision.kb_narrow is True
    assert decision.strict_no_kb_reason == "kb-feature-disabled"


@pytest.mark.parametrize(
    (
        "feature",
        "action",
        "scope",
        "kb_slugs_for_request",
        "include_owned_private_kbs",
        "kbs_in_scope",
        "reason",
    ),
    [
        (
            {"kb_personal_enabled": False, "kb_slugs_filter": [], "kb_narrow": False},
            "general",
            None,
            None,
            False,
            None,
            None,
        ),
        (
            {"kb_personal_enabled": False, "kb_slugs_filter": [], "kb_narrow": True},
            "strict_no_kb",
            None,
            None,
            False,
            None,
            "kb-scopes-disabled",
        ),
        (
            {"kb_personal_enabled": True, "kb_slugs_filter": [], "kb_narrow": False},
            "continue",
            "personal",
            None,
            False,
            [],
            None,
        ),
        (
            {"kb_personal_enabled": True, "kb_slugs_filter": None, "kb_narrow": True},
            "continue",
            "both",
            None,
            True,
            [],
            None,
        ),
        (
            {"kb_personal_enabled": False, "kb_slugs_filter": None, "kb_narrow": False},
            "continue",
            "org",
            None,
            False,
            [],
            None,
        ),
        (
            {"kb_personal_enabled": True, "kb_slugs_filter": ["engineering", "product"], "kb_narrow": False},
            "continue",
            "both",
            ["engineering", "product"],
            False,
            ["engineering", "product"],
            None,
        ),
        (
            {"kb_personal_enabled": False, "kb_slugs_filter": ["engineering"], "kb_narrow": True},
            "continue",
            "org",
            ["engineering"],
            False,
            ["engineering"],
            None,
        ),
    ],
)
def test_retrieval_scope_tri_state_matrix(
    feature,
    action,
    scope,
    kb_slugs_for_request,
    include_owned_private_kbs,
    kbs_in_scope,
    reason,
):
    decision = policy.resolve_kb_retrieval_scope(feature)

    assert decision.action == action
    assert decision.kb_narrow is bool(feature["kb_narrow"])
    assert decision.scope == scope
    assert decision.kb_slugs_for_request == kb_slugs_for_request
    assert decision.include_owned_private_kbs is include_owned_private_kbs
    assert decision.kbs_in_scope == kbs_in_scope
    assert decision.strict_no_kb_reason == reason


def test_retrieval_scope_defaults_to_personal_enabled_all_org_kbs():
    decision = policy.resolve_kb_retrieval_scope({"kb_narrow": False})

    assert decision.action == "continue"
    assert decision.scope == "both"
    assert decision.kb_slugs_for_request is None
    assert decision.include_owned_private_kbs is True
    assert decision.kbs_in_scope == []


def test_build_retrieve_body_uses_resolved_scope_contract():
    scope_decision = policy.resolve_kb_retrieval_scope(
        {
            "kb_personal_enabled": True,
            "kb_slugs_filter": ["engineering"],
            "kb_narrow": True,
        }
    )

    body = policy.build_retrieve_body(
        rewritten_query="rewritten",
        raw_query="raw",
        org_id="org",
        user_id="user",
        top_k=20,
        conversation_history=[{"role": "user", "content": "raw"}],
        telemetry_level="full",
        scope_decision=scope_decision,
        taxonomy_applied=True,
        classified_node_ids=["node-1"],
    )

    assert body == {
        "query": "rewritten",
        "raw_query": "raw",
        "org_id": "org",
        "user_id": "user",
        "scope": "both",
        "top_k": 20,
        "conversation_history": [{"role": "user", "content": "raw"}],
        "telemetry_level": "full",
        "kb_narrow": True,
        "kb_slugs": ["engineering"],
        "taxonomy_node_ids": ["node-1"],
    }


def test_build_retrieve_body_adds_owned_private_flag_for_all_collections():
    scope_decision = policy.resolve_kb_retrieval_scope(
        {"kb_personal_enabled": True, "kb_slugs_filter": None, "kb_narrow": False}
    )

    body = policy.build_retrieve_body(
        rewritten_query="q",
        raw_query="q",
        org_id="org",
        user_id="user",
        top_k=20,
        conversation_history=[],
        telemetry_level="shadow",
        scope_decision=scope_decision,
        taxonomy_applied=False,
        classified_node_ids=[],
    )

    assert body["scope"] == "both"
    assert "kb_slugs" not in body
    assert body["include_owned_private_kbs"] is True
    assert "taxonomy_node_ids" not in body


def test_build_retrieve_body_for_org_explicit_subset():
    scope_decision = policy.resolve_kb_retrieval_scope(
        {"kb_personal_enabled": False, "kb_slugs_filter": ["engineering"], "kb_narrow": False}
    )

    body = policy.build_retrieve_body(
        rewritten_query="q",
        raw_query="q",
        org_id="org",
        user_id="user",
        top_k=20,
        conversation_history=[],
        telemetry_level="shadow",
        scope_decision=scope_decision,
        taxonomy_applied=False,
        classified_node_ids=[],
    )

    assert body["scope"] == "org"
    assert body["kb_slugs"] == ["engineering"]
    assert "include_owned_private_kbs" not in body
    assert "taxonomy_node_ids" not in body


def test_build_retrieve_body_rejects_non_continuing_scope():
    scope_decision = policy.resolve_kb_retrieval_scope(
        {"kb_personal_enabled": False, "kb_slugs_filter": [], "kb_narrow": False}
    )

    with pytest.raises(ValueError):
        policy.build_retrieve_body(
            rewritten_query="q",
            raw_query="q",
            org_id="org",
            user_id="user",
            top_k=20,
            conversation_history=[],
            telemetry_level="shadow",
            scope_decision=scope_decision,
            taxonomy_applied=False,
            classified_node_ids=[],
        )


@pytest.mark.parametrize(
    (
        "taxonomy_enabled",
        "kbs_in_scope",
        "kbs_with_coverage",
        "classified_node_ids",
        "applied",
        "skip_reason",
    ),
    [
        (False, ["kb-a"], ["kb-a"], ["node-1"], False, "disabled"),
        (True, [], [], ["node-1"], False, "no_kbs_in_scope"),
        (True, ["kb-a"], [], ["node-1"], False, "all_kbs_low_coverage"),
        (True, ["kb-a"], ["kb-a"], [], False, "empty_classify"),
        (True, ["kb-a"], ["kb-a"], ["node-1"], True, None),
    ],
)
def test_taxonomy_decision_matrix(
    taxonomy_enabled,
    kbs_in_scope,
    kbs_with_coverage,
    classified_node_ids,
    applied,
    skip_reason,
):
    decision = policy.resolve_kb_taxonomy_decision(
        taxonomy_enabled=taxonomy_enabled,
        kbs_in_scope=kbs_in_scope,
        kbs_with_coverage=kbs_with_coverage,
        classified_node_ids=classified_node_ids,
    )

    assert decision.applied is applied
    assert decision.skip_reason == skip_reason
