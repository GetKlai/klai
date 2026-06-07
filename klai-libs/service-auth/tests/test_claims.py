"""Tests for project_role_scopes (SPEC-SEC-SERVICE-AUTH-002 REQ-4b + pin)."""

from __future__ import annotations

from klai_service_auth import project_role_scopes

_PROJ = "362771533686374406"


def test_pinned_project_roles_are_extracted():
    payload = {
        f"urn:zitadel:iam:org:project:{_PROJ}:roles": {
            "klai:internal:retrieval:query": {"362757920133283846": "klai.localhost"}
        }
    }
    assert project_role_scopes(payload, _PROJ) == {"klai:internal:retrieval:query"}


def test_roles_from_a_different_project_are_ignored():
    # Security pin: a roles claim for an UNRELATED project must not inject a
    # scope, even if the role key collides with one of ours.
    payload = {
        "urn:zitadel:iam:org:project:999999999999999999:roles": {
            "klai:internal:retrieval:query": {}
        }
    }
    assert project_role_scopes(payload, _PROJ) == set()


def test_empty_project_id_returns_empty():
    payload = {f"urn:zitadel:iam:org:project:{_PROJ}:roles": {"x": {}}}
    assert project_role_scopes(payload, "") == set()


def test_absent_roles_claim_returns_empty():
    assert project_role_scopes({"scope": "openid"}, _PROJ) == set()


def test_non_dict_roles_claim_returns_empty():
    payload = {f"urn:zitadel:iam:org:project:{_PROJ}:roles": "not-a-dict"}
    assert project_role_scopes(payload, _PROJ) == set()
