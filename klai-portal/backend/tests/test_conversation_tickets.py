"""SPEC-KNOWLEDGE-ESCALATION-001 §4.1/§4.3/§4.4 — tickets from a reviewed conversation.

Same DB-mocked style as tests/test_app_activity.py: ``TicketSession`` answers
the router's SQL by marker, but keeps the ``conversation_tickets`` rows as
state so the ON CONFLICT reuse rules (created -> 409, failed -> retry) are
exercised end to end. HubSpot is served by respx on ``api.hubapi.com``, so the
real client code runs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.services.secrets import portal_secrets
from tests.test_app_activity import CALLER_DISPLAY_NAME, CALLER_PORTAL_USER_ID, WIDGET_UUID, _call, _perms, _Rows

API = "https://api.hubapi.com"
KEY = "pat-eu1-00000000-synthetic"
T0 = dt.datetime(2026, 9, 14, 9, 12, tzinfo=dt.UTC)
VISITOR_EMAIL = "sam.jansen@example.com"
TARGETS = [{"key": "sales", "label": "Sales", "pipeline_id": "0", "stage_id": "1"}]
BASE = "/api/app/activity/conversations/255"


def _conversation(*, email: str | None = VISITOR_EMAIL, is_test: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=255,
        widget_id=uuid.UUID(WIDGET_UUID),
        widget_name="Fictief help",
        started_at=T0,
        language_detected="nl",
        visitor_name="Sam Jansen",
        visitor_email=email,
        is_test=is_test,
        is_preview=False,
    )


def _settings(*, ui_domain: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        targets=TARGETS,
        service_key_encrypted=portal_secrets.encrypt(KEY),
        hubspot_portal_id=12345,
        hubspot_ui_domain=ui_domain,
    )


class TicketSession:
    """Answers the ticket paths of the activity router by SQL marker."""

    def __init__(self, *, conversation: Any | None = None, settings: Any | None = None) -> None:
        self.conversation = conversation
        self.settings = settings
        self.tickets: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.messages = [
            SimpleNamespace(
                id=1,
                role="user",
                content="Wat kost een extra nummer?",
                sources=None,
                created_at=T0,
                sequence=1,
                rating=None,
                answer_signals=None,
            ),
            SimpleNamespace(
                id=2,
                role="assistant",
                content="Dat weet ik niet.",
                created_at=T0 + dt.timedelta(minutes=1),
                sources=[{"title": "Prijzen", "url": "https://help.example.com/prijzen"}],
                sequence=2,
                rating=None,
                answer_signals=None,
            ),
        ]

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = " ".join(str(statement).split())
        params = params or {}
        self.calls.append((sql, params))
        if "c.id = :conversation_id" in sql:
            return _Rows([self.conversation] if self.conversation else [])
        if "FROM widget_ticket_settings" in sql:
            return _Rows([self.settings] if self.settings else [])
        if sql.startswith("INSERT INTO conversation_tickets"):
            return self._insert(params)
        if sql.startswith("UPDATE conversation_tickets"):
            row = next(r for r in self.tickets.values() if r["id"] == params["ticket_id"])
            row.update({k: v for k, v in params.items() if k != "ticket_id" and k != "org_id"})
            return _Rows([])
        if sql.startswith("SELECT status FROM conversation_tickets"):
            row = self.tickets.get(params["target_key"])
            return _Rows([SimpleNamespace(status=row["status"])] if row else [])
        if "FROM conversation_tickets" in sql:
            return _Rows(
                [
                    SimpleNamespace(**{**row, "created_by_name": CALLER_DISPLAY_NAME, "conversation_id": 255})
                    for row in self.tickets.values()
                ]
            )
        if "FROM portal_orgs" in sql:
            return _Rows([SimpleNamespace(widget_messages_retention_days=None)])
        if "FROM portal_users" in sql:
            return _Rows([SimpleNamespace(id=CALLER_PORTAL_USER_ID, display_name=CALLER_DISPLAY_NAME)])
        if "FROM widget_messages" in sql:
            return _Rows(self.messages)
        if "FROM answer_reviews" in sql:
            return _Rows(
                [
                    SimpleNamespace(
                        message_id=2,
                        turn_sequence=2,
                        verdict="incomplete",
                        cause="knowledge_missing",
                        note="Prijsvraag, doorzetten naar Sales.",
                        kb_slug=None,
                        reviewer_name=CALLER_DISPLAY_NAME,
                        reviewed_at=T0,
                    )
                ]
            )
        if "FROM conversation_quality_judgments" in sql:
            return _Rows([])
        raise AssertionError(f"TicketSession got unexpected SQL:\n{sql}")

    def _insert(self, params: dict[str, Any]) -> _Rows:
        key = params["target_key"]
        row = self.tickets.get(key)
        if row is not None and row["status"] != "failed":
            return _Rows([])
        if row is None:
            row = {"id": len(self.tickets) + 1, "target_key": key}
            self.tickets[key] = row
        row.update(
            target_label=params["target_label"],
            status="pending",
            error=None,
            ticket_url=None,
            contact_status=None,
            company_status=None,
            created_at=T0,
        )
        return _Rows([SimpleNamespace(id=row["id"], created_at=T0)])

    async def commit(self) -> None:
        self.commits += 1


def _mock_hubspot(*, contact: bool = True, company: str | None = None) -> dict[str, respx.Route]:
    """Every HubSpot call of the full flow (SPEC §4.4), with the answers a key
    carrying all scopes would get. A test narrows one route to a 403 to play a
    key that lacks that scope."""
    props = {"email": VISITOR_EMAIL, "firstname": "Sam", "lastname": "Jansen", "lifecyclestage": "customer"}
    results = [{"id": "22", "properties": props}] if contact else []
    companies = (
        [{"toObjectId": int(company), "associationTypes": [{"category": "HUBSPOT_DEFINED", "typeId": 1}]}]
        if company
        else []
    )
    return {
        "search": respx.post(f"{API}/crm/v3/objects/contacts/search").mock(
            return_value=httpx.Response(200, json={"total": len(results), "results": results})
        ),
        "contact_create": respx.post(f"{API}/crm/v3/objects/contacts").mock(
            return_value=httpx.Response(201, json={"id": "41"})
        ),
        "companies": respx.get(url__regex=rf"{API}/crm/v4/objects/contacts/\d+/associations/companies").mock(
            return_value=httpx.Response(200, json={"results": companies})
        ),
        "company": respx.get(url__regex=rf"{API}/crm/v3/objects/companies/\d+").mock(
            return_value=httpx.Response(200, json={"id": company, "properties": {"name": "Fictief BV"}})
        ),
        "ticket": respx.post(f"{API}/crm/v3/objects/tickets").mock(
            return_value=httpx.Response(201, json={"id": "9001"})
        ),
    }


def _associations(route: respx.Route) -> list[tuple[str, int]]:
    body = json.loads(route.calls.last.request.content)
    return [(a["to"]["id"], a["types"][0]["associationTypeId"]) for a in body.get("associations", [])]


# ---------------------------------------------------------------------------
# Detail: ticket block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversation", "settings", "available"),
    [
        (_conversation(), _settings(), True),
        (_conversation(email=None), _settings(), False),
        (_conversation(email="  "), _settings(), False),
        (_conversation(is_test=True), _settings(), False),
        (_conversation(), None, False),
    ],
)
async def test_detail_ticket_available_only_with_email_settings_and_not_test(
    conversation: Any, settings: Any, available: bool
) -> None:
    db = TicketSession(conversation=conversation, settings=settings)
    response = await _call(db, _perms("kb_manager"), "get", BASE)

    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert ticket["available"] is available
    assert ticket["targets"] == ([{"key": "sales", "label": "Sales"}] if settings else [])
    assert ticket["tickets"] == []
    assert VISITOR_EMAIL not in response.text


# ---------------------------------------------------------------------------
# POST .../tickets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_post_ticket_for_an_existing_contact_links_the_contact() -> None:
    routes = _mock_hubspot()
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 201, response.text
    ticket_url = "https://app.hubspot.com/contacts/12345/record/0-5/9001"
    assert response.json() == {
        "target_key": "sales",
        "target_label": "Sales",
        "status": "created",
        "ticket_url": ticket_url,
        "contact_status": "existing",
        "company_status": "none",
        "error": None,
        "created_by_name": CALLER_DISPLAY_NAME,
        "created_at": "2026-09-14T09:12:00Z",
    }
    assert db.tickets["sales"]["status"] == "created"
    assert db.tickets["sales"]["hubspot_ticket_id"] == "9001"
    body = json.loads(routes["ticket"].calls.last.request.content)
    assert _associations(routes["ticket"]) == [("22", 16)]
    assert not routes["contact_create"].called
    assert body["properties"]["subject"] == "Webchat: Wat kost een extra nummer?"
    content = body["properties"]["content"]
    assert not content.startswith("Niet gevonden in HubSpot")
    assert "Prijsvraag, doorzetten naar Sales." in content
    assert VISITOR_EMAIL in content
    assert "https://voys.getklai.com/app/knowledge/activity/255" in content
    # The pending row was committed before HubSpot was called.
    assert db.commits >= 2
    assert VISITOR_EMAIL not in response.text


@pytest.mark.asyncio
@respx.mock
async def test_post_ticket_for_an_unknown_visitor_creates_and_links_the_contact() -> None:
    routes = _mock_hubspot(contact=False)
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 201, response.text
    assert (response.json()["contact_status"], response.json()["company_status"]) == ("created", "none")
    assert db.tickets["sales"]["hubspot_contact_id"] == "41"
    assert json.loads(routes["contact_create"].calls.last.request.content)["properties"] == {
        "email": VISITOR_EMAIL,
        "firstname": "Sam",
        "lastname": "Jansen",
    }
    # A contact created a moment ago has no company yet (SPEC §4.4 step 5).
    assert not routes["companies"].called
    assert _associations(routes["ticket"]) == [("41", 16)]
    content = json.loads(routes["ticket"].calls.last.request.content)["properties"]["content"]
    assert not content.startswith("Niet gevonden in HubSpot")


@pytest.mark.asyncio
@respx.mock
async def test_post_ticket_when_the_key_may_not_create_contacts_files_it_unlinked() -> None:
    """Without crm.objects.contacts.write the ticket still goes in, without a
    contact, naming the visitor on its first line, and the row says why."""
    routes = _mock_hubspot(contact=False)
    routes["contact_create"].mock(return_value=httpx.Response(403, json={"category": "MISSING_SCOPES"}))
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 201, response.text
    assert response.json()["contact_status"] == "create_forbidden"
    assert response.json()["status"] == "created"
    assert db.tickets["sales"]["contact_status"] == "create_forbidden"
    assert db.tickets["sales"]["hubspot_contact_id"] is None
    body = json.loads(routes["ticket"].calls.last.request.content)
    assert "associations" not in body
    first_line = body["properties"]["content"].splitlines()[0]
    assert first_line == f"Niet gevonden in HubSpot: Sam Jansen · {VISITOR_EMAIL}"


@pytest.mark.asyncio
@respx.mock
async def test_post_ticket_contact_exists_conflict_uses_the_existing_contact() -> None:
    """The search index lags behind HubSpot's store, so create can answer 409
    for a contact the search missed; that contact is the ticket's contact."""
    routes = _mock_hubspot(contact=False, company="77")
    routes["contact_create"].mock(
        return_value=httpx.Response(
            409, json={"status": "error", "message": "Contact already exists. Existing ID: 58", "category": "CONFLICT"}
        )
    )
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 201, response.text
    assert (response.json()["contact_status"], response.json()["company_status"]) == ("existing", "linked")
    assert db.tickets["sales"]["hubspot_contact_id"] == "58"
    assert _associations(routes["ticket"]) == [("58", 16), ("77", 26)]


@pytest.mark.asyncio
@respx.mock
async def test_post_ticket_for_an_existing_contact_with_a_company_links_both() -> None:
    routes = _mock_hubspot(company="77")
    db = TicketSession(conversation=_conversation(), settings=_settings(ui_domain="app-eu1.hubspot.com"))

    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 201, response.text
    assert (response.json()["contact_status"], response.json()["company_status"]) == ("existing", "linked")
    assert response.json()["ticket_url"] == "https://app-eu1.hubspot.com/contacts/12345/record/0-5/9001"
    assert db.tickets["sales"]["company_status"] == "linked"
    assert _associations(routes["ticket"]) == [("22", 16), ("77", 26)]


@pytest.mark.asyncio
@respx.mock
async def test_post_ticket_refused_with_the_company_is_retried_once_without_it() -> None:
    """HubSpot may refuse the company association for a key without
    crm.objects.companies.read; the ticket then goes in with the contact only
    and the row records that the company link was forbidden."""
    routes = _mock_hubspot(company="77")
    routes["ticket"].mock(
        side_effect=[httpx.Response(403, json={"category": "MISSING_SCOPES"}), httpx.Response(201, json={"id": "9003"})]
    )
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 201, response.text
    assert response.json()["company_status"] == "forbidden"
    assert db.tickets["sales"]["hubspot_ticket_id"] == "9003"
    assert routes["ticket"].call_count == 2
    assert _associations(routes["ticket"]) == [("22", 16)]


@pytest.mark.asyncio
@respx.mock
async def test_second_post_for_the_same_target_is_409_ticket_exists() -> None:
    routes = _mock_hubspot()
    db = TicketSession(conversation=_conversation(), settings=_settings())
    first = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})
    assert first.status_code == 201

    second = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert second.status_code == 409
    assert second.json()["detail"] == "ticket_exists"
    assert routes["ticket"].call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_hubspot_failure_marks_the_row_failed_and_a_retry_reuses_it() -> None:
    routes = _mock_hubspot()
    routes["ticket"].mock(return_value=httpx.Response(500, json={}))
    db = TicketSession(conversation=_conversation(), settings=_settings())

    failed = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert failed.status_code == 502
    assert "500" in failed.json()["detail"]
    assert db.tickets["sales"]["status"] == "failed"
    assert db.tickets["sales"]["error"] == failed.json()["detail"]

    routes["ticket"].mock(return_value=httpx.Response(201, json={"id": "9002"}))
    retried = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert retried.status_code == 201, retried.text
    assert list(db.tickets) == ["sales"]
    assert db.tickets["sales"]["id"] == 1
    assert db.tickets["sales"]["status"] == "created"
    assert db.tickets["sales"]["error"] is None


@pytest.mark.asyncio
async def test_post_ticket_409_ticket_unavailable_without_email() -> None:
    db = TicketSession(conversation=_conversation(email=None), settings=_settings())
    response = await _call(db, _perms("kb_manager"), "post", f"{BASE}/tickets", json={"target_key": "sales"})

    assert response.status_code == 409
    assert response.json()["detail"] == "ticket_unavailable"


# ---------------------------------------------------------------------------
# Tenant scoping and preview privacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [("get", "/ticket-preview"), ("post", "/tickets")])
async def test_conversation_of_another_org_is_404(method: str, path: str) -> None:
    """The org-scoped conversation read finds nothing for a foreign id, so
    neither route confirms the conversation exists."""
    db = TicketSession(conversation=None, settings=_settings())
    response = await _call(db, _perms("admin"), method, f"{BASE}{path}", json={"target_key": "sales"})

    assert response.status_code == 404
    conversation_sql = [sql for sql, _ in db.calls if "c.id = :conversation_id" in sql]
    assert conversation_sql and "c.org_id = :org_id" in conversation_sql[0]
    assert db.calls[0][1]["org_id"] == 101


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(("role", "shows_names"), [("kb_manager", False), ("admin", True)])
async def test_preview_shows_contact_and_company_name_only_to_admins(role: str, shows_names: bool) -> None:
    _mock_hubspot(company="77")
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms(role), "get", f"{BASE}/ticket-preview")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "contact": "existing",
        "lifecycle_stage": "customer",
        "contact_name": "Sam Jansen" if shows_names else None,
        "company_name": "Fictief BV" if shows_names else None,
    }
    assert VISITOR_EMAIL not in response.text


@pytest.mark.asyncio
@respx.mock
async def test_preview_for_an_unknown_visitor_says_new_and_creates_nothing() -> None:
    routes = _mock_hubspot(contact=False)
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("admin"), "get", f"{BASE}/ticket-preview")

    assert response.status_code == 200, response.text
    assert response.json() == {"contact": "new", "lifecycle_stage": None, "contact_name": None, "company_name": None}
    assert not routes["contact_create"].called


@pytest.mark.asyncio
@respx.mock
async def test_preview_company_name_is_null_when_the_key_may_not_read_companies() -> None:
    routes = _mock_hubspot(company="77")
    routes["company"].mock(return_value=httpx.Response(403, json={}))
    db = TicketSession(conversation=_conversation(), settings=_settings())

    response = await _call(db, _perms("admin"), "get", f"{BASE}/ticket-preview")

    assert response.status_code == 200, response.text
    assert response.json()["company_name"] is None
    assert response.json()["contact_name"] == "Sam Jansen"


# ---------------------------------------------------------------------------
# Storage shape (post-deploy SQL is the source of truth)
# ---------------------------------------------------------------------------

_SQL_PATH = next(
    (Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("post_deploy_*_conversation_tickets_rls.sql")
)


def _sql_columns(table: str) -> set[str]:
    sql = _SQL_PATH.read_text(encoding="utf-8")
    block = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.DOTALL)
    assert block, f"{table} CREATE TABLE block missing"
    names = set()
    for raw in block.group(1).splitlines():
        line = raw.strip()
        if line and not line.startswith(("--", "CHECK", "CONSTRAINT", "UNIQUE", "PRIMARY")):
            names.add(line.split()[0].rstrip(","))
    return names


@pytest.mark.parametrize("table", ["widget_ticket_settings", "conversation_tickets"])
def test_models_match_the_post_deploy_sql_and_rls_is_cat_d(table: str) -> None:
    from app.core.rls_guard import RLS_DML_TABLES
    from app.models.base import Base

    assert {c.name for c in Base.metadata.tables[table].columns} == _sql_columns(table)
    sql = _SQL_PATH.read_text(encoding="utf-8")
    assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
    assert f"DROP POLICY IF EXISTS tenant_isolation ON {table}" in sql
    assert f"CREATE POLICY tenant_isolation ON {table}" in sql
    assert table in RLS_DML_TABLES
