"""Tests for connector content_type field (SPEC-EVIDENCE-001, R10)."""

from datetime import UTC

from app.api.connectors import (
    ConnectorCreateRequest,
    ConnectorOut,
    ConnectorUpdateRequest,
    _connector_out,
)

# -- Schema tests --------------------------------------------------------------


class TestConnectorCreateRequest:
    def test_content_type_defaults_to_none(self):
        """content_type is optional and defaults to None."""
        req = ConnectorCreateRequest(
            name="My connector",
            connector_type="github",
            config={},
        )
        assert req.content_type is None

    def test_content_type_accepts_valid_value(self):
        """content_type accepts known content type values."""
        req = ConnectorCreateRequest(
            name="My connector",
            connector_type="web_crawler",
            config={},
            content_type="kb_article",
        )
        assert req.content_type == "kb_article"


class TestConnectorUpdateRequest:
    def test_content_type_defaults_to_none(self):
        """content_type is optional and defaults to None in update."""
        req = ConnectorUpdateRequest()
        assert req.content_type is None

    def test_content_type_can_be_set(self):
        """content_type can be set in update request."""
        req = ConnectorUpdateRequest(content_type="meeting_transcript")
        assert req.content_type == "meeting_transcript"


class TestConnectorOut:
    def test_includes_content_type(self):
        """ConnectorOut includes content_type field."""
        from datetime import datetime

        out = ConnectorOut(
            id="abc-123",
            kb_id=1,
            name="Test",
            connector_type="github",
            config={},
            schedule=None,
            is_enabled=True,
            last_sync_at=None,
            last_sync_status=None,
            created_at=datetime.now(UTC),
            created_by="user-1",
            content_type="kb_article",
            allowed_assertion_modes=[],
        )
        assert out.content_type == "kb_article"

    def test_content_type_nullable(self):
        """ConnectorOut allows None content_type."""
        from datetime import datetime

        out = ConnectorOut(
            id="abc-123",
            kb_id=1,
            name="Test",
            connector_type="github",
            config={},
            schedule=None,
            is_enabled=True,
            last_sync_at=None,
            last_sync_status=None,
            created_at=datetime.now(UTC),
            created_by="user-1",
            content_type=None,
            allowed_assertion_modes=[],
        )
        assert out.content_type is None


# -- Default content_type per connector_type -----------------------------------


CONNECTOR_TYPE_DEFAULTS = {
    "web_crawler": "web_crawl",
    "github": "kb_article",
    "notion": "kb_article",
    "google_drive": "pdf_document",
    "ms_docs": "kb_article",
}


class TestContentTypeDefaults:
    def test_default_content_type_mapping(self):
        """Each connector_type should have a defined default content_type."""
        from app.api.connectors import CONTENT_TYPE_DEFAULTS

        for connector_type, expected in CONNECTOR_TYPE_DEFAULTS.items():
            assert CONTENT_TYPE_DEFAULTS[connector_type] == expected, (
                f"Expected default for {connector_type} to be {expected}"
            )

    def test_all_connector_types_have_defaults(self):
        """Every ConnectorType should have a default content_type."""
        from app.api.connectors import CONTENT_TYPE_DEFAULTS

        expected_types = {"github", "notion", "web_crawler", "google_drive", "ms_docs"}
        assert set(CONTENT_TYPE_DEFAULTS.keys()) == expected_types


# -- _connector_out helper -----------------------------------------------------


class TestConnectorOutHelper:
    def test_connector_out_includes_content_type(self):
        """_connector_out maps the content_type column."""
        from unittest.mock import MagicMock

        mock_connector = MagicMock()
        mock_connector.id = "conn-1"
        mock_connector.kb_id = 1
        mock_connector.name = "Test"
        mock_connector.connector_type = "github"
        mock_connector.config = {}
        mock_connector.schedule = None
        mock_connector.is_enabled = True
        mock_connector.last_sync_at = None
        mock_connector.last_sync_status = None
        mock_connector.created_at = "2026-01-01T00:00:00Z"
        mock_connector.created_by = "user-1"
        mock_connector.content_type = "kb_article"

        out = _connector_out(mock_connector)
        assert out.content_type == "kb_article"
