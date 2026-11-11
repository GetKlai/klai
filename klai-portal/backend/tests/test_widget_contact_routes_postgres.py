"""Real-PostgreSQL proof that the shipped post-deploy SQL adds one route and leaves the old settings in place.

The file ``post_deploy_widget_contact_routes.sql`` runs as it ships (aimed at a
throwaway schema), twice, as every deploy applies every post-deploy file. It
only adds: the previous release must still find its keys after a rollback. What
it leaves must read back as routes (``routes_from_config``) without an error.

Run: ``RLS_TEST_DATABASE_URL=... uv run pytest tests/test_widget_contact_routes_postgres.py -m postgres -q``

synthetic-data: generator=hand-written seed=0 (invented widget ids, example.com addresses, made-up texts).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from app.services.contact_routes import routes_from_config

pytestmark = pytest.mark.postgres

_SCHEMA = "widget_contact_routes_test_schema"
_SQL = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_widget_contact_routes.sql"
).read_text()

# Longer than a note may be, with a link and an address: the old field allowed 500 characters of free text.
_LONG_REPLY = (
    "Voor prijzen en offertes plan je een gesprek via https://book.example.com/a of mail info@example.com. " * 3
)[:300]
_LEGACY = {
    "title": "Hulp",
    "booking_url": "https://book.example.com/link",
    "contact_email": "hulp@example.com",
    "contact_phone": "010 000 0000",
    "off_topic_subjects": "prijzen, offertes",
    "off_topic_reply": _LONG_REPLY,
    "integrations": {"appointments": {"enabled": True, "booking_url": "https://book.example.com/embed"}},
}
_ROWS = {
    "legacy": _LEGACY,
    "routed": {
        "contact_routes": [{"id": "own", "name": "Eigen route", "email": "eigen@example.com", "is_default": True}],
        "booking_url": "https://book.example.com/old",
    },
    "plain": {"title": "Leeg"},
    # Subjects without a reply never referred, so they may not start referring now.
    "subjects_only": {"off_topic_subjects": "prijzen", "contact_phone": "010 000 0000"},
    "bad_url": {"booking_url": "https://"},
}


@pytest.fixture
async def pg() -> AsyncIterator[asyncpg.Connection]:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set")
    conn = await asyncpg.connect(dsn.replace("+asyncpg", ""))
    await conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE; CREATE SCHEMA {_SCHEMA}")
    await conn.execute(f"CREATE TABLE {_SCHEMA}.widgets (id text PRIMARY KEY, widget_config jsonb NOT NULL)")
    try:
        yield conn
    finally:
        await conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await conn.close()


async def _apply(conn: asyncpg.Connection) -> dict[str, dict]:
    await conn.execute(_SQL.replace("public.widgets", f"{_SCHEMA}.widgets"))
    rows = await conn.fetch(f"SELECT id, widget_config::text AS config FROM {_SCHEMA}.widgets")  # noqa: S608
    return {row["id"]: json.loads(row["config"]) for row in rows}


async def test_the_sql_only_adds_routes_keeps_every_old_key_and_is_idempotent(pg) -> None:
    for widget_id, config in _ROWS.items():
        await pg.execute(f"INSERT INTO {_SCHEMA}.widgets VALUES ($1, $2::jsonb)", widget_id, json.dumps(config))  # noqa: S608

    first = await _apply(pg)
    second = await _apply(pg)

    assert first == second
    for widget_id, config in _ROWS.items():
        # Expand only: every old key is still there, unchanged.
        assert {key: first[widget_id][key] for key in config} == config
    assert first["legacy"]["contact_routes"] == [
        {
            "id": "support",
            "name": "Support",
            # The embedded panel was on, so its URL is the one visitors booked with.
            "booking_url": "https://book.example.com/embed",
            "email": "hulp@example.com",
            "phone": "010 000 0000",
            "do_not_answer": "prijzen, offertes",
            "referral_text": _LONG_REPLY,
            "is_default": True,
        }
    ]
    assert first["subjects_only"]["contact_routes"] == [
        {"id": "support", "name": "Support", "phone": "010 000 0000", "is_default": True}
    ]
    assert "contact_routes" not in first["plain"]
    assert first["routed"]["contact_routes"] == _ROWS["routed"]["contact_routes"]
    # Copied as it was; reading drops the URL the admin API would refuse and keeps the route.
    (route,) = routes_from_config(first["bad_url"], org_id=1, widget_id="bad_url")
    assert (route.name, route.booking_url, route.is_default) == ("Support", None, True)
    (legacy,) = routes_from_config(first["legacy"], org_id=1, widget_id="legacy")
    assert legacy.referral_text == _LONG_REPLY
