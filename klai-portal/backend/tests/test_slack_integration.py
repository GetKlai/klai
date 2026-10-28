"""SPEC-SLACK-001: the portal side of the Slack integration.

Covers the managed Slack key (§4, §7.1), the status the admin page reads, the
disconnect from the portal (§12 test 8) and the internal link API that
klai-slack calls (§7.2), including its shared-secret guard.

synthetic-data: generator=hand-written seed=0 (org ids 41/42, invented Slack ids).
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from fastapi import HTTPException
from helpers import FakeResult, setup_db
from sqlalchemy.exc import IntegrityError

from tests.conftest import FakeOrg

_ORG_KB = SimpleNamespace(id=10, owner_type="org", owner_user_id=None)
_ORG_KB_2 = SimpleNamespace(id=11, owner_type="org", owner_user_id=None)
_PERSONAL_KB = SimpleNamespace(id=12, owner_type="user", owner_user_id="u")
_SECRET = "test-internal-secret"
_SLACK_URL = "http://klai-slack:8000"


def _db(results: list[FakeResult]) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    setup_db(db, results)
    return db


def _slack_key(**overrides) -> SimpleNamespace:
    from app.services.slack_integration import SLACK_KEY_PERMISSIONS

    values = {
        "id": "key-1",
        "org_id": 42,
        "key_prefix": "pk_live_abcd",
        "key_hash": "h",
        "permissions": dict(SLACK_KEY_PERMISSIONS),
        "rate_limit_rpm": 600,
        "last_used_at": None,
        "created_at": "2026-10-28 10:00:00+00:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _installation(**overrides) -> SimpleNamespace:
    values = {
        "org_id": 42,
        "team_id": "T0EXAMPLE1",
        "team_name": "Example Workspace",
        "installed_by_slack_user_id": "U0EXAMPLE1",
        "installed_at": "2026-10-28 10:05:00+00:00",
        "revoked_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def events(monkeypatch) -> MagicMock:
    import app.services.slack_integration as slack_integration

    emit = MagicMock()
    monkeypatch.setattr(slack_integration, "emit_event", emit)
    return emit


# ---------------------------------------------------------------------------
# The managed key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_mints_a_slack_key_with_read_access_on_the_chosen_org_kbs(events):
    from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
    from app.services.slack_integration import create_slack_key

    db = _db([FakeResult(), FakeResult([]), FakeResult([_ORG_KB, _ORG_KB_2])])

    plaintext, key = await create_slack_key(db, org_id=42, user_id="u", kb_ids=[11, 10, 11])

    added = [call.args[0] for call in db.add.call_args_list]
    assert added[0] is key and isinstance(key, PartnerAPIKey)
    assert plaintext.startswith("pk_live_") and key.key_prefix == plaintext[:12]
    assert key.name == "Slack"
    assert key.rate_limit_rpm == 600
    assert key.permissions == {
        "slack": True,
        "chat": True,
        "feedback": True,
        "knowledge_append": False,
        "general_chat": False,
    }
    access = [row for row in added if isinstance(row, PartnerApiKeyKbAccess)]
    assert sorted((row.kb_id, row.access_level) for row in access) == [(10, "read"), (11, "read")]
    db.commit.assert_awaited_once()
    assert events.call_args.args[0] == "slack.key_created"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kb_ids", "found", "status_code"),
    [
        ([], [], 400),  # at least one KB (§7.1)
        ([10, 12], [_ORG_KB, _PERSONAL_KB], 400),  # personal KBs never reach Slack (§5)
        ([10, 99], [_ORG_KB], 400),  # a KB of another org is not found
    ],
)
async def test_create_rejects_a_kb_choice_outside_the_orgs_shared_kbs(events, kb_ids, found, status_code):
    from app.services.slack_integration import create_slack_key

    db = _db([FakeResult(), FakeResult([]), FakeResult(found)])

    with pytest.raises(HTTPException) as exc:
        await create_slack_key(db, org_id=42, user_id="u", kb_ids=kb_ids)

    assert exc.value.status_code == status_code
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_refuses_a_second_slack_key_for_the_org(events):
    from app.services.slack_integration import create_slack_key

    db = _db([FakeResult(), FakeResult([_slack_key()])])

    with pytest.raises(HTTPException) as exc:
        await create_slack_key(db, org_id=42, user_id="u", kb_ids=[10])

    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_patch_replaces_the_kb_access_of_the_slack_key(events):
    from app.models.partner_api_keys import PartnerApiKeyKbAccess
    from app.services.slack_integration import set_slack_kb_access

    key = _slack_key()
    db = _db([FakeResult([key]), FakeResult([_ORG_KB_2]), FakeResult()])

    assert await set_slack_kb_access(db, org_id=42, user_id="u", kb_ids=[11]) is key

    deletes = [str(call.args[0]) for call in db.execute.await_args_list if "DELETE" in str(call.args[0])]
    assert len(deletes) == 1 and "partner_api_key_kb_access" in deletes[0]
    assert [(row.kb_id, row.access_level) for row in (c.args[0] for c in db.add.call_args_list)] == [(11, "read")]
    assert all(isinstance(c.args[0], PartnerApiKeyKbAccess) for c in db.add.call_args_list)
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    ("key", "installation", "status"),
    [
        (None, None, "not_connected"),
        (_slack_key(), None, "awaiting_link"),
        (_slack_key(), _installation(), "connected"),
        (_slack_key(), _installation(revoked_at="2026-10-28 11:00:00+00:00"), "revoked"),
    ],
)
def test_status_follows_key_and_installation(key, installation, status):
    from app.services.slack_integration import derive_status

    assert derive_status(key, installation) == status


# ---------------------------------------------------------------------------
# Disconnect from the portal (§12 test 8)
# ---------------------------------------------------------------------------


def _disconnect_db(installation) -> AsyncMock:
    return _db([FakeResult([_slack_key()]), FakeResult([installation] if installation else []), FakeResult()])


def _deleted_tables(db: AsyncMock) -> list[str]:
    tables = ("portal_slack_installations", "partner_api_key_kb_access", "partner_api_keys")
    deleted = []
    for call in db.execute.await_args_list:
        sql = str(call.args[0])
        if sql.startswith("DELETE FROM"):
            deleted.append(next(t for t in tables if sql.split()[2] == t))
    return deleted


@pytest.mark.asyncio
@respx.mock
async def test_disconnect_deletes_everything_and_revokes_the_workspace_in_klai_slack(events, monkeypatch):
    from app.services import slack_integration

    monkeypatch.setattr(slack_integration.settings, "slack_service_url", _SLACK_URL)
    monkeypatch.setattr(slack_integration.settings, "internal_secret", _SECRET)
    route = respx.delete(f"{_SLACK_URL}/internal/installations/T0EXAMPLE1").mock(return_value=httpx.Response(204))
    db = _disconnect_db(_installation())

    assert await slack_integration.disconnect_slack(db, org_id=42, user_id="u") is True

    assert _deleted_tables(db) == ["portal_slack_installations", "partner_api_key_kb_access", "partner_api_keys"]
    db.commit.assert_awaited_once()
    assert route.calls.last.request.headers["authorization"] == f"Bearer {_SECRET}"
    assert events.call_args.args[0] == "slack.disconnected"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("failure", [httpx.Response(500), httpx.ConnectError("refused")])
async def test_disconnect_reports_a_failing_klai_slack_after_deleting(events, monkeypatch, failure):
    from app.services import slack_integration

    monkeypatch.setattr(slack_integration.settings, "slack_service_url", _SLACK_URL)
    respx.delete(f"{_SLACK_URL}/internal/installations/T0EXAMPLE1").mock(side_effect=[failure])
    db = _disconnect_db(_installation())

    assert await slack_integration.disconnect_slack(db, org_id=42, user_id="u") is False

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_disconnect_without_a_deployed_klai_slack_reports_not_revoked(events, monkeypatch):
    from app.services import slack_integration

    monkeypatch.setattr(slack_integration.settings, "slack_service_url", "")
    db = _disconnect_db(_installation())

    assert await slack_integration.disconnect_slack(db, org_id=42, user_id="u") is False

    assert not respx.calls
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_disconnect_of_an_unlinked_key_needs_no_call_to_klai_slack(events, monkeypatch):
    from app.services import slack_integration

    monkeypatch.setattr(slack_integration.settings, "slack_service_url", _SLACK_URL)
    db = _disconnect_db(None)

    assert await slack_integration.disconnect_slack(db, org_id=42, user_id="u") is True

    assert not respx.calls
    assert "partner_api_keys" in _deleted_tables(db)


@pytest.mark.asyncio
@pytest.mark.parametrize(("slack_revoked", "status_code", "body"), [(True, 204, b""), (False, 200, None)])
async def test_admin_delete_is_loud_when_klai_slack_was_not_reached(monkeypatch, slack_revoked, status_code, body):
    import json

    import app.api.admin_slack as admin_slack

    monkeypatch.setattr(admin_slack, "disconnect_slack", AsyncMock(return_value=slack_revoked))

    response = await admin_slack.delete_slack(perms=SimpleNamespace(org_id=42, user_id="u"), _platform=None, db=None)

    assert response.status_code == status_code
    if body is None:
        assert json.loads(response.body) == {"slack_revoked": False}
    else:
        assert response.body == body


def test_every_admin_slack_route_requires_admin_and_the_slack_unlock():
    from app.api.admin_slack import router

    routes = [route for route in router.routes if hasattr(route, "dependant")]
    assert {(next(iter(r.methods)), r.path) for r in routes} == {
        ("GET", "/api/admin/slack"),
        ("POST", "/api/admin/slack/key"),
        ("PATCH", "/api/admin/slack"),
        ("DELETE", "/api/admin/slack"),
    }
    for route in routes:
        closures = [
            cell.cell_contents
            for dep in route.dependant.dependencies
            for cell in (getattr(dep.call, "__closure__", None) or ())
        ]
        assert "slack" in closures, route.path
        assert any(getattr(value, "value", None) == "admin" for value in closures), route.path


# ---------------------------------------------------------------------------
# Internal link API (§7.2)
# ---------------------------------------------------------------------------


def _request(token: str | None = _SECRET) -> MagicMock:
    request = MagicMock()
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    request.headers = MagicMock()
    request.headers.get = lambda key, default="": headers.get(key, default)
    request.client = MagicMock(host="172.18.0.9")
    request.method = "POST"
    request.url = MagicMock(path="/internal/slack/link")
    request.scope = {}
    request.state = MagicMock()
    return request


@pytest.fixture
def internal(monkeypatch):
    import app.api.internal_slack as internal_slack
    from app.api import internal as internal_mod

    monkeypatch.setattr(internal_mod.settings, "internal_secret", _SECRET)
    monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())
    monkeypatch.setattr(internal_slack, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal_slack, "set_tenant", AsyncMock())
    monkeypatch.setattr(internal_slack, "emit_event", MagicMock())
    scopes: list[object] = []

    @contextlib.asynccontextmanager
    async def _cross_org_scope(session):
        scopes.append(session)
        yield session

    monkeypatch.setattr(internal_slack, "cross_org_scope", _cross_org_scope)
    internal_slack.cross_org_scopes = scopes  # type: ignore[attr-defined]
    return internal_slack


def _link_body(api_key: str = "pk_live_" + "e" * 40):
    from app.api.internal_slack import LinkRequest

    return LinkRequest(api_key=api_key, team_id="T0EXAMPLE1", team_name="Example Workspace", slack_user_id="U0EXAMPLE1")


def _key_for(plaintext: str, **overrides) -> SimpleNamespace:
    import hashlib

    return _slack_key(key_hash=hashlib.sha256(plaintext.encode()).hexdigest(), **overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("secret", "token", "status_code"), [("", _SECRET, 503), (_SECRET, "wrong", 401), (_SECRET, None, 401)]
)
async def test_link_fails_closed_without_the_internal_secret(internal, monkeypatch, secret, token, status_code):
    from app.api import internal as internal_mod

    monkeypatch.setattr(internal_mod.settings, "internal_secret", secret)
    db = _db([FakeResult()])

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace(body=_link_body(), request=_request(token), db=db)

    assert exc.value.status_code == status_code
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key_row",
    [None, "normal_key"],
    ids=["unknown key", "not a slack key"],
)
async def test_link_answers_one_generic_404_for_a_key_that_is_not_a_slack_key(internal, key_row):
    plaintext = "pk_live_" + "e" * 40
    rows = [] if key_row is None else [_key_for(plaintext, permissions={"chat": True})]
    db = _db([FakeResult(rows), FakeResult([FakeOrg(id=42, platform_unlocked_features=["slack"])])])

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace(body=_link_body(plaintext), request=_request(), db=db)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Unknown Slack key"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_link_is_forbidden_for_an_org_without_the_slack_unlock(internal):
    plaintext = "pk_live_" + "e" * 40
    db = _db(
        [FakeResult([_key_for(plaintext)]), FakeResult([FakeOrg(id=42, platform_unlocked_features=["partner_api"])])]
    )

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace(body=_link_body(plaintext), request=_request(), db=db)

    assert exc.value.status_code == 403
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_link_refuses_an_org_that_is_already_linked(internal):
    plaintext = "pk_live_" + "e" * 40
    db = _db(
        [
            FakeResult([_key_for(plaintext)]),
            FakeResult([FakeOrg(id=42, platform_unlocked_features=["slack"])]),
            FakeResult([_installation()]),
        ]
    )

    response = await internal.link_slack_workspace(body=_link_body(plaintext), request=_request(), db=db)

    assert response.status_code == 409
    assert response.body == b'{"code":"org_already_linked"}'
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_link_refuses_a_workspace_linked_to_another_org_without_naming_it(internal):
    plaintext = "pk_live_" + "e" * 40
    db = _db(
        [
            FakeResult([_key_for(plaintext)]),
            FakeResult([FakeOrg(id=42, platform_unlocked_features=["slack"])]),
            FakeResult([]),
        ]
    )
    # The other org's row is invisible under this org's RLS scope; only the
    # UNIQUE(team_id) constraint sees it.
    db.flush.side_effect = IntegrityError(
        "INSERT",
        {},
        Exception('duplicate key value violates unique constraint "uq_portal_slack_installations_team_id"'),
    )

    response = await internal.link_slack_workspace(body=_link_body(plaintext), request=_request(), db=db)

    assert response.status_code == 409
    assert response.body == b'{"code":"team_already_linked"}'
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_records_the_workspace_and_returns_the_org_and_its_kbs(internal):
    from app.models.slack import PortalSlackInstallation

    plaintext = "pk_live_" + "e" * 40
    db = _db(
        [
            FakeResult([_key_for(plaintext)]),
            FakeResult([FakeOrg(id=42, name="Example Org", platform_unlocked_features=["slack"])]),
            FakeResult([]),
            FakeResult([11, 10]),
        ]
    )

    result = await internal.link_slack_workspace(body=_link_body(plaintext), request=_request(), db=db)

    assert result.model_dump() == {"org_id": 42, "org_name": "Example Org", "key_id": "key-1", "kb_ids": [10, 11]}
    row = db.add.call_args.args[0]
    assert isinstance(row, PortalSlackInstallation)
    assert (row.org_id, row.team_id, row.team_name, row.partner_api_key_id, row.installed_by_slack_user_id) == (
        42,
        "T0EXAMPLE1",
        "Example Workspace",
        "key-1",
        "U0EXAMPLE1",
    )
    internal.set_tenant.assert_awaited_once_with(db, 42)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_org", [42, None], ids=["first revoke", "already revoked or unknown"])
async def test_unlink_marks_the_workspace_revoked_and_is_idempotent(internal, revoked_org):
    db = _db([FakeResult([revoked_org] if revoked_org else [])])

    await internal.unlink_slack_workspace(team_id="T0EXAMPLE1", request=_request(), db=db)

    # The link only names the Slack team, so the one UPDATE runs cross-org.
    assert internal.cross_org_scopes == [db]
    updates = [str(call.args[0]) for call in db.execute.await_args_list if str(call.args[0]).startswith("UPDATE")]
    assert len(updates) == 1 and "revoked_at" in updates[0] and "revoked_at IS NULL" in updates[0]
    db.commit.assert_awaited_once()
