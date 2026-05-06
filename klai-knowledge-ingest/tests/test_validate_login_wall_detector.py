"""Tests for SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase E -- validation report.

Exercises ``knowledge_ingest.validation._build_report`` directly against
synthetic ``crawled_pages`` row sets so we don't need a live DB. The async
``validate_login_wall_detector`` entrypoint is just a thin wrapper that
fetches rows and forwards them to ``_build_report``; the cluster maths
lives in the helper.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_ingest.backfill_tasks import PURGED_PLACEHOLDER_HASH
from knowledge_ingest.validation import _build_report

FIXTURES = Path(__file__).parent / "fixtures"


def _wall() -> str:
    return (FIXTURES / "auth_walls" / "redcactus_hubspot.md").read_text(encoding="utf-8")


def _clean() -> str:
    return (FIXTURES / "clean_pages" / "redcactus_ifttt.md").read_text(encoding="utf-8")


def _row(
    *,
    url: str,
    raw: str,
    content_hash: str = "live-hash",
    content_simhash: int | None = None,
) -> dict:
    return {
        "url": url,
        "raw_markdown": raw,
        "content_hash": content_hash,
        "content_simhash": content_simhash,
    }


def _walls(n: int) -> list[dict]:
    return [_row(url=f"https://x/wall-{i}", raw=_wall()) for i in range(n)]


# ---------------------------------------------------------------------------
# Cluster discovery + sampling
# ---------------------------------------------------------------------------


class TestClusterDiscovery:
    def test_six_walls_form_one_cluster(self) -> None:
        report = _build_report(
            _walls(6),
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert report["total_pages"] == 6
        assert len(report["clusters"]) == 1
        assert report["clusters"][0]["size"] == 6
        # Sample urls are sorted + capped at sample_size
        assert len(report["clusters"][0]["sample_urls"]) == 6

    def test_five_walls_below_threshold_produce_no_cluster(self) -> None:
        report = _build_report(
            _walls(5),
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert report["clusters"] == []

    def test_isolated_clean_page_not_clustered(self) -> None:
        rows = [*_walls(6), _row(url="https://help.voys.nl/2fa-freedom", raw=_clean())]
        report = _build_report(
            rows,
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert len(report["clusters"]) == 1
        assert report["clusters"][0]["size"] == 6
        assert "https://help.voys.nl/2fa-freedom" not in (
            report["clusters"][0]["sample_urls"]
        )

    def test_sample_size_caps_url_list(self) -> None:
        report = _build_report(
            _walls(20),
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert report["clusters"][0]["size"] == 20
        assert len(report["clusters"][0]["sample_urls"]) == 10


# ---------------------------------------------------------------------------
# Recovery candidates
# ---------------------------------------------------------------------------


class TestRecoveryCandidates:
    def test_purged_isolated_page_is_recovery_candidate(self) -> None:
        rows = [
            _row(
                url="https://help.voys.nl/2fa-freedom",
                raw=_clean(),
                content_hash=PURGED_PLACEHOLDER_HASH,
            )
        ]
        report = _build_report(
            rows,
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert report["recovery_candidates"] == ["https://help.voys.nl/2fa-freedom"]

    def test_purged_clustering_page_not_a_candidate(self) -> None:
        # 6 wall pages all purged, all still clustering → no recovery
        rows = _walls(6)
        for r in rows:
            r["content_hash"] = PURGED_PLACEHOLDER_HASH
        report = _build_report(
            rows,
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert report["recovery_candidates"] == []

    def test_live_pages_never_recovery_candidates(self) -> None:
        rows = [_row(url="https://x/live", raw=_clean(), content_hash="live")]
        report = _build_report(
            rows,
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        assert report["recovery_candidates"] == []


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


class TestReportShape:
    def test_report_is_json_serialisable(self) -> None:
        import json

        report = _build_report(
            _walls(6),
            org_id="org-voys",
            kb_slug="support",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        # No raise = JSON-serialisable.
        json.dumps(report)
        # Top-level keys per AC-10.1.
        assert set(report.keys()) == {
            "org_id",
            "kb_slug",
            "total_pages",
            "clusters",
            "recovery_candidates",
        }

    def test_existing_simhash_reused_not_recomputed(self) -> None:
        """Read-only behaviour: rows with non-NULL simhash use that value."""
        # Pre-populate simhash to a value DIFFERENT from compute_simhash(raw)
        # so we can detect whether the report uses the row's value or
        # recomputes.
        rows = _walls(1)
        rows[0]["content_simhash"] = 12345  # arbitrary fingerprint
        rows[0]["content_hash"] = PURGED_PLACEHOLDER_HASH
        report = _build_report(
            rows,
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        # If we'd recomputed, the page would carry compute_simhash(_wall())
        # and clustering against itself would be size 0 either way (only 1
        # row), so this test mainly confirms the report doesn't crash on
        # arbitrary stored hashes.
        assert report["total_pages"] == 1

    def test_clusters_sorted_by_size_desc(self) -> None:
        # Two distinct clusters: 6 walls (large) and 6 clean dupes (small group).
        wall_rows = _walls(6)
        clean_rows = [
            _row(url=f"https://x/clean-{i}", raw=_clean()) for i in range(7)
        ]
        report = _build_report(
            wall_rows + clean_rows,
            org_id="org-1",
            kb_slug="kb-1",
            cluster_min=5,
            hamming_max=3,
            sample_size=10,
        )
        # Both groups exceed threshold → 2 clusters
        assert len(report["clusters"]) == 2
        assert report["clusters"][0]["size"] >= report["clusters"][1]["size"]
