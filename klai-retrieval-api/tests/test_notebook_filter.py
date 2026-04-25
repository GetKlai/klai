"""Tests for the personal-vs-team notebook visibility filter.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-5: ``_search_notebook`` MUST filter klai_focus
chunks by ``owner_user_id`` when the notebook is personal, symmetric with
``_search_knowledge``'s ``user_id`` filter on private chunks.

The filter is built from the chunk-level payload field ``notebook_visibility``
written at ingest time by klai-focus/research-api. AC-4 requires the cross-
user-same-org regression: user A in org X cannot read user B's personal
notebook in org X.
"""

from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter

from retrieval_api.models import RetrieveRequest
from retrieval_api.services.search import _notebook_filter


def _request(*, user_id: str | None = "user-A", notebook_id: str | None = "n-1") -> RetrieveRequest:
    return RetrieveRequest(
        query="confidential",
        org_id="org-X",
        scope="notebook",
        user_id=user_id,
        notebook_id=notebook_id,
    )


def _find_visibility_should(conditions: list[FieldCondition | Filter]) -> Filter | None:
    """Return the ``Filter(should=[...])`` block that gates by notebook_visibility."""
    for c in conditions:
        if isinstance(c, Filter) and c.should is not None:
            return c
    return None


class TestNotebookFilterShape:
    def test_includes_tenant_and_notebook_match(self) -> None:
        conditions = _notebook_filter(_request())

        kinds = [c for c in conditions if isinstance(c, FieldCondition)]
        keys = {c.key for c in kinds}
        assert "tenant_id" in keys
        assert "notebook_id" in keys

    def test_includes_visibility_should_block(self) -> None:
        conditions = _notebook_filter(_request())

        visibility = _find_visibility_should(conditions)
        assert visibility is not None
        assert visibility.should is not None
        # Two branches: team-leg and personal-leg
        assert len(visibility.should) == 2

    def test_team_branch_matches_visibility_org(self) -> None:
        # Team-equivalent chunks pass tenant gate without owner check.
        # Value is "org" — mirrors Notebook.scope in research-api so no
        # translation layer sits between the DB record and Qdrant payload.
        conditions = _notebook_filter(_request())
        visibility = _find_visibility_should(conditions)
        assert visibility is not None
        team_branch = visibility.should[0]
        assert isinstance(team_branch, FieldCondition)
        assert team_branch.key == "notebook_visibility"
        assert team_branch.match is not None
        # MatchValue.value is the matched literal.
        assert getattr(team_branch.match, "value", None) == "org"

    def test_personal_branch_requires_owner_user_id(self) -> None:
        # Personal chunks pass only when owner_user_id matches the requester.
        conditions = _notebook_filter(_request(user_id="user-A"))
        visibility = _find_visibility_should(conditions)
        assert visibility is not None
        personal_branch = visibility.should[1]
        assert isinstance(personal_branch, Filter)
        assert personal_branch.must is not None
        # Two conditions: visibility=personal AND owner_user_id=user-A
        keys: list[str] = []
        for cond in personal_branch.must:
            assert isinstance(cond, FieldCondition)
            keys.append(cond.key)
        assert "notebook_visibility" in keys
        assert "owner_user_id" in keys

    def test_personal_branch_owner_matches_request_user_id(self) -> None:
        conditions = _notebook_filter(_request(user_id="user-XYZ"))
        visibility = _find_visibility_should(conditions)
        assert visibility is not None
        personal_branch = visibility.should[1]
        assert isinstance(personal_branch, Filter)
        assert personal_branch.must is not None
        owner_cond = next(
            c
            for c in personal_branch.must
            if isinstance(c, FieldCondition) and c.key == "owner_user_id"
        )
        assert getattr(owner_cond.match, "value", None) == "user-XYZ"


class TestNotebookFilterCrossUserRegression:
    """AC-4: filter constructed for user A MUST encode user A as the owner check.

    A subsequent Qdrant query against chunks belonging to user B in the same
    org returns zero hits for the personal-leg, and the team-leg only matches
    when the chunk is explicitly marked team-visibility.
    """

    def test_user_a_filter_does_not_reference_user_b(self) -> None:
        conditions = _notebook_filter(_request(user_id="user-A"))
        rendered = repr(conditions)
        assert "user-A" in rendered
        assert "user-B" not in rendered
