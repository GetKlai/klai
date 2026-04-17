"""Smoke tests for admin widgets endpoints — SPEC-WIDGET-002.

Verifies the endpoint module imports, Pydantic schemas validate,
and the helper functions exist. Widget-specific: no key_prefix,
no permissions, no active field anywhere in the schema.
"""

from unittest.mock import MagicMock


def test_module_imports():
    """All admin_widgets symbols import without error."""
    from app.api.admin_widgets import (
        create_widget,
        delete_widget,
        get_widget_detail,
        list_widgets,
        router,
        update_widget,
    )

    assert router.prefix == "/api/widgets"
    assert callable(create_widget)
    assert callable(list_widgets)
    assert callable(get_widget_detail)
    assert callable(update_widget)
    assert callable(delete_widget)


def test_create_request_schema():
    """CreateWidgetRequest accepts valid input with widget_config."""
    from app.api.admin_widgets import CreateWidgetRequest, WidgetConfig

    req = CreateWidgetRequest(
        name="Test Widget",
        description="Help bot",
        kb_ids=[1, 2],
        rate_limit_rpm=60,
        widget_config=WidgetConfig(
            allowed_origins=["https://example.com"],
            title="Help",
            welcome_message="Hi!",
            css_variables={},
        ),
    )
    assert req.name == "Test Widget"
    assert len(req.kb_ids) == 2
    assert req.widget_config.title == "Help"


def test_create_request_rejects_short_name():
    """CreateWidgetRequest rejects names shorter than 3 chars."""
    import pytest

    from app.api.admin_widgets import CreateWidgetRequest

    with pytest.raises(ValueError):
        CreateWidgetRequest(name="ab", kb_ids=[])


def test_response_has_no_api_key_fields():
    """WidgetResponse has no API-key-specific fields."""
    from app.api.admin_widgets import WidgetResponse

    fields = set(WidgetResponse.model_fields.keys())
    assert "key_prefix" not in fields
    assert "key_hash" not in fields
    assert "permissions" not in fields
    assert "active" not in fields
    assert "integration_type" not in fields
    # Widget-specific fields present
    assert "widget_id" in fields
    assert "widget_config" in fields


def test_widget_config_defaults():
    """WidgetConfig has sensible defaults."""
    from app.api.admin_widgets import WidgetConfig

    config = WidgetConfig()
    assert config.allowed_origins == []
    assert config.title == ""
    assert config.welcome_message == ""
    assert config.css_variables == {}


def test_widget_to_response_helper():
    """_widget_to_response produces correct output."""
    from app.api.admin_widgets import _widget_to_response

    widget = MagicMock()
    widget.id = "uuid-1"
    widget.name = "Help Bot"
    widget.description = None
    widget.widget_id = "wgt_abc123"
    widget.widget_config = {
        "allowed_origins": ["https://example.com"],
        "title": "Help",
        "welcome_message": "Hi",
        "css_variables": {},
    }
    widget.rate_limit_rpm = 60
    widget.last_used_at = None
    widget.created_at = "2026-01-01"
    widget.created_by = "user-1"

    resp = _widget_to_response(widget, kb_access_count=1)
    assert resp.name == "Help Bot"
    assert resp.widget_id == "wgt_abc123"
    assert resp.widget_config.title == "Help"
    assert resp.kb_access_count == 1


def test_generate_widget_id():
    """generate_widget_id produces wgt_ prefix with 40 hex chars."""
    from app.models.widgets import generate_widget_id

    wid = generate_widget_id()
    assert wid.startswith("wgt_")
    assert len(wid) == 44  # wgt_ (4) + 40 hex
