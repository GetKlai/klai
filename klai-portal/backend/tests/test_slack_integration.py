"""SPEC-SLACK-001: the portal side of the Slack integration.

Covers the managed Slack key (§4, §7.1), the status the admin page reads, the
disconnect from the portal (§12 test 8), the internal link API that
klai-slack calls (§7.2), including its shared-secret guard, and the
portal-driven OAuth link (§8 target state: ``/connect`` and ``/link-oauth``).

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
        (_slack_key(), None, "not_connected"),  # an orphan key; the next connect replaces it
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
        ("POST", "/api/admin/slack/connect"),
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
    request.url = MagicMock(path="/internal/slack/link-oauth")
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


# ---------------------------------------------------------------------------
# Portal-driven OAuth link (§8 target state)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_returns_the_token_beside_a_klai_slack_url_that_does_not_carry_it(events, monkeypatch):
    import jwt

    import app.api.admin_slack as admin_slack
    from app.services import slack_integration

    monkeypatch.setattr(slack_integration.settings, "internal_secret", _SECRET)
    monkeypatch.setattr(slack_integration.settings, "domain", "getklai.com")
    monkeypatch.setattr(slack_integration.settings, "slack_service_public_url", "https://api.getklai.com")
    db = _db([FakeResult([]), FakeResult([_ORG_KB, _ORG_KB_2])])

    result = await admin_slack.connect_slack(
        body=admin_slack.SlackKbIdsIn(kb_ids=[11, 10]),
        perms=SimpleNamespace(org_id=42, org_slug="example", user_id="u"),
        _platform=None,
        db=db,
    )

    # The browser form-POSTs the token: a URL lands in access logs and history.
    assert result.connect_url == "https://api.getklai.com/slack/connect"
    claims = jwt.decode(result.token, _SECRET, algorithms=["HS256"])
    assert claims.pop("exp") - claims.pop("iat") == 15 * 60
    assert claims.pop("jti")
    assert claims == {
        "typ": "slack_link",
        "org_id": 42,
        "user_id": "u",
        "kb_ids": [10, 11],
        "return_url": "https://example.getklai.com/admin/slack",
    }
    db.add.assert_not_called()  # nothing is minted before Slack OAuth succeeded
    db.commit.assert_not_awaited()
    assert events.call_args.args[0] == "slack.connect_started"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "kb_ids", "status_code"),
    [
        ([FakeResult([]), FakeResult([])], [], 400),  # at least one KB (§7.1)
        ([FakeResult([]), FakeResult([_ORG_KB, _PERSONAL_KB])], [10, 12], 400),  # never personal (§5)
        ([FakeResult([]), FakeResult([_ORG_KB])], [10, 99], 400),  # a KB of another org is not found
        ([FakeResult([_slack_key()]), FakeResult([_installation()])], [10], 409),
    ],
    ids=["no kb", "personal kb", "kb of another org", "linked workspace"],
)
async def test_connect_refuses_a_bad_kb_choice_and_an_org_that_is_linked(events, results, kb_ids, status_code):
    from app.services.slack_integration import start_slack_connect

    db = _db(results)
    with pytest.raises(HTTPException) as exc:
        await start_slack_connect(db, org_id=42, org_slug="example", user_id="u", kb_ids=kb_ids)

    assert exc.value.status_code == status_code
    assert _deleted_tables(db) == []
    events.assert_not_called()


@pytest.mark.asyncio
async def test_connect_replaces_an_orphan_key_that_no_workspace_holds(events, monkeypatch):
    from app.services import slack_integration

    monkeypatch.setattr(slack_integration.settings, "internal_secret", _SECRET)
    db = _db([FakeResult([_slack_key()]), FakeResult([]), FakeResult([_ORG_KB]), FakeResult()])

    _url, token = await slack_integration.start_slack_connect(
        db, org_id=42, org_slug="example", user_id="u", kb_ids=[10]
    )

    # Left in place, the orphan would make link-oauth's mint answer 409.
    assert _deleted_tables(db) == ["partner_api_key_kb_access", "partner_api_keys"]
    db.commit.assert_awaited_once()
    assert slack_integration.decode_link_token(token)["kb_ids"] == [10]


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int | None]] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.values:
            return None
        self.values[key] = (value, ex)
        return True


@pytest.fixture
def token_store(internal, monkeypatch) -> _FakeRedis:
    store = _FakeRedis()
    monkeypatch.setattr(internal, "get_redis_pool", AsyncMock(return_value=store))
    return store


def _link_token(secret: str = _SECRET, **overrides) -> str:
    import time

    import jwt

    now = int(time.time())
    claims = {
        "typ": "slack_link",
        "org_id": 42,
        "user_id": "u",
        "kb_ids": [10, 11],
        "return_url": "https://example.getklai.com/admin/slack",
        "jti": "jti-1",
        "iat": now,
        "exp": now + 900,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def _oauth_body(token: str):
    from app.api.internal_slack import LinkOAuthRequest

    return LinkOAuthRequest(
        token=token, team_id="T0EXAMPLE1", team_name="Example Workspace", slack_user_id="U0EXAMPLE1"
    )


def _portal_user(**overrides) -> SimpleNamespace:
    values = {"zitadel_user_id": "u", "org_id": 42, "role": "admin", "seat_type": "knowledge", "status": "active"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _oauth_db(kbs=(_ORG_KB, _ORG_KB_2), existing_key=None, unlocked=("slack",), user=None) -> AsyncMock:
    org = FakeOrg(id=42, name="Example Org", slug="example", plan="core", platform_unlocked_features=list(unlocked))
    user = _portal_user() if user is None else user
    return _db(
        [
            FakeResult([org]),
            FakeResult([(org, user)] if user else []),  # resolve_user_permissions
            FakeResult(),  # org row lock
            FakeResult([existing_key] if existing_key else []),
            FakeResult(list(kbs)),
        ]
    )


@pytest.mark.asyncio
async def test_link_oauth_mints_the_key_records_the_workspace_and_returns_the_plaintext_once(internal, token_store):
    import hashlib

    from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
    from app.models.slack import PortalSlackInstallation

    db = _oauth_db()

    result = await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    added = [call.args[0] for call in db.add.call_args_list]
    key = next(row for row in added if isinstance(row, PartnerAPIKey))
    assert key.created_by == "u" and key.org_id == 42
    assert (key.name, key.rate_limit_rpm, key.key_prefix) == ("Slack", 600, result.api_key[:12])
    assert key.permissions == {
        "slack": True,
        "chat": True,
        "feedback": True,
        "knowledge_append": False,
        "general_chat": False,
    }
    assert key.key_hash == hashlib.sha256(result.api_key.encode()).hexdigest()
    access = [row for row in added if isinstance(row, PartnerApiKeyKbAccess)]
    assert sorted((row.kb_id, row.access_level) for row in access) == [(10, "read"), (11, "read")]
    installation = next(row for row in added if isinstance(row, PortalSlackInstallation))
    assert (installation.org_id, installation.team_id, installation.partner_api_key_id) == (42, "T0EXAMPLE1", key.id)
    assert result.api_key.startswith("pk_live_")
    assert result.model_dump(exclude={"api_key"}) == {
        "org_id": 42,
        "org_name": "Example Org",
        "key_id": key.id,
        "kb_ids": [10, 11],
        "return_url": "https://example.getklai.com/admin/slack",
    }
    internal.set_tenant.assert_awaited_once_with(db, 42)
    db.commit.assert_awaited_once()
    assert [call.args[0] for call in internal.emit_event.call_args_list] == ["slack.key_created", "slack.linked"]
    assert token_store.values == {"slack_link_jti:jti-1": ("1", 900)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        _link_token(iat=1, exp=2),
        _link_token(secret="another-secret-of-at-least-32-bytes"),
        _link_token(typ="widget_session"),
    ],
    ids=["expired", "tampered signature", "other token type"],
)
async def test_link_oauth_rejects_an_invalid_token_with_one_generic_401(internal, token_store, token):
    db = _oauth_db()

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace_oauth(body=_oauth_body(token), request=_request(), db=db)

    assert (exc.value.status_code, exc.value.detail) == (401, "Invalid link token")
    db.execute.assert_not_awaited()
    assert token_store.values == {}


@pytest.mark.asyncio
async def test_link_oauth_refuses_a_replayed_token(internal, token_store):
    token = _link_token()
    await internal.link_slack_workspace_oauth(body=_oauth_body(token), request=_request(), db=_oauth_db())
    replay_db = _oauth_db()

    response = await internal.link_slack_workspace_oauth(body=_oauth_body(token), request=_request(), db=replay_db)

    assert response.status_code == 409
    assert response.body == b'{"code":"token_used"}'
    replay_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_oauth_fails_closed_without_the_token_store(internal, monkeypatch):
    monkeypatch.setattr(internal, "get_redis_pool", AsyncMock(return_value=None))
    db = _oauth_db()

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    assert exc.value.status_code == 503
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_link_oauth_rejects_a_kb_deleted_since_the_link_started(internal, token_store):
    db = _oauth_db(kbs=[_ORG_KB])

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    assert exc.value.status_code == 400
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_oauth_is_forbidden_once_the_slack_unlock_is_withdrawn(internal, token_store):
    db = _oauth_db(unlocked=("partner_api",))

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    assert exc.value.status_code == 403
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [_portal_user(status="suspended"), _portal_user(status="offboarded"), _portal_user(role="company"), False],
    ids=["suspended", "offboarded", "demoted to company", "no longer in the org"],
)
async def test_link_oauth_mints_nothing_for_a_user_who_is_no_longer_an_active_admin(internal, token_store, user):
    db = _oauth_db(user=user)

    response = await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    assert response.status_code == 403
    assert response.body == b'{"code":"not_admin"}'
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    assert "slack_link_jti:jti-1" in token_store.values  # the token cannot be retried


@pytest.mark.asyncio
async def test_link_oauth_refuses_an_org_that_already_has_a_slack_key(internal, token_store):
    db = _oauth_db(existing_key=_slack_key())

    response = await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    assert response.status_code == 409
    assert response.body == b'{"code":"org_already_linked"}'
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_link_oauth_mints_no_key_for_a_workspace_linked_to_another_org(internal, token_store):
    db = _oauth_db()
    # Key and KB rows flush; the installation row trips UNIQUE(team_id).
    db.flush.side_effect = [
        None,
        None,
        IntegrityError(
            "INSERT",
            {},
            Exception('duplicate key value violates unique constraint "uq_portal_slack_installations_team_id"'),
        ),
    ]

    response = await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(), db=db)

    assert response.body == b'{"code":"team_already_linked"}'
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    internal.emit_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("secret", "token", "status_code"), [("", _SECRET, 503), (_SECRET, "wrong", 401), (_SECRET, None, 401)]
)
async def test_link_oauth_fails_closed_without_the_internal_secret(
    internal, token_store, monkeypatch, secret, token, status_code
):
    from app.api import internal as internal_mod

    monkeypatch.setattr(internal_mod.settings, "internal_secret", secret)
    db = _oauth_db()

    with pytest.raises(HTTPException) as exc:
        await internal.link_slack_workspace_oauth(body=_oauth_body(_link_token()), request=_request(token), db=db)

    assert exc.value.status_code == status_code
    db.execute.assert_not_awaited()
    assert token_store.values == {}
