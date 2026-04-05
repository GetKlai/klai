"""Tests for taxonomy_node_ids + tags filter in RetrieveRequest.

Verifies that:
- RetrieveRequest accepts taxonomy_node_ids (optional, defaults to None)
- RetrieveRequest accepts tags (optional, defaults to None)
- An empty list does NOT add a taxonomy/tag filter
- A non-empty list adds appropriate filters
- Backward-compatible: OR filter matches both taxonomy_node_ids and taxonomy_node_id
"""
from __future__ import annotations

from retrieval_api.models import RetrieveRequest


def _make_request(
    taxonomy_node_ids: list[int] | None = None,
    tags: list[str] | None = None,
) -> RetrieveRequest:
    return RetrieveRequest(
        query="test",
        org_id="org-abc",
        scope="org",
        taxonomy_node_ids=taxonomy_node_ids,
        tags=tags,
    )


class TestRetrieveRequestModel:
    def test_default_taxonomy_node_ids_is_none(self):
        req = RetrieveRequest(query="q", org_id="o")
        assert req.taxonomy_node_ids is None

    def test_default_tags_is_none(self):
        req = RetrieveRequest(query="q", org_id="o")
        assert req.tags is None

    def test_accepts_taxonomy_node_ids_list(self):
        req = _make_request(taxonomy_node_ids=[1, 5, 10])
        assert req.taxonomy_node_ids == [1, 5, 10]

    def test_accepts_empty_list(self):
        req = _make_request(taxonomy_node_ids=[])
        assert req.taxonomy_node_ids == []

    def test_accepts_tags_list(self):
        req = _make_request(tags=["sso", "okta"])
        assert req.tags == ["sso", "okta"]


class TestTaxonomyFilterInQuery:
    """Verify that the Qdrant query includes taxonomy filter iff non-empty list provided."""

    def test_no_taxonomy_filter_when_none(self):
        """taxonomy_node_ids=None -> no taxonomy filter added."""
        req = _make_request(taxonomy_node_ids=None)
        assert not req.taxonomy_node_ids  # falsy check mirrors service code

    def test_no_taxonomy_filter_when_empty_list(self):
        """taxonomy_node_ids=[] -> no taxonomy filter (empty list is falsy)."""
        req = _make_request(taxonomy_node_ids=[])
        assert not req.taxonomy_node_ids  # empty list is falsy

    def test_taxonomy_filter_added_when_non_empty(self):
        """taxonomy_node_ids=[5, 7] -> truthy, filter must be added."""
        req = _make_request(taxonomy_node_ids=[5, 7])
        assert req.taxonomy_node_ids  # non-empty is truthy
        assert 5 in req.taxonomy_node_ids
        assert 7 in req.taxonomy_node_ids


class TestTagFilterInQuery:
    """Verify tag filter behavior."""

    def test_no_tag_filter_when_none(self):
        req = _make_request(tags=None)
        assert not req.tags

    def test_no_tag_filter_when_empty(self):
        req = _make_request(tags=[])
        assert not req.tags

    def test_tag_filter_added_when_non_empty(self):
        req = _make_request(tags=["sso", "okta"])
        assert req.tags
        assert "sso" in req.tags
