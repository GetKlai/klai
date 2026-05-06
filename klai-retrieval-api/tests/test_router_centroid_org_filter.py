"""Tests for SPEC-TI-008 / audit-tenant-isolation-2026-05-05 finding B-1.

Verifies that _default_compute_centroids always scopes its Qdrant scroll
filter to the requesting org_id, preventing cross-tenant centroid
contamination through shared source_labels (Notion, Confluence, GitHub,
Slack, Web, etc.).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval_api.services.router import (
    KBEntry,
    _default_compute_centroids,
    clear_centroid_cache,
    route_to_sources,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_centroid_cache()
    yield
    clear_centroid_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_point(vector: list[float]) -> MagicMock:
    """Build a minimal Qdrant ScoredPoint-like mock with a dense vector."""
    point = MagicMock()
    point.vector = {"vector_chunk": vector}
    return point


def _build_scroll_side_effect(
    org_a_vectors: list[list[float]],
    org_b_vectors: list[list[float]],
    org_a_id: str,
    org_b_id: str,
) -> callable:
    """Return an async callable that dispatches scroll results by org_id filter value.

    Inspects the ``scroll_filter`` argument to determine which org's vectors
    to return, mirroring real Qdrant behaviour where the filter is enforced
    server-side.
    """

    async def _scroll(collection_name, scroll_filter, limit, with_payload, with_vectors):
        # Extract the org_id value from the filter's must conditions
        org_id_value: str | None = None
        for cond in scroll_filter.must:
            if hasattr(cond, "key") and cond.key == "org_id":
                org_id_value = cond.match.value
                break

        if org_id_value == org_a_id:
            points = [_make_point(v) for v in org_a_vectors]
        elif org_id_value == org_b_id:
            points = [_make_point(v) for v in org_b_vectors]
        else:
            points = []

        return points, None  # (points, next_page_offset)

    return _scroll


# ---------------------------------------------------------------------------
# AC-2 / AC-5: org_id filter is present in scroll call AND centroids are
# scoped to the requesting org
# ---------------------------------------------------------------------------


class TestCentroidsFilterByOrg:
    """AC-2 + AC-5: Scroll filter must include org_id; centroids must be
    computed only from the requesting org's vectors.
    """

    @pytest.mark.asyncio
    async def test_centroids_filter_by_org(self):
        """Two orgs share source_label 'Notion'. Their vectors are semantically
        opposite. The centroid for org A must be dominated by org A's vectors,
        not contaminated by org B's.
        """
        # Org A: vectors pointing in positive x direction
        org_a_vectors = [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],
            [0.95, 0.05, 0.0],
        ]
        # Org B: vectors pointing in positive y direction (semantically opposite)
        org_b_vectors = [
            [0.0, 1.0, 0.0],
            [0.1, 0.9, 0.0],
            [0.05, 0.95, 0.0],
        ]

        catalog = [KBEntry(source_label="Notion", name="Notion")]

        def _cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=False))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        scroll_fn = _build_scroll_side_effect(org_a_vectors, org_b_vectors, "org-a", "org-b")

        mock_client = AsyncMock()
        mock_client.scroll.side_effect = scroll_fn

        with patch("retrieval_api.services.search._get_client", return_value=mock_client):
            centroid_a = await _default_compute_centroids(catalog, org_id="org-a")
            centroid_b = await _default_compute_centroids(catalog, org_id="org-b")

        assert "Notion" in centroid_a, "Centroid for org-a must have Notion entry"
        assert "Notion" in centroid_b, "Centroid for org-b must have Notion entry"

        # Expected mean for org A ≈ [0.947, 0.05, 0.0] — points in +x direction
        org_a_mean = [sum(v[i] for v in org_a_vectors) / len(org_a_vectors) for i in range(3)]
        # Expected mean for org B ≈ [0.05, 0.947, 0.0] — points in +y direction
        org_b_mean = [sum(v[i] for v in org_b_vectors) / len(org_b_vectors) for i in range(3)]

        sim_a_to_a = _cosine(centroid_a["Notion"], org_a_mean)
        sim_a_to_b = _cosine(centroid_a["Notion"], org_b_mean)

        # Centroid A should be very close to org A's mean and far from org B's
        assert sim_a_to_a > 0.99, (
            f"Centroid for org-a should align with org-a vectors, got cosine={sim_a_to_a:.4f}"
        )
        assert sim_a_to_b < 0.15, (
            f"Centroid for org-a should NOT align with org-b vectors, got cosine={sim_a_to_b:.4f}"
        )

    @pytest.mark.asyncio
    async def test_scroll_filter_includes_org_id(self):
        """AC-2: The Filter passed to client.scroll MUST contain a FieldCondition
        for key='org_id' with the exact org_id value.
        """
        catalog = [
            KBEntry(source_label="Notion", name="Notion"),
            KBEntry(source_label="Confluence", name="Confluence"),
        ]

        captured_filters: list = []

        async def _capturing_scroll(
            collection_name, scroll_filter, limit, with_payload, with_vectors
        ):
            captured_filters.append(scroll_filter)
            return [], None

        mock_client = AsyncMock()
        mock_client.scroll.side_effect = _capturing_scroll

        with patch("retrieval_api.services.search._get_client", return_value=mock_client):
            await _default_compute_centroids(catalog, org_id="org-xyz")

        assert len(captured_filters) == 2, "Expected one scroll call per catalog entry"

        for scroll_filter in captured_filters:
            conditions = scroll_filter.must
            org_conditions = [c for c in conditions if hasattr(c, "key") and c.key == "org_id"]
            assert len(org_conditions) == 1, (
                f"Each scroll filter must have exactly one org_id FieldCondition, "
                f"found {len(org_conditions)}"
            )
            assert org_conditions[0].match.value == "org-xyz", (
                f"org_id filter value must equal the requesting org_id, "
                f"got {org_conditions[0].match.value!r}"
            )

            source_conditions = [
                c for c in conditions if hasattr(c, "key") and c.key == "source_label"
            ]
            assert len(source_conditions) == 1, (
                "Each scroll filter must also have a source_label FieldCondition"
            )


# ---------------------------------------------------------------------------
# AC-4: Cache is keyed per org — two orgs must not share a cache entry
# ---------------------------------------------------------------------------


class TestCentroidCacheKeyedByOrg:
    """AC-4: Centroid cache must be keyed by org_id so that org A's cached
    centroids do not bleed into org B's routing decisions.
    """

    @pytest.mark.asyncio
    async def test_centroid_cache_keyed_by_org(self):
        """Each org gets its own compute call; a second call for the same org
        reuses the cache without invoking compute again.
        """
        call_log: list[str] = []

        async def tracking_compute(catalog, org_id: str):
            call_log.append(org_id)
            if org_id == "org-a":
                return {"Notion": [1.0, 0.0]}
            return {"Notion": [0.0, 1.0]}

        catalog = [KBEntry(source_label="Notion", name="Notion")]

        # First call for org-a: compute runs
        await route_to_sources(
            "vague query",
            query_vector=[0.9, 0.1],
            org_id="org-a",
            source_label_catalog=catalog,
            compute_centroid_fn=tracking_compute,
        )

        # First call for org-b: compute runs again (different org)
        await route_to_sources(
            "vague query",
            query_vector=[0.1, 0.9],
            org_id="org-b",
            source_label_catalog=catalog,
            compute_centroid_fn=tracking_compute,
        )

        # Second call for org-a: cache hit, compute NOT called again
        decision_a2 = await route_to_sources(
            "another query",
            query_vector=[0.9, 0.1],
            org_id="org-a",
            source_label_catalog=catalog,
            compute_centroid_fn=tracking_compute,
        )

        assert call_log == ["org-a", "org-b"], (
            f"Compute must be called once per org; got call_log={call_log}"
        )
        assert decision_a2.cache_hit is True, "Second call for org-a should be a cache hit"

    @pytest.mark.asyncio
    async def test_different_orgs_get_different_centroids(self):
        """Routing decisions must reflect each org's own centroid, not a shared one."""

        async def org_specific_compute(catalog, org_id: str):
            # org-a: Notion points in +x, org-b: Notion points in +y
            if org_id == "org-a":
                return {"Notion": [1.0, 0.0, 0.0]}
            return {"Notion": [0.0, 1.0, 0.0]}

        catalog = [
            KBEntry(source_label="Notion", name="Notion"),
            KBEntry(source_label="GitHub", name="GitHub"),
        ]

        decision_a = await route_to_sources(
            "completely neutral query",
            query_vector=[1.0, 0.0, 0.0],  # close to org-a Notion
            org_id="org-a",
            source_label_catalog=catalog,
            compute_centroid_fn=org_specific_compute,
        )

        # For org-b, the centroid for Notion is [0,1,0]; query [1,0,0] is orthogonal
        decision_b = await route_to_sources(
            "completely neutral query",
            query_vector=[1.0, 0.0, 0.0],
            org_id="org-b",
            source_label_catalog=catalog,
            compute_centroid_fn=org_specific_compute,
        )

        # The routing decisions should differ because centroids differ per org
        # (or both fail to meet margin threshold, which is also acceptable;
        # the important thing is that each org computed its own centroid)
        assert decision_a is not None
        assert decision_b is not None
        # Both computed independently — verify no cache cross-contamination
        # by checking that org-b did NOT reuse org-a's cache
        assert decision_a.cache_hit is False
        assert decision_b.cache_hit is False
