"""Tests for visibility enforcement in _scope_filter."""
from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter

from retrieval_api.models import RetrieveRequest
from retrieval_api.services.search import _scope_filter


def _make_request(scope: str = "org", user_id: str | None = None, kb_slugs: list[str] | None = None) -> RetrieveRequest:
    return RetrieveRequest(
        query="test query",
        org_id="org-abc",
        scope=scope,
        user_id=user_id,
        kb_slugs=kb_slugs,
    )


def _find_visibility_filter(conditions: list) -> Filter | None:
    """Return the nested visibility Filter (should=[...]) if present."""
    for cond in conditions:
        if isinstance(cond, Filter) and cond.should is not None:
            return cond
    return None


class TestScopeFilterVisibility:
    def test_org_scope_includes_visibility_filter(self):
        req = _make_request(scope="org")
        conditions = _scope_filter(req)
        assert _find_visibility_filter(conditions) is not None

    def test_both_scope_includes_visibility_filter(self):
        req = _make_request(scope="both")
        conditions = _scope_filter(req)
        assert _find_visibility_filter(conditions) is not None

    def test_personal_scope_no_visibility_filter(self):
        """personal scope already restricts to one user — no extra visibility gate needed."""
        req = _make_request(scope="personal", user_id="user-1")
        conditions = _scope_filter(req)
        assert _find_visibility_filter(conditions) is None

    def test_org_scope_without_user_only_public_branch(self):
        """Without user_id, only the not-private branch is present (no own-private exception)."""
        req = _make_request(scope="org", user_id=None)
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        assert vis is not None
        assert vis.should is not None
        assert len(vis.should) == 1  # only not_private branch

    def test_org_scope_with_user_includes_own_private_branch(self):
        """With user_id, should has two branches: not-private + own-private."""
        req = _make_request(scope="org", user_id="user-99")
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        assert vis is not None
        assert vis.should is not None
        assert len(vis.should) == 2

    def test_not_private_branch_uses_must_not(self):
        """The first branch excludes chunks where visibility='private'."""
        req = _make_request(scope="org", user_id=None)
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        not_private_branch = vis.should[0]
        assert isinstance(not_private_branch, Filter)
        assert not_private_branch.must_not is not None
        cond = not_private_branch.must_not[0]
        assert isinstance(cond, FieldCondition)
        assert cond.key == "visibility"

    def test_own_private_branch_matches_user_id(self):
        """Second branch (own-private) must match visibility=private AND user_id."""
        req = _make_request(scope="org", user_id="user-42")
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        own_branch = vis.should[1]
        assert isinstance(own_branch, Filter)
        assert own_branch.must is not None
        keys = {c.key for c in own_branch.must if isinstance(c, FieldCondition)}
        assert "visibility" in keys
        assert "user_id" in keys

    def test_kb_slugs_filter_is_always_added(self):
        """kb_slugs filter is appended regardless of visibility logic."""
        req = _make_request(scope="org", kb_slugs=["kb-a", "kb-b"])
        conditions = _scope_filter(req)
        slug_conds = [c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"]
        assert len(slug_conds) == 1

    def test_org_id_always_first_condition(self):
        req = _make_request(scope="org")
        conditions = _scope_filter(req)
        assert isinstance(conditions[0], FieldCondition)
        assert conditions[0].key == "org_id"
        assert conditions[0].match.value == "org-abc"
