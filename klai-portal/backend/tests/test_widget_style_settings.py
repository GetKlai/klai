from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.admin.settings import OrgSettingsUpdate, update_org_settings
from app.models.portal import PortalOrg
from tests.conftest import make_perms


@pytest.mark.asyncio
async def test_tenant_can_save_clear_and_preserve_widget_style_defaults():
    org = SimpleNamespace(
        name="Voys", default_language="nl", mfa_policy="optional", primary_domain="voys.nl",
        auto_accept_same_domain=False, telemetry_level="shadow", pii_masked_entities=[],
        pii_allow_list=[], widget_css_variables={},
    )
    db = AsyncMock()
    db.get.return_value = org
    perms = make_perms(role="admin", org_id=8)
    styles = {"--klai-background-color": "#ffffff", "--klai-message-gap": "20px"}
    result = await update_org_settings(OrgSettingsUpdate(widget_css_variables=styles), perms, db)
    assert result.widget_css_variables == styles
    assert org.widget_css_variables == styles
    db.get.assert_awaited_with(PortalOrg, 8)
    await update_org_settings(OrgSettingsUpdate(default_language="en"), perms, db)
    assert org.widget_css_variables == styles
    result = await update_org_settings(OrgSettingsUpdate(widget_css_variables={}), perms, db)
    assert result.widget_css_variables == {}


@pytest.mark.parametrize("styles", [
    {"background": "red"},
    {"--klai-background-color": "red; } body { display:none"},
    {"--klai-message-gap": "-20px"},
    {"--klai-message-font-size": "100px"},
])
def test_tenant_style_defaults_reject_unsupported_or_disruptive_css(styles):
    with pytest.raises(ValidationError):
        OrgSettingsUpdate(widget_css_variables=styles)
