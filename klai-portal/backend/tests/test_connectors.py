"""Tests for connector content_type field (SPEC-EVIDENCE-001, R10)."""

from datetime import UTC

import pytest
from pydantic import ValidationError

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
            last_sync_documents_ok=None,
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
            last_sync_documents_ok=None,
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
        """Every ConnectorType should have a default content_type.

        Updated by SPEC-KB-CONNECTORS-001 R6 to include the five new connector types.
        """
        from app.api.connectors import CONTENT_TYPE_DEFAULTS

        expected_types = {
            "github",
            "notion",
            "web_crawler",
            "google_drive",
            "ms_docs",
            "airtable",
            "confluence",
            "google_docs",
            "google_sheets",
            "google_slides",
        }
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


# -- ConnectorType Literal extension (SPEC-KB-CONNECTORS-001 R6) ---------------


class TestConnectorTypeLiteral:
    """Regression + acceptance tests for SPEC-KB-CONNECTORS-001 Phase 5.

    RED phase: these tests fail until the Literal is extended to include the
    five new connector types: airtable, confluence, google_docs, google_sheets,
    google_slides.
    """

    # --- New types accepted ---------------------------------------------------

    def test_create_connector_accepts_airtable(self):
        """ConnectorCreateRequest accepts connector_type='airtable'."""
        req = ConnectorCreateRequest(
            name="Airtable connector",
            connector_type="airtable",
            config={},
        )
        assert req.connector_type == "airtable"

    def test_create_connector_accepts_confluence(self):
        """ConnectorCreateRequest accepts connector_type='confluence'."""
        req = ConnectorCreateRequest(
            name="Confluence connector",
            connector_type="confluence",
            config={},
        )
        assert req.connector_type == "confluence"

    def test_create_connector_accepts_google_docs(self):
        """ConnectorCreateRequest accepts connector_type='google_docs'."""
        req = ConnectorCreateRequest(
            name="Google Docs connector",
            connector_type="google_docs",
            config={},
        )
        assert req.connector_type == "google_docs"

    def test_create_connector_accepts_google_sheets(self):
        """ConnectorCreateRequest accepts connector_type='google_sheets'."""
        req = ConnectorCreateRequest(
            name="Google Sheets connector",
            connector_type="google_sheets",
            config={},
        )
        assert req.connector_type == "google_sheets"

    def test_create_connector_accepts_google_slides(self):
        """ConnectorCreateRequest accepts connector_type='google_slides'."""
        req = ConnectorCreateRequest(
            name="Google Slides connector",
            connector_type="google_slides",
            config={},
        )
        assert req.connector_type == "google_slides"

    # --- Unknown type rejected ------------------------------------------------

    def test_create_connector_rejects_unknown_type(self):
        """Pydantic rejects an unrecognised connector_type with a 422-equivalent ValidationError."""
        with pytest.raises(ValidationError):
            ConnectorCreateRequest(
                name="Bad connector",
                connector_type="invalid_type",
                config={},
            )

    # --- Existing types still accepted (regression guard) ---------------------

    def test_create_connector_still_accepts_github(self):
        """Regression: existing type 'github' still accepted after Literal extension."""
        req = ConnectorCreateRequest(
            name="GitHub connector",
            connector_type="github",
            config={},
        )
        assert req.connector_type == "github"

    def test_create_connector_still_accepts_notion(self):
        """Regression: existing type 'notion' still accepted after Literal extension."""
        req = ConnectorCreateRequest(
            name="Notion connector",
            connector_type="notion",
            config={},
        )
        assert req.connector_type == "notion"

    def test_create_connector_still_accepts_web_crawler(self):
        """Regression: existing type 'web_crawler' still accepted after Literal extension."""
        req = ConnectorCreateRequest(
            name="Web crawler connector",
            connector_type="web_crawler",
            config={},
        )
        assert req.connector_type == "web_crawler"

    def test_create_connector_still_accepts_google_drive(self):
        """Regression: existing type 'google_drive' still accepted after Literal extension."""
        req = ConnectorCreateRequest(
            name="Google Drive connector",
            connector_type="google_drive",
            config={},
        )
        assert req.connector_type == "google_drive"

    def test_create_connector_still_accepts_ms_docs(self):
        """Regression: existing type 'ms_docs' still accepted after Literal extension."""
        req = ConnectorCreateRequest(
            name="MS Docs connector",
            connector_type="ms_docs",
            config={},
        )
        assert req.connector_type == "ms_docs"


# -- CONTENT_TYPE_DEFAULTS coverage for new types (SPEC-KB-CONNECTORS-001 R6) -


class TestContentTypeDefaultsExtended:
    """Verify CONTENT_TYPE_DEFAULTS covers all ConnectorType values including new ones."""

    def test_new_types_have_defaults(self):
        """All five new connector types have entries in CONTENT_TYPE_DEFAULTS."""
        from app.api.connectors import CONTENT_TYPE_DEFAULTS

        new_types = {"airtable", "confluence", "google_docs", "google_sheets", "google_slides"}
        for connector_type in new_types:
            assert connector_type in CONTENT_TYPE_DEFAULTS, (
                f"Missing CONTENT_TYPE_DEFAULTS entry for '{connector_type}'"
            )

    def test_all_connector_types_have_defaults_extended(self):
        """Every ConnectorType value (old + new) has a default content_type entry."""
        from app.api.connectors import CONTENT_TYPE_DEFAULTS

        expected_types = {
            "github",
            "notion",
            "web_crawler",
            "google_drive",
            "ms_docs",
            "airtable",
            "confluence",
            "google_docs",
            "google_sheets",
            "google_slides",
        }
        assert expected_types.issubset(set(CONTENT_TYPE_DEFAULTS.keys()))
