"""Real-PostgreSQL proof that the shipped post-deploy SQL turns old contact settings into one valid route.

The file ``post_deploy_widget_contact_routes.sql`` runs as it ships (aimed at a
throwaway schema), twice, as every deploy applies every post-deploy file. What
it leaves must validate as the admin API's ``WidgetConfig``.

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

from app.api.admin_widgets import WidgetConfig

pytestmark = pytest.mark.postgres

_SCHEMA = "widget_contact_routes_test_schema"
_SQL = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_widget_contact_routes.sql"
).read_text()

_LEGACY = {
    "title": "Hulp",
    "booking_url": "https://book.example.com/link",
    "contact_email": "hulp@example.com",
    "contact_phone": "010 000 0000",
    "off_topic_subjects": "prijzen, offertes",
    "off_topic_reply": "Daarvoor plan je een afspraak met een collega.",
    "integrations": {"appointments": {"enabled": True, "booking_url": "https://book.example.com/embed"}},
}
_ROUTED = {
    "contact_routes": [{"id": "own", "name": "Eigen route", "email": "eigen@example.com", "is_default": True}],
    "booking_url": "https://book.example.com/old",
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


async def _seed(conn: asyncpg.Connection, rows: dict[str, dict]) -> None:
    for widget_id, config in rows.items():
        await conn.execute(f"INSERT INTO {_SCHEMA}.widgets VALUES ($1, $2::jsonb)", widget_id, json.dumps(config))  # noqa: S608


async def _apply(conn: asyncpg.Connection) -> dict[str, dict]:
    await conn.execute(_SQL.replace("public.widgets", f"{_SCHEMA}.widgets"))
    rows = await conn.fetch(f"SELECT id, widget_config::text AS config FROM {_SCHEMA}.widgets")  # noqa: S608
    return {row["id"]: json.loads(row["config"]) for row in rows}


async def test_old_settings_become_one_default_route_and_the_old_keys_go(pg) -> None:
    await _seed(pg, {"legacy": _LEGACY, "routed": _ROUTED, "plain": {"title": "Leeg"}})

    first = await _apply(pg)
    second = await _apply(pg)

    assert first == second
    assert first["legacy"] == {
        "title": "Hulp",
        "integrations": {"appointments": {"enabled": True}},
        "contact_routes": [
            {
                "id": "support",
                "name": "Support",
                # The embedded panel was on, so its URL is the one visitors booked with.
                "booking_url": "https://book.example.com/embed",
                "email": "hulp@example.com",
                "phone": "010 000 0000",
                "do_not_answer": "prijzen, offertes",
                "note": "Daarvoor plan je een afspraak met een collega.",
                "is_default": True,
            }
        ],
    }
    assert first["routed"] == {"contact_routes": _ROUTED["contact_routes"]}
    assert first["plain"] == {"title": "Leeg"}
    for config in first.values():
        WidgetConfig.model_validate(config)


async def test_a_value_the_admin_api_refuses_stops_the_file_and_changes_nothing(pg) -> None:
    bad = {**_LEGACY, "off_topic_reply": "Kijk op https://example.com/prijzen"}
    await _seed(pg, {"bad": bad})

    with pytest.raises(asyncpg.RaiseError, match="bad"):
        await _apply(pg)
    # The file's own BEGIN is still open and aborted, as psql -v ON_ERROR_STOP=1 leaves it.
    await pg.execute("ROLLBACK")

    stored = await pg.fetchval(f"SELECT widget_config::text FROM {_SCHEMA}.widgets")  # noqa: S608
    assert json.loads(stored) == bad
