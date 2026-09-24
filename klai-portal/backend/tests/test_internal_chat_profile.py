"""Internal-chat entry on POST /partner/v1/chat/completions (one-chat-pipeline slice 1).

A partner key with the ``internal_chat`` permission is LibreChat's key for one
org. The OpenAI ``user`` field carries the LibreChat Mongo ObjectId; it must
resolve to a portal user of THAT org or the request is refused with 403. Every
other key keeps its widget/partner profile and never triggers identity
resolution.

The fake database below answers ORM selects by evaluating their equality
filters against in-memory rows, so a missing ``org_id`` filter shows up as a
cross-org match instead of being hidden by a mock that returns a fixed value.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api.partner_dependencies import PartnerAuthContext
from app.models.portal import PortalOrg, PortalUser
from app.services.chat_profile import ChatProfile

ORG_A = 1
ORG_B = 2
OID_A = str(ObjectId())
OID_B = str(ObjectId())
OID_UNKNOWN = str(ObjectId())

_TITLE_PROMPT = (
    "Please generate a concise, 5-word-or-less title for the conversation, using its same language, "
    "with no punctuation."
)


class _FakeDB:
    def __init__(self, rows: list[Any], events: list[str]) -> None:
        self.rows = rows
        self.events = events
        self.commit = AsyncMock()

    async def execute(self, stmt: Any) -> MagicMock:
        entity = stmt.column_descriptions[0]["entity"]
        where = stmt.whereclause
        clauses = list(getattr(where, "clauses", [where]))
        matches = [
            row
            for row in self.rows
            if isinstance(row, entity) and all(getattr(row, c.left.key) == c.right.value for c in clauses)
        ]
        assert len(matches) <= 1
        self.events.append(f"select:{entity.__tablename__}")
        result = MagicMock()
        result.scalar_one_or_none.return_value = matches[0] if matches else None
        return result


class _FakeMongo:
    """Motor stand-in: {database name: {ObjectId: user document}}."""

    databases: ClassVar[dict[str, dict[ObjectId, dict]]] = {}

    def __init__(self, _uri: str) -> None:
        pass

    def __getitem__(self, db_name: str) -> dict:
        docs = self.databases.get(db_name, {})

        class _Users:
            async def find_one(self, query: dict) -> dict | None:
                return docs.get(query["_id"])

        return {"users": _Users()}

    def close(self) -> None:
        pass


def _user(org_id: int, sub: str, **overrides: Any) -> PortalUser:
    values: dict[str, Any] = {
        "org_id": org_id,
        "zitadel_user_id": sub,
        "role": "admin",
        "status": "active",
        "librechat_user_id": None,
        "kb_retrieval_enabled": True,
        "kb_personal_enabled": False,
        "kb_slugs_filter": None,
        "kb_narrow": False,
        "kb_pref_version": 0,
    }
    values.update(overrides)
    return PortalUser(**values)


@pytest.fixture
def world(monkeypatch):
    """Two orgs, each with its own LibreChat database and one employee."""
    import app.services.internal_chat_identity as identity

    events: list[str] = []

    async def fake_set_tenant(_db, org_id: int) -> None:
        events.append(f"tenant:{org_id}")

    monkeypatch.setattr(identity, "set_tenant", fake_set_tenant)
    monkeypatch.setattr(identity, "AsyncIOMotorClient", _FakeMongo)
    monkeypatch.setattr(identity.settings, "librechat_mongo_root_uri", "mongodb://fake")
    monkeypatch.setattr(identity, "get_effective_products", AsyncMock(return_value=["chat", "knowledge"]))
    _FakeMongo.databases = {
        "librechat-a": {ObjectId(OID_A): {"openidId": "sub-a"}},
        "librechat-b": {ObjectId(OID_B): {"openidId": "sub-b"}},
    }
    rows: list[Any] = [
        PortalOrg(id=ORG_A, zitadel_org_id="zorg-a", librechat_container="librechat-a", telemetry_level="shadow"),
        PortalOrg(id=ORG_B, zitadel_org_id="zorg-b", librechat_container="librechat-b", telemetry_level="shadow"),
        _user(ORG_A, "sub-a"),
        _user(ORG_B, "sub-b"),
    ]
    return rows, events


def _auth(permissions: dict | None = None, *, key_id: str = "key-1") -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id=key_id,
        org_id=ORG_A,
        zitadel_org_id="zorg-a",
        permissions=permissions if permissions is not None else {"chat": True, "internal_chat": True},
        kb_access={},
        rate_limit_rpm=60,
    )


def _request(body: dict) -> MagicMock:
    payload = json.dumps(body).encode()

    async def stream():
        yield payload

    request = MagicMock()
    request.headers = {"content-length": str(len(payload))}
    request.stream = stream
    return request


async def _resolve(world, librechat_user_id: object, auth: PartnerAuthContext | None = None) -> ChatProfile:
    from app.services.chat_profile import resolve_chat_profile

    rows, events = world
    return await resolve_chat_profile(_FakeDB(rows, events), auth or _auth(), librechat_user_id)


def _employee(world) -> PortalUser:
    rows, _ = world
    return next(r for r in rows if isinstance(r, PortalUser) and r.zitadel_user_id == "sub-a")


# --- identity: fail-closed and bound to the key's org -----------------------


@pytest.mark.asyncio
async def test_objectid_already_mapped_in_another_org_is_forbidden(world):
    rows, _ = world
    other = next(r for r in rows if isinstance(r, PortalUser) and r.zitadel_user_id == "sub-b")
    other.librechat_user_id = OID_B

    with pytest.raises(HTTPException) as exc:
        await _resolve(world, OID_B)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_objectid_from_another_orgs_librechat_is_forbidden(world):
    with pytest.raises(HTTPException) as exc:
        await _resolve(world, OID_B)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_sub_that_only_exists_in_another_org_is_forbidden(world):
    _FakeMongo.databases["librechat-a"][ObjectId(OID_UNKNOWN)] = {"openidId": "sub-b"}

    with pytest.raises(HTTPException) as exc:
        await _resolve(world, OID_UNKNOWN)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unknown_objectid_is_forbidden_through_the_endpoint(world):
    import app.api.partner as partner

    rows, events = world
    with pytest.raises(HTTPException) as exc:
        await partner.canonical_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}], "user": OID_UNKNOWN}),
            auth=_auth(),
            db=_FakeDB(rows, events),
        )

    assert exc.value.status_code == 403


@pytest.mark.parametrize("user_field", [None, "", 42])
@pytest.mark.asyncio
async def test_internal_key_without_a_usable_user_field_is_forbidden(world, user_field):
    with pytest.raises(HTTPException) as exc:
        await _resolve(world, user_field)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_suspended_employee_is_forbidden(world):
    _employee(world).status = "suspended"

    with pytest.raises(HTTPException) as exc:
        await _resolve(world, OID_A)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_tenant_is_set_before_any_portal_users_select(world):
    _, events = world

    await _resolve(world, OID_A)

    assert events.index(f"tenant:{ORG_A}") < events.index("select:portal_users")


@pytest.mark.asyncio
async def test_first_resolution_caches_the_librechat_mapping(world):
    profile = await _resolve(world, OID_A)

    assert profile.user_id == "sub-a"
    assert _employee(world).librechat_user_id == OID_A


# --- surfaces that must not change -----------------------------------------


@pytest.mark.asyncio
async def test_partner_key_sending_user_stays_partner_without_identity_resolution(world, monkeypatch):
    import app.services.chat_profile as chat_profile

    monkeypatch.setattr(chat_profile, "resolve_librechat_user", AsyncMock(side_effect=AssertionError))

    profile = await _resolve(world, OID_A, _auth({"chat": True, "general_chat": True}))

    assert profile == ChatProfile(surface="partner")


@pytest.mark.asyncio
async def test_widget_token_is_widget_profile(world):
    profile = await _resolve(world, OID_A, _auth({"chat": True}, key_id="wgt_abc"))

    assert profile == ChatProfile(surface="widget")


# --- internal profile from the employee's settings -------------------------


@pytest.mark.asyncio
async def test_strict_employee_gets_strict_profile_with_user_id(world):
    _employee(world).kb_narrow = True

    profile = await _resolve(world, OID_A)

    assert profile == ChatProfile(
        surface="internal", kb_mode="strict", kb_scope="org", user_id="sub-a", stream_live=False
    )


@pytest.mark.asyncio
async def test_open_employee_streams_live(world):
    profile = await _resolve(world, OID_A)

    assert profile.kb_mode == "open"
    assert profile.stream_live is True


@pytest.mark.asyncio
async def test_retrieval_disabled_is_general(world):
    _employee(world).kb_retrieval_enabled = False

    profile = await _resolve(world, OID_A)

    assert profile.kb_mode == "general"
    assert profile.user_id == "sub-a"
    assert profile.stream_live is True


@pytest.mark.asyncio
async def test_no_knowledge_entitlement_is_general(world, monkeypatch):
    import app.services.internal_chat_identity as identity

    _employee(world).role = "member"
    monkeypatch.setattr(identity, "get_effective_products", AsyncMock(return_value=["chat"]))

    profile = await _resolve(world, OID_A)

    assert profile.kb_mode == "general"


@pytest.mark.asyncio
async def test_org_admin_has_knowledge_access_without_the_product(world, monkeypatch):
    import app.services.internal_chat_identity as identity

    monkeypatch.setattr(identity, "get_effective_products", AsyncMock(return_value=["chat"]))

    profile = await _resolve(world, OID_A)

    assert profile.kb_mode == "open"


@pytest.mark.asyncio
async def test_strict_employee_with_nothing_to_search_stays_strict_with_empty_scope(world):
    employee = _employee(world)
    employee.kb_narrow = True
    employee.kb_retrieval_enabled = False

    profile = await _resolve(world, OID_A)

    assert profile.kb_mode == "strict"
    assert profile.kb_slugs == ()


@pytest.mark.asyncio
async def test_personal_kb_enabled_searches_both_with_user_id(world):
    employee = _employee(world)
    employee.kb_personal_enabled = True
    employee.kb_slugs_filter = ["handbook"]

    profile = await _resolve(world, OID_A)

    assert profile.kb_scope == "both"
    assert profile.kb_slugs == ("handbook",)
    assert profile.user_id == "sub-a"


@pytest.mark.asyncio
async def test_personal_kb_only_when_every_org_kb_is_off(world):
    employee = _employee(world)
    employee.kb_personal_enabled = True
    employee.kb_slugs_filter = []

    profile = await _resolve(world, OID_A)

    assert profile.kb_scope == "personal"
    assert profile.kb_slugs is None


# --- routing on the profile, not the body ----------------------------------


@pytest.mark.asyncio
async def test_internal_title_prompt_goes_to_passthrough_without_retrieval(world, monkeypatch):
    import app.api.partner as partner

    rows, events = world
    passthrough = AsyncMock(return_value={"choices": [{"message": {"content": "Title"}}]})
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", passthrough)
    monkeypatch.setattr(partner, "_enforce_openai_compatible_usage_limits", AsyncMock())
    monkeypatch.setattr(partner, "chat_completions", AsyncMock(side_effect=AssertionError))

    result = await partner.canonical_chat_completions(
        http_request=_request(
            {
                "model": "klai-primary",
                "stream": False,
                "user": OID_A,
                "messages": [{"role": "user", "content": _TITLE_PROMPT}],
            }
        ),
        auth=_auth(),
        db=_FakeDB(rows, events),
    )

    assert result == {"choices": [{"message": {"content": "Title"}}]}
    passthrough.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_turn_with_tools_goes_to_knowledge_path_with_profile(world, monkeypatch):
    import app.api.partner as partner

    rows, events = world
    knowledge_flow = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(partner, "chat_completions", knowledge_flow)

    await partner.canonical_chat_completions(
        http_request=_request(
            {
                "model": "klai-primary",
                "user": OID_A,
                "messages": [{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
                "tools": [{"type": "function", "function": {"name": "search"}}],
                "tool_choice": "auto",
            }
        ),
        auth=_auth({"chat": True, "general_chat": True, "internal_chat": True}),
        db=_FakeDB(rows, events),
    )

    profile = knowledge_flow.await_args.kwargs["profile"]
    assert profile.surface == "internal"
    assert profile.user_id == "sub-a"


# --- the existing internal endpoint shares the resolver ---------------------


@pytest.mark.asyncio
async def test_internal_knowledge_feature_resolves_through_the_shared_service(world, monkeypatch):
    import app.api.internal as internal

    rows, events = world
    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    shared = AsyncMock(return_value=_employee(world))
    monkeypatch.setattr(internal, "resolve_librechat_user", shared)

    response = await internal.get_knowledge_feature(
        librechat_user_id=OID_A, org_id="zorg-a", request=MagicMock(), db=_FakeDB(rows, events)
    )

    assert response.enabled is True
    assert response.zitadel_user_id == "sub-a"
    assert shared.await_args.args[1].id == ORG_A


@pytest.mark.asyncio
async def test_internal_knowledge_feature_is_disabled_for_another_orgs_user(world, monkeypatch):
    import app.api.internal as internal

    rows, events = world
    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())

    response = await internal.get_knowledge_feature(
        librechat_user_id=OID_B, org_id="zorg-a", request=MagicMock(), db=_FakeDB(rows, events)
    )

    assert response.enabled is False
    assert response.zitadel_user_id is None
