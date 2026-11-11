"""Real-PostgreSQL proof of the two post-deploy files behind widget contact routes.

``post_deploy_widget_contact_routes.sql`` adds one route and leaves the old
settings in place; ``post_deploy_widget_drop_legacy_contact_keys.sql`` then
removes those settings from every widget that has routes. Both run as they ship
(aimed at a throwaway schema), in name order and twice, as every deploy applies
every post-deploy file. What they leave must read back as routes
(``routes_from_config``) without an error.

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

from app.services.contact_routes import SavedContactRoute, routes_from_config

pytestmark = pytest.mark.postgres

_SCHEMA = "widget_contact_routes_test_schema"
_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_SQL = (_VERSIONS / "post_deploy_widget_contact_routes.sql").read_text()
_DROP_SQL = (_VERSIONS / "post_deploy_widget_drop_legacy_contact_keys.sql").read_text()
_OLD_KEYS = {"booking_url", "contact_email", "contact_phone", "off_topic_subjects", "off_topic_reply"}

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
    "referral_only": {"off_topic_subjects": "prijzen", "off_topic_reply": "Bel ons even."},
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


async def _apply(conn: asyncpg.Connection, *files: str) -> dict[str, dict]:
    for sql in files or (_SQL,):
        await conn.execute(sql.replace("public.widgets", f"{_SCHEMA}.widgets"))
    rows = await conn.fetch(f"SELECT id, widget_config::text AS config FROM {_SCHEMA}.widgets")  # noqa: S608
    return {row["id"]: json.loads(row["config"]) for row in rows}


async def _insert(conn: asyncpg.Connection, rows: dict[str, dict]) -> None:
    for widget_id, config in rows.items():
        await conn.execute(f"INSERT INTO {_SCHEMA}.widgets VALUES ($1, $2::jsonb)", widget_id, json.dumps(config))  # noqa: S608


async def test_the_sql_only_adds_routes_keeps_every_old_key_and_is_idempotent(pg) -> None:
    await _insert(pg, _ROWS)

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
    # Subjects and a reply without any channel: the referral keeps running and the route can be saved again.
    assert first["referral_only"]["contact_routes"] == [
        {
            "id": "support",
            "name": "Support",
            "do_not_answer": "prijzen",
            "referral_text": "Bel ons even.",
            "is_default": True,
        }
    ]
    SavedContactRoute.model_validate(first["referral_only"]["contact_routes"][0])


async def test_after_the_conversion_the_old_keys_are_gone_from_every_widget_with_routes(pg) -> None:
    rows = {
        **_ROWS,
        # Saved by the routes release with every route removed: it kept the old keys only for a rollback.
        "saved_without_routes": {"contact_routes": [], "contact_email": "oud@example.com"},
        # Only values the conversion declines, so no route to show them: they stay.
        "declined": {"off_topic_subjects": "prijzen", "contact_email": "  "},
    }
    await _insert(pg, rows)
    converted = await _apply(pg, _SQL)

    first = await _apply(pg, _SQL, _DROP_SQL)

    for widget_id in ("legacy", "routed", "subjects_only", "bad_url", "referral_only", "saved_without_routes"):
        assert not _OLD_KEYS & first[widget_id].keys(), widget_id
        assert first[widget_id]["contact_routes"] == converted[widget_id]["contact_routes"], widget_id
    assert first["legacy"]["integrations"] == {"appointments": {"enabled": True}}
    assert first["legacy"]["title"] == "Hulp"
    assert first["plain"] == rows["plain"]
    assert first["declined"] == rows["declined"]

    # An image from before routes saved a widget after the last deploy: the next
    # deploy converts it first and then clears it, and leaves the rest as it was.
    await _insert(pg, {"late_write": {"booking_url": "https://book.example.com/late"}})
    second = await _apply(pg, _SQL, _DROP_SQL)

    assert second.pop("late_write") == {
        "contact_routes": [
            {"id": "support", "name": "Support", "booking_url": "https://book.example.com/late", "is_default": True}
        ]
    }
    assert second == first
