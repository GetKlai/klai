"""Tests for Pydantic Field constraints (TASK-010)."""
import pytest
from pydantic import ValidationError

from knowledge_ingest.models import IngestRequest, RetrieveRequest


class TestIngestRequestValidation:
    def test_content_over_500k_rejected(self):
        with pytest.raises(ValidationError, match="content"):
            IngestRequest(
                org_id="org1",
                kb_slug="test",
                path="test.md",
                content="x" * 500_001,
            )

    def test_content_at_500k_accepted(self):
        req = IngestRequest(
            org_id="org1",
            kb_slug="test",
            path="test.md",
            content="x" * 500_000,
        )
        assert len(req.content) == 500_000


class TestRetrieveRequestValidation:
    def test_query_over_2k_rejected(self):
        with pytest.raises(ValidationError, match="query"):
            RetrieveRequest(org_id="org1", query="x" * 2_001)

    def test_query_at_2k_accepted(self):
        req = RetrieveRequest(org_id="org1", query="x" * 2_000)
        assert len(req.query) == 2_000

    def test_top_k_over_50_rejected(self):
        with pytest.raises(ValidationError, match="top_k"):
            RetrieveRequest(org_id="org1", query="test", top_k=51)

    def test_top_k_under_1_rejected(self):
        with pytest.raises(ValidationError, match="top_k"):
            RetrieveRequest(org_id="org1", query="test", top_k=0)

    def test_top_k_at_50_accepted(self):
        req = RetrieveRequest(org_id="org1", query="test", top_k=50)
        assert req.top_k == 50

    def test_top_k_at_1_accepted(self):
        req = RetrieveRequest(org_id="org1", query="test", top_k=1)
        assert req.top_k == 1

    def test_user_id_field_exists(self):
        req = RetrieveRequest(org_id="org1", query="test", user_id="user123")
        assert req.user_id == "user123"
