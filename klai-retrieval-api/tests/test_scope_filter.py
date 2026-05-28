"""Tests for visibility enforcement in _scope_filter."""
from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter

from retrieval_api.models import RetrieveRequest
from retrieval_api.services.search import _scope_filter


def _make_request(
    scope: str = "org",
    user_id: str | None = None,
    kb_slugs: list[str] | None = None,
    include_owned_private_kbs: bool = False,
) -> RetrieveRequest:
    return RetrieveRequest(
        query="test query",
        org_id="org-abc",
        scope=scope,
        user_id=user_id,
        kb_slugs=kb_slugs,
        include_owned_private_kbs=include_owned_private_kbs,
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

    def test_personal_scope_filters_by_canonical_kb_slug_only(self):
        """SPEC-RAG-PERSONAL-SCOPE-001 REQ-2: scope=personal narrows to the
        canonical Persoonlijk-KB slug, irrespective of any user_id field
        on the chunk.

        The slug template ``personal-<user_id>`` is the structural ownership
        proof: a chunk with this slug is BY CONSTRUCTION part of the
        canonical Persoonlijk-KB of the user encoded in the suffix. The
        user_id payload field is redundant defence-in-depth — and an
        ``user_id OR kb_slug`` OR-filter (the SPEC-PERSONAL-KB-#709 attempt)
        wrongly lets chunks from OTHER user-owned KBs (e.g. ``test2``)
        through via the user_id branch.

        This test pins the post-fix contract: scope=personal MUST contain
        a single direct FieldCondition on ``kb_slug`` with the canonical
        value, AND MUST NOT contain a nested should-filter on ``user_id``
        for ownership.
        """
        req = _make_request(scope="personal", user_id="user-1")
        conditions = _scope_filter(req)

        # The canonical slug filter is a direct FieldCondition, not nested in a should.
        slug_conditions = [
            c for c in conditions
            if isinstance(c, FieldCondition) and c.key == "kb_slug"
        ]
        assert len(slug_conditions) == 1, (
            f"expected exactly one kb_slug condition, got {len(slug_conditions)}"
        )
        assert slug_conditions[0].match.value == "personal-user-1", (
            f"expected canonical slug 'personal-user-1', "
            f"got {slug_conditions[0].match.value!r}"
        )

        # Regression guard: must NOT carry the pre-fix OR-filter that let
        # test2-style chunks through via the user_id branch.
        should_filters_with_user_id = [
            c for c in conditions
            if isinstance(c, Filter)
            and c.should is not None
            and any(
                isinstance(s, FieldCondition) and s.key == "user_id"
                for s in c.should
            )
        ]
        assert should_filters_with_user_id == [], (
            "scope=personal must NOT use the user_id-OR-kb_slug branch — "
            "the OR lets chunks from non-canonical user-owned KBs leak."
        )

    def test_personal_scope_canonical_slug_uses_shared_helper(self):
        """The canonical slug template lives in klai-libs/kb-slugs.

        This test imports the shared helper and asserts the filter value
        matches its output exactly — guards against retrieval-api silently
        re-inventing a different template string.
        """
        from klai_kb_slugs import personal_kb_slug

        req = _make_request(scope="personal", user_id="someone")
        conditions = _scope_filter(req)
        slug_cond = next(
            c for c in conditions
            if isinstance(c, FieldCondition) and c.key == "kb_slug"
        )
        assert slug_cond.match.value == personal_kb_slug("someone")

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

    def test_scope_both_own_private_branch_narrows_to_canonical_and_selected_slugs(self):
        """SPEC-RAG-PERSONAL-SCOPE-001 REQ-3: scope=both, personal portion
        narrows to canonical + explicitly selected private slugs.

        The visibility-should clause for scope=both/org has two branches:
        not_private (org chunks) and (visibility=private + user_id=me)
        (personal chunks). Without narrowing, ALL user-owned private
        chunks pass through the second branch — including non-canonical
        user-created private KBs (e.g. ``test2``). This is the scope=both
        sibling of the scope=personal leak fixed by REQ-2.

        Fix: add an allowed-slug condition to the private branch's must
        list so only canonical Persoonlijk-KB chunks and explicitly selected
        private KB slugs pass via the user_id-bypass.
        """
        req = _make_request(scope="both", user_id="user-42", kb_slugs=["engineering", "test2"])
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        assert vis is not None
        assert vis.should is not None and len(vis.should) == 2
        own_branch = vis.should[1]
        assert isinstance(own_branch, Filter)
        assert own_branch.must is not None
        fields_by_key = {
            c.key: c
            for c in own_branch.must
            if isinstance(c, FieldCondition)
        }
        assert "visibility" in fields_by_key
        assert "user_id" in fields_by_key
        assert "kb_slug" in fields_by_key, (
            "scope=both private branch must carry an allowed kb_slug condition"
        )
        assert fields_by_key["kb_slug"].match.any == ["personal-user-42", "engineering", "test2"]

    def test_scope_both_without_selected_slugs_blocks_non_canonical_private_kbs(self):
        """Selecting only Persoonlijk must not leak other user-owned private KBs."""
        req = _make_request(scope="both", user_id="user-42", kb_slugs=None)
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        own_branch = vis.should[1]
        slug_cond = next(
            c for c in own_branch.must
            if isinstance(c, FieldCondition) and c.key == "kb_slug"
        )
        assert slug_cond.match.any == ["personal-user-42"]

    def test_scope_both_all_collections_allows_owned_private_kbs(self):
        """include_owned_private_kbs=True keeps all org KBs broad while also
        allowing every private chunk owned by the caller.
        """
        req = _make_request(
            scope="both",
            user_id="user-42",
            kb_slugs=None,
            include_owned_private_kbs=True,
        )
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        own_branch = vis.should[1]
        keys = {c.key for c in own_branch.must if isinstance(c, FieldCondition)}
        assert keys == {"visibility", "user_id"}

    def test_scope_org_own_private_branch_does_not_add_canonical_slug(self):
        """scope=org also has the visibility-should clause but is a pure-org
        scope semantically. The own-private branch lets a user's private
        chunks through (legitimate for "their org plus their private
        notes"). REQ-3 explicitly narrows ONLY scope=both, not scope=org.

        Rationale: scope=org callers (partner_chat) never opt into
        personal narrowing; they don't model a Persoonlijk dropdown. The
        chunks that pass via the own-private branch for scope=org are
        legitimate org-personal overlaps. Don't tighten without a
        consumer asking for it.
        """
        req = _make_request(scope="org", user_id="user-42")
        conditions = _scope_filter(req)
        vis = _find_visibility_filter(conditions)
        own_branch = vis.should[1]
        keys = {c.key for c in own_branch.must if isinstance(c, FieldCondition)}
        assert "kb_slug" not in keys, (
            "scope=org private branch should NOT carry canonical kb_slug — "
            "REQ-3 narrows scope=both only."
        )

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
        assert len(bare_slug_conds) == 0, (
            "bare kb_slug FieldCondition must not exist for scope=both"
        )

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
        """scope=both + kb_slugs without user_id uses direct slug filter."""
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
