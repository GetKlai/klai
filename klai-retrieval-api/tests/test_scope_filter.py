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

    def test_kb_slugs_filter_org_scope(self):
        """kb_slugs filter added as a direct FieldCondition for scope=org."""
        req = _make_request(scope="org", kb_slugs=["kb-a", "kb-b"])
        conditions = _scope_filter(req)
        slug_conds = [c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"]
        assert len(slug_conds) == 1

    def test_kb_slugs_both_scope_with_user_bypasses_personal_chunks(self):
        """scope=both + kb_slugs: personal chunks bypass the slug filter.

        The slug filter must not exclude personal KB chunks when the user has
        personal KB enabled. kb_slugs is an org-only filter.

        The resulting condition must be a Filter(should=[slug_match, user_id_match])
        so that a chunk passes if it matches a slug OR belongs to the requesting user.
        """
        req = _make_request(scope="both", user_id="user-42", kb_slugs=["engineering"])
        conditions = _scope_filter(req)

        # Must NOT be a bare FieldCondition on kb_slug (that would exclude personal chunks)
        bare_slug_conds = [
            c for c in conditions
            if isinstance(c, FieldCondition) and c.key == "kb_slug"
        ]
        assert len(bare_slug_conds) == 0, "bare kb_slug FieldCondition must not exist for scope=both"

        # Must be a Filter(should=[...]) containing both slug and user_id bypass
        slug_should_filters = [
            c for c in conditions
            if isinstance(c, Filter) and c.should is not None
            and any(
                isinstance(s, FieldCondition) and s.key == "kb_slug"
                for s in c.should
            )
        ]
        assert len(slug_should_filters) == 1, "expected one slug should-filter"
        should_filter = slug_should_filters[0]
        keys = set()
        for s in should_filter.should:
            if isinstance(s, FieldCondition):
                keys.add(s.key)
        assert "kb_slug" in keys
        assert "user_id" in keys

    def test_kb_slugs_both_scope_without_user_falls_back_to_direct_filter(self):
        """scope=both + kb_slugs without user_id: direct slug FieldCondition (no bypass possible)."""
        req = _make_request(scope="both", user_id=None, kb_slugs=["engineering"])
        conditions = _scope_filter(req)
        slug_conds = [c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"]
        assert len(slug_conds) == 1

    def test_org_id_always_first_condition(self):
        req = _make_request(scope="org")
        conditions = _scope_filter(req)
        assert isinstance(conditions[0], FieldCondition)
        assert conditions[0].key == "org_id"
        assert conditions[0].match.value == "org-abc"
