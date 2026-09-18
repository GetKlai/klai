"""Tests for connector content_type field (SPEC-EVIDENCE-001, R10)."""

from datetime import UTC
from pathlib import Path
from runpy import run_path
from typing import get_args
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.connectors import (
    ConnectorCreateRequest,
    ConnectorOut,
    ConnectorUpdateRequest,
    _connector_out,
)
from tests.conftest import make_perms

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
        assert req.clear_credentials is False

    def test_content_type_can_be_set(self):
        """content_type can be set in update request."""
        req = ConnectorUpdateRequest(content_type="meeting_transcript")
        assert req.content_type == "meeting_transcript"

    def test_clear_credentials_can_be_set(self):
        """clear_credentials is an explicit opt-in update action."""
        req = ConnectorUpdateRequest(clear_credentials=True)
        assert req.clear_credentials is True


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
        assert out.has_saved_credentials is False

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
            "json_feed",
            "hubspot_support",  # SPEC-RAG-SUPPORT-GAP
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
        mock_connector.encrypted_credentials = b"encrypted-placeholder"

        out = _connector_out(mock_connector)
        assert out.content_type == "kb_article"
        assert out.has_saved_credentials is True


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

    def test_create_connector_accepts_json_feed(self):
        """ConnectorCreateRequest accepts connector_type='json_feed'."""
        req = ConnectorCreateRequest(
            name="Prices feed",
            connector_type="json_feed",
            config={"url": "https://data.example.com/prices.json"},
        )
        assert req.connector_type == "json_feed"

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

        new_types = {"airtable", "confluence", "google_docs", "google_sheets", "google_slides", "json_feed"}
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
            "json_feed",
        }
        assert expected_types.issubset(set(CONTENT_TYPE_DEFAULTS.keys()))

    def test_database_constraint_matches_connector_type_literal(self):
        from app.api.connectors import ConnectorType

        # SPEC-RAG-SUPPORT-GAP: hubspot_support extended the CHECK constraint via
        # migration s1p2c3a4s5e6; its _CONNECTOR_TYPES must match the Literal.
        support_migration = run_path(
            str(Path(__file__).parents[1] / "alembic/versions/s1p2c3a4s5e6_add_support_cases.py")
        )
        assert set(support_migration["_CONNECTOR_TYPES"]) == set(get_args(ConnectorType))
        assert "hubspot_support" in get_args(ConnectorType)

        # Earlier json_feed migration remains a regression anchor.
        json_feed_migration = run_path(
            str(Path(__file__).parents[1] / "alembic/versions/9bf37c021a4e_add_json_feed_connector_type.py")
        )
        assert set(json_feed_migration["CONNECTOR_TYPES_AFTER"]).issubset(set(get_args(ConnectorType)))
        assert "json_feed" not in json_feed_migration["CONNECTOR_TYPES_BEFORE"]


# -- knowledge_gaps rollout gate on hubspot_support connectors -----------------
# SPEC-RAG-SUPPORT-GAP: support collection/analysis is only permitted while the
# tenant has ``knowledge_gaps`` in PortalOrg.platform_unlocked_features (source
# of truth, fail-closed when absent). The lifecycle boundary here gates
# create / reconfigure / re-enable / trigger-sync for the hubspot_support type,
# while leaving disable / delete / clear-credentials cleanup and every other
# connector type untouched.


def _org(*, unlocked: list[str]) -> MagicMock:
    org = MagicMock()
    org.id = 101
    org.zitadel_org_id = "zitadel-org-101"
    org.platform_unlocked_features = unlocked
    return org


def _hubspot_connector(*, is_enabled: bool = True, connector_type: str = "hubspot_support") -> MagicMock:
    connector = MagicMock()
    connector.id = "conn-hs-1"
    connector.kb_id = 1
    connector.connector_type = connector_type
    connector.is_enabled = is_enabled
    connector.last_sync_status = None
    connector.last_sync_at = None
    connector.state = "active"
    return connector


def _kb() -> MagicMock:
    kb = MagicMock()
    kb.id = 1
    kb.slug = "support-kb"
    kb.owner_type = "org"
    return kb


def _execute_returning(connector: MagicMock) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = connector
    return AsyncMock(return_value=result)


class TestHubspotSupportFeatureGate:
    @pytest.mark.asyncio
    async def test_create_hubspot_support_blocked_when_feature_off(self) -> None:
        from app.api.connectors import create_connector

        db = AsyncMock()
        with (
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors.check_connector_allowed"),
            patch("app.api.connectors._load_org_or_500", new=AsyncMock(return_value=_org(unlocked=[]))),
        ):
            with pytest.raises(HTTPException) as exc:
                await create_connector(
                    kb_slug="support-kb",
                    body=ConnectorCreateRequest(name="HS", connector_type="hubspot_support", config={}),
                    perms=make_perms(),
                    db=db,
                )
        assert exc.value.status_code == 403
        assert exc.value.detail == {"error_code": "feature_not_unlocked", "feature": "knowledge_gaps"}
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_sync_blocked_when_feature_off_before_network(self) -> None:
        from app.api.connectors import trigger_sync

        connector = _hubspot_connector()
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch("app.api.connectors._load_org_or_500", new=AsyncMock(return_value=_org(unlocked=[]))),
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors.assert_can_add_item_to_kb", new=AsyncMock()),
            patch("app.api.connectors.klai_connector_client") as client,
        ):
            client.trigger_sync = AsyncMock()
            with pytest.raises(HTTPException) as exc:
                await trigger_sync(kb_slug="support-kb", connector_id="conn-hs-1", perms=make_perms(), db=db)
        assert exc.value.status_code == 403
        # Never reach klai-connector: no sync run, no credential decrypt downstream.
        client.trigger_sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trigger_sync_allowed_when_feature_on(self) -> None:
        from app.api.connectors import trigger_sync

        connector = _hubspot_connector()
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch(
                "app.api.connectors._load_org_or_500",
                new=AsyncMock(return_value=_org(unlocked=["knowledge_gaps"])),
            ),
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors.assert_can_add_item_to_kb", new=AsyncMock()),
            patch("app.api.connectors.emit_event"),
            patch("app.api.connectors.klai_connector_client") as client,
        ):
            client.trigger_sync = AsyncMock(return_value=MagicMock(started_at=None))
            await trigger_sync(kb_slug="support-kb", connector_id="conn-hs-1", perms=make_perms(), db=db)
        client.trigger_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_sync_generic_connector_not_gated_when_feature_off(self) -> None:
        from app.api.connectors import trigger_sync

        connector = _hubspot_connector(connector_type="github")
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch("app.api.connectors._load_org_or_500", new=AsyncMock(return_value=_org(unlocked=[]))),
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors.assert_can_add_item_to_kb", new=AsyncMock()),
            patch("app.api.connectors.emit_event"),
            patch("app.api.connectors.klai_connector_client") as client,
        ):
            client.trigger_sync = AsyncMock(return_value=MagicMock(started_at=None))
            await trigger_sync(kb_slug="support-kb", connector_id="conn-hs-1", perms=make_perms(), db=db)
        client.trigger_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_response_preserves_a_failure_already_reported_by_worker(self) -> None:
        from datetime import UTC, datetime, timedelta

        from app.api.connectors import trigger_sync

        connector = _hubspot_connector()
        started = datetime.now(UTC)
        completed = started + timedelta(seconds=1)
        db = AsyncMock()
        db.execute = _execute_returning(connector)

        async def completed_callback(*args, **kwargs):
            connector.last_sync_status = "failed"
            connector.last_sync_at = completed

        db.refresh.side_effect = completed_callback
        with (
            patch("app.api.connectors._load_org_or_500", AsyncMock(return_value=_org(unlocked=["knowledge_gaps"]))),
            patch("app.api.connectors._get_kb_with_owner_check", AsyncMock(return_value=_kb())),
            patch("app.api.connectors.assert_can_add_item_to_kb", AsyncMock()),
            patch("app.api.connectors.emit_event"),
            patch(
                "app.api.connectors.klai_connector_client.trigger_sync",
                AsyncMock(return_value=MagicMock(started_at=started)),
            ),
        ):
            await trigger_sync(kb_slug="support-kb", connector_id="conn-hs-1", perms=make_perms(), db=db)

        assert connector.last_sync_status == "failed"
        assert connector.last_sync_at == completed

    @pytest.mark.asyncio
    async def test_update_reconfigure_blocked_when_feature_off(self) -> None:
        from app.api.connectors import update_connector

        connector = _hubspot_connector()
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors._load_org_or_500", new=AsyncMock(return_value=_org(unlocked=[]))),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_connector(
                    kb_slug="support-kb",
                    connector_id="conn-hs-1",
                    body=ConnectorUpdateRequest(config={"lookback_days": 45}),
                    perms=make_perms(),
                    db=db,
                )
        assert exc.value.status_code == 403
        assert exc.value.detail == {"error_code": "feature_not_unlocked", "feature": "knowledge_gaps"}

    @pytest.mark.asyncio
    async def test_update_reenable_blocked_when_feature_off(self) -> None:
        from app.api.connectors import update_connector

        connector = _hubspot_connector(is_enabled=False)
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors._load_org_or_500", new=AsyncMock(return_value=_org(unlocked=[]))),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_connector(
                    kb_slug="support-kb",
                    connector_id="conn-hs-1",
                    body=ConnectorUpdateRequest(is_enabled=True),
                    perms=make_perms(),
                    db=db,
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_update_disable_allowed_when_feature_off(self) -> None:
        from app.api.connectors import update_connector

        connector = _hubspot_connector()
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors._connector_out", return_value=MagicMock()),
            patch(
                "app.api.connectors._load_org_or_500",
                new=AsyncMock(side_effect=AssertionError("feature gate must not load org for a pure disable")),
            ),
        ):
            await update_connector(
                kb_slug="support-kb",
                connector_id="conn-hs-1",
                body=ConnectorUpdateRequest(is_enabled=False),
                perms=make_perms(),
                db=db,
            )
        assert connector.is_enabled is False

    @pytest.mark.asyncio
    async def test_update_clear_credentials_allowed_when_feature_off(self) -> None:
        from app.api.connectors import update_connector

        connector = _hubspot_connector()
        db = AsyncMock()
        db.execute = _execute_returning(connector)
        with (
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=_kb())),
            patch("app.api.connectors._connector_out", return_value=MagicMock()),
            patch(
                "app.api.connectors._load_org_or_500",
                new=AsyncMock(side_effect=AssertionError("feature gate must not load org for a credential clear")),
            ),
        ):
            await update_connector(
                kb_slug="support-kb",
                connector_id="conn-hs-1",
                body=ConnectorUpdateRequest(clear_credentials=True),
                perms=make_perms(),
                db=db,
            )
        assert connector.encrypted_credentials is None
