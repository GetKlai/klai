"""Repository and migration against a real PostgreSQL (``-m postgres``).

Needs ``SLACK_TEST_DATABASE_URL`` (postgresql+asyncpg://…) pointing at a
throwaway database; the module migrates it to head and drops everything after.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic.config import Config
from slack_sdk.oauth.installation_store.models.installation import Installation
from sqlalchemy import text

from alembic import command
from app import db, repo
from app.oauth_store import EncryptedInstallationStore
from app.services.scope import resolve_kb_ids

pytestmark = pytest.mark.postgres

DB_URL = os.environ.get("SLACK_TEST_DATABASE_URL", "")
KEY = "pk_live_synthetic_key_0001"
BOT_TOKEN = "xoxb-synthetic-0001"


@pytest.fixture(scope="module")
def migrated() -> Iterator[None]:
    if not DB_URL:
        pytest.skip("SLACK_TEST_DATABASE_URL not set")
    os.environ["DATABASE_URL"] = DB_URL
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@pytest.fixture
async def engine(migrated: None) -> AsyncIterator[None]:
    db.init_engine(DB_URL)
    yield
    async with db.session() as s:
        for table in (
            "processed_events",
            "turns",
            "thread_scopes",
            "channel_defaults",
            "workspace_links",
            "slack_bots",
            "slack_installations",
        ):
            await s.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await db.dispose_engine()


async def test_event_dedupe_is_insert_first(engine: None) -> None:
    async with db.session() as s:
        assert await repo.mark_event_processed(s, "Ev1") is True
    async with db.session() as s:
        assert await repo.mark_event_processed(s, "Ev1") is False


async def test_link_key_is_encrypted_at_rest(engine: None) -> None:
    async with db.session() as s:
        await repo.save_link(
            s,
            team_id="T1",
            org_id=7,
            org_name="Voorbeeld BV",
            api_key=KEY,
            key_id="4f2c9a1e-0000-4000-8000-000000000042",
            linked_by_slack_user_id="U1",
        )
    async with db.session() as s:
        raw = (await s.execute(text("SELECT klai_api_key_enc FROM workspace_links"))).scalar_one()
        link = await repo.get_link(s, "T1")
    assert KEY not in raw
    assert link is not None
    assert repo.link_api_key(link) == KEY


async def test_scope_rows_and_purge(engine: None) -> None:
    store = EncryptedInstallationStore(client_id="1111.2222", engine=db.engine)  # pyright: ignore[reportArgumentType]
    await store.async_save(
        Installation(
            app_id="A1",
            team_id="T1",
            team_name="Voorbeeld",
            bot_token=BOT_TOKEN,
            bot_id="B1",
            bot_user_id="UB1",
            bot_scopes="chat:write",
            user_id="U1",
        )
    )
    async with db.session() as s:
        await repo.set_channel_default(s, "T1", "C1", [2, 3], "U1")
        await repo.set_thread_scope(s, "T1", "C1", "1.0", [3], "U2")
        await repo.record_turn(
            s,
            team_id="T1",
            channel_id="C1",
            thread_ts="1.0",
            message_ts="1.0",
            role="user",
            text="vraag",
        )
    async with db.session() as s:
        raw_token = (await s.execute(text("SELECT bot_token FROM slack_bots"))).scalar_one()
        assert BOT_TOKEN not in raw_token
        assert await repo.get_bot_token(s, "T1") == BOT_TOKEN
        assert await repo.get_team_name(s, "T1") == "Voorbeeld"
        assert await resolve_kb_ids(s, "T1", "C1", "1.0", [1, 2, 3]) == [3]
        assert await resolve_kb_ids(s, "T1", "C1", "2.0", [1, 2]) == [2]
        assert [t.text for t in await repo.list_turns(s, "T1", "C1", "1.0")] == ["vraag"]
        await repo.set_channel_default(s, "T1", "C1", None, "U1")
        assert await repo.get_channel_default(s, "T1", "C1") is None
    async with db.session() as s:
        await repo.purge_team(s, "T1")
    async with db.session() as s:
        for table in ("thread_scopes", "turns", "slack_bots", "slack_installations"):
            count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table
