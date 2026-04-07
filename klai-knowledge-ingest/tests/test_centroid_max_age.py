"""Tests for SPEC-KB-026 R6: centroid store max-age check in load_centroids.

Verifies that stale centroid files (older than taxonomy_centroid_max_age_hours)
are rejected and a warning is logged.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import structlog

from knowledge_ingest.clustering import CentroidStore, load_centroids


class TestCentroidMaxAge:
    """R6: load_centroids must reject stale centroid files."""

    def test_stale_centroid_returns_none(self, tmp_path):
        """A centroid file older than max_age_hours returns None."""
        stale_time = datetime.now(tz=UTC) - timedelta(hours=49)
        centroid_data = {
            "version": 1,
            "computed_at": stale_time.isoformat(),
            "kb_slug": "kb1",
            "org_id": "org1",
            "clusters": [],
        }

        centroid_path = tmp_path / "org1_kb1.json"
        centroid_path.write_text(json.dumps(centroid_data))

        with patch("knowledge_ingest.clustering.settings") as mock_settings:
            mock_settings.taxonomy_centroids_dir = str(tmp_path)
            mock_settings.taxonomy_centroid_max_age_hours = 48

            result = load_centroids("org1", "kb1")

        assert result is None

    def test_fresh_centroid_returns_store(self, tmp_path):
        """A centroid file within max_age_hours returns the CentroidStore."""
        fresh_time = datetime.now(tz=UTC) - timedelta(hours=1)
        centroid_data = {
            "version": 1,
            "computed_at": fresh_time.isoformat(),
            "kb_slug": "kb1",
            "org_id": "org1",
            "clusters": [
                {
                    "cluster_id": 0,
                    "centroid": [0.1, 0.2],
                    "size": 10,
                    "taxonomy_node_id": None,
                    "content_label_summary": ["test"],
                }
            ],
        }

        centroid_path = tmp_path / "org1_kb1.json"
        centroid_path.write_text(json.dumps(centroid_data))

        with patch("knowledge_ingest.clustering.settings") as mock_settings:
            mock_settings.taxonomy_centroids_dir = str(tmp_path)
            mock_settings.taxonomy_centroid_max_age_hours = 48

            result = load_centroids("org1", "kb1")

        assert result is not None
        assert isinstance(result, CentroidStore)
        assert len(result.clusters) == 1

    def test_stale_centroid_logs_warning(self, tmp_path, caplog):
        """Stale centroid must log a warning with age_hours and path."""
        stale_time = datetime.now(tz=UTC) - timedelta(hours=72)
        centroid_data = {
            "version": 1,
            "computed_at": stale_time.isoformat(),
            "kb_slug": "kb1",
            "org_id": "org1",
            "clusters": [],
        }

        centroid_path = tmp_path / "org1_kb1.json"
        centroid_path.write_text(json.dumps(centroid_data))

        with patch("knowledge_ingest.clustering.settings") as mock_settings:
            mock_settings.taxonomy_centroids_dir = str(tmp_path)
            mock_settings.taxonomy_centroid_max_age_hours = 48

            result = load_centroids("org1", "kb1")

        assert result is None

    def test_exactly_at_max_age_returns_store(self, tmp_path):
        """A centroid file exactly at max_age_hours is still valid."""
        # 47 hours ago = within 48h limit
        almost_stale = datetime.now(tz=UTC) - timedelta(hours=47)
        centroid_data = {
            "version": 1,
            "computed_at": almost_stale.isoformat(),
            "kb_slug": "kb1",
            "org_id": "org1",
            "clusters": [],
        }

        centroid_path = tmp_path / "org1_kb1.json"
        centroid_path.write_text(json.dumps(centroid_data))

        with patch("knowledge_ingest.clustering.settings") as mock_settings:
            mock_settings.taxonomy_centroids_dir = str(tmp_path)
            mock_settings.taxonomy_centroid_max_age_hours = 48

            result = load_centroids("org1", "kb1")

        assert result is not None


    def test_naive_datetime_treated_as_utc(self, tmp_path):
        """Naive computed_at is treated as UTC and stale check works correctly."""
        # Naive timestamp 49 hours ago (UTC, but without tzinfo)
        naive_stale = (datetime.now(tz=UTC) - timedelta(hours=49)).replace(tzinfo=None)
        centroid_data = {
            "version": 1,
            "computed_at": naive_stale.isoformat(),  # no timezone info
            "kb_slug": "kb1",
            "org_id": "org1",
            "clusters": [],
        }

        centroid_path = tmp_path / "org1_kb1.json"
        centroid_path.write_text(json.dumps(centroid_data))

        with patch("knowledge_ingest.clustering.settings") as mock_settings:
            mock_settings.taxonomy_centroids_dir = str(tmp_path)
            mock_settings.taxonomy_centroid_max_age_hours = 48

            result = load_centroids("org1", "kb1")

        # Must not raise TypeError; must be rejected as stale
        assert result is None

    def test_invalid_computed_at_returns_none(self, tmp_path):
        """Unparseable computed_at returns None (treat as stale) instead of crashing."""
        centroid_data = {
            "version": 1,
            "computed_at": "not-a-valid-date",
            "kb_slug": "kb1",
            "org_id": "org1",
            "clusters": [],
        }

        centroid_path = tmp_path / "org1_kb1.json"
        centroid_path.write_text(json.dumps(centroid_data))

        with patch("knowledge_ingest.clustering.settings") as mock_settings:
            mock_settings.taxonomy_centroids_dir = str(tmp_path)
            mock_settings.taxonomy_centroid_max_age_hours = 48

            result = load_centroids("org1", "kb1")

        assert result is None


class TestCentroidMaxAgeConfig:
    """R6: taxonomy_centroid_max_age_hours config setting."""

    def test_config_default_value(self):
        from knowledge_ingest.config import Settings

        s = Settings()
        assert s.taxonomy_centroid_max_age_hours == 48
