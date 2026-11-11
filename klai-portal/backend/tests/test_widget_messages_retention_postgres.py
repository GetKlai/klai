"""Real-PostgreSQL proof that the widget retention sweep survives strict RLS.

Incident (2026-09-16): every run of ``_retention_run_once`` failed with::

    InsufficientPrivilegeError: new row violates row-level security policy
    for table "conversation_quality_judgments"

The anonymizing UPDATEs ran in a ``cross_org_session``. There the policy's
USING clause lets every row through (``_rls_current_org_id()`` is NULL), but
``WITH CHECK (org_id = _rls_current_org_id())`` compares against NULL and
rejects every updated row. The DELETE is not subject to WITH CHECK, which is
why the sweep only broke once a judged conversation expired. A mocked session
cannot express any of this, so these tests use real policies on a non-superuser
role, with the production policy text on the three tables the sweep touches.

Skipped unless ``RLS_TEST_DATABASE_URL`` is set. CI runs them in the postgres
lane (``pytest -m postgres``).

Local run::

    docker run --rm -d --name retention-rls-test -e POSTGRES_PASSWORD=test \
        -p 55441:5432 postgres:16
    RLS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55441/postgres \
        uv run pytest tests/test_widget_messages_retention_postgres.py -m postgres -q
"""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import structlog.testing
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core import database as db_module
from app.core.database import TenantContextSession

pytestmark = pytest.mark.postgres

# A dedicated schema keeps the real table names the service queries without
# touching whatever the CI database already has in `public`.
_SCHEMA = "retention_rls_test"
_ROLE = "retention_rls_test_role"
_ROLE_PASSWORD = "retention_rls_test_pw"  # throwaway role in a disposable container

# S608: interpolated values are module constants, not input.
_SETUP_SQL = [
    f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE",
    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') "  # noqa: S608
    f"THEN CREATE ROLE {_ROLE} LOGIN PASSWORD '{_ROLE_PASSWORD}'; END IF; END $$;",
    f"CREATE SCHEMA {_SCHEMA}",
    f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {_ROLE}",
    # Same body as production's _rls_current_org_id()
    # (post_deploy_rls_raise_on_missing_context.sql), schema-local so it can
    # never replace the real function in a shared database.
    f"""
    CREATE FUNCTION {_SCHEMA}._rls_current_org_id() RETURNS integer LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_org    text := current_setting('app.current_org_id', true);
        v_bypass text := current_setting('app.cross_org_admin', true);
    BEGIN
        IF v_bypass = 'true' THEN RETURN NULL; END IF;
        IF v_org IS NULL OR v_org = '' THEN
            RAISE EXCEPTION 'RLS: app.current_org_id is not set' USING ERRCODE = '42501';
        END IF;
        RETURN v_org::integer;
    END;
    $$
    """,
    f"""CREATE TABLE {_SCHEMA}.portal_orgs (
        id integer PRIMARY KEY, widget_messages_retention_days integer,
        telemetry_level varchar(16) NOT NULL DEFAULT 'shadow')""",
    # Only the columns the retention sweep and the judge's selection read or
    # write.
    f"""CREATE TABLE {_SCHEMA}.widget_conversations (
        id bigint PRIMARY KEY, org_id integer NOT NULL, visitor_name text, visitor_email text,
        started_at timestamptz NOT NULL DEFAULT now(), last_message_at timestamptz NOT NULL DEFAULT now(),
        outcome text, is_preview boolean NOT NULL DEFAULT false, is_test boolean NOT NULL DEFAULT false,
        loaded_origin varchar(200), first_user_query text, session_key text, ip_hash varchar(64))""",
    f"""CREATE TABLE {_SCHEMA}.widget_messages (
        id bigint PRIMARY KEY,
        conversation_id bigint NOT NULL REFERENCES {_SCHEMA}.widget_conversations(id) ON DELETE CASCADE,
        org_id integer NOT NULL, created_at timestamptz NOT NULL,
        role text, content text, sources jsonb, rating text, sequence integer NOT NULL DEFAULT 0,
        answer_signals jsonb)""",
    # The DDL of migration c8e2f5a1d3b7, without the FK to portal_orgs.
    f"""CREATE TABLE {_SCHEMA}.portal_gap_kept_questions (
        id bigserial PRIMARY KEY, org_id integer NOT NULL, message_id bigint NOT NULL,
        conversation_ref varchar(64) NOT NULL, cluster_key varchar(32) NOT NULL, question varchar(200) NOT NULL,
        signals jsonb NOT NULL, turn_at timestamptz NOT NULL,
        CONSTRAINT uq_gap_kept_questions_org_message UNIQUE (org_id, message_id))""",
    f"GRANT USAGE ON SEQUENCE {_SCHEMA}.portal_gap_kept_questions_id_seq TO {_ROLE}",
    f"""CREATE TABLE {_SCHEMA}.portal_gap_proposals (
        id serial PRIMARY KEY, org_id integer NOT NULL, cluster_key varchar NOT NULL,
        decision varchar(16) NOT NULL DEFAULT 'none')""",
    f"""CREATE TABLE {_SCHEMA}.conversation_quality_judgments (
        id bigserial PRIMARY KEY, org_id integer NOT NULL,
        conversation_id bigint UNIQUE REFERENCES {_SCHEMA}.widget_conversations(id) ON DELETE SET NULL,
        channel text, outcome text, failure_category text, reasoning text, confidence text,
        suggested_action text, model_used text, judged_at timestamptz, anonymized_at timestamptz,
        failed_attempts smallint NOT NULL DEFAULT 0, last_attempted_at timestamptz)""",
    f"CREATE TABLE {_SCHEMA}.widget_handoff_sessions (conversation_id bigint)",
    f"GRANT SELECT ON {_SCHEMA}.portal_orgs, {_SCHEMA}.widget_handoff_sessions TO {_ROLE}",
    f"GRANT USAGE ON SEQUENCE {_SCHEMA}.conversation_quality_judgments_id_seq TO {_ROLE}",
]
for _table in (
    "widget_conversations",
    "widget_messages",
    "conversation_quality_judgments",
    "portal_gap_kept_questions",
    "portal_gap_proposals",
):
    _SETUP_SQL += [
        f"ALTER TABLE {_SCHEMA}.{_table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {_SCHEMA}.{_table} FORCE ROW LEVEL SECURITY",
        # Production policy text, verbatim apart from the schema prefix.
        f"CREATE POLICY tenant_isolation ON {_SCHEMA}.{_table} "
        f"USING ({_SCHEMA}._rls_current_org_id() IS NULL OR org_id = {_SCHEMA}._rls_current_org_id()) "
        f"WITH CHECK (org_id = {_SCHEMA}._rls_current_org_id())",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{_table} TO {_ROLE}",
    ]

# Two tenants, each with one conversation: an expired message, a judgment that
# quotes it, and the visitor's contact details. Org 1 also has a fresh message
# that must survive the sweep. Unqualified: the admin engine's search_path is
# the test schema.
_SEED_SQL = [
    "INSERT INTO portal_orgs VALUES (1, NULL), (2, NULL)",
    "INSERT INTO widget_conversations (id, org_id, visitor_name, visitor_email) "
    "VALUES (10, 1, 'Anna', 'anna@example.com'), (20, 2, 'Bram', 'bram@example.com')",
    "INSERT INTO widget_messages (id, conversation_id, org_id, created_at) VALUES "
    "(101, 10, 1, now() - interval '30 days'), (102, 10, 1, now()), "
    "(201, 20, 2, now() - interval '30 days')",
    "INSERT INTO conversation_quality_judgments (org_id, conversation_id, reasoning) "
    "VALUES (1, 10, 'Anna asked about invoices'), (2, 20, 'Bram asked about refunds')",
]


def _role_dsn(superuser_dsn: str) -> str:
    scheme, _, rest = superuser_dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{_ROLE}:{_ROLE_PASSWORD}@{hostpart}"


@pytest.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """Superuser engine (bypasses RLS) with the seeded schema; the service's
    sessions are swapped for a non-superuser role that RLS does apply to."""
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set — real-PostgreSQL retention tests skipped")
    search_path = {"server_settings": {"search_path": _SCHEMA}}
    admin = create_async_engine(dsn, connect_args=search_path)
    async with admin.begin() as conn:
        for stmt in _SETUP_SQL + _SEED_SQL:
            await conn.execute(text(stmt))

    role_engine = create_async_engine(_role_dsn(dsn), connect_args=search_path)
    factory = async_sessionmaker(role_engine, class_=TenantContextSession, expire_on_commit=False)
    try:
        with (
            patch.object(db_module, "AsyncSessionLocal", factory),
            patch("app.services.widget_messages_retention.settings") as mock_settings,
        ):
            mock_settings.widget_messages_retention_days = 7
            mock_settings.widget_jwt_secret = "fictief-test-geheim"
            yield admin
    finally:
        await role_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await admin.dispose()


async def _state(admin: AsyncEngine) -> dict[str, list]:
    async with admin.connect() as conn:
        return {
            "messages": list((await conn.execute(text("SELECT id FROM widget_messages ORDER BY id"))).scalars()),
            "visitors": [
                tuple(r)
                for r in await conn.execute(
                    text("SELECT id, visitor_name, visitor_email FROM widget_conversations ORDER BY id")
                )
            ],
            "reasoning": [
                tuple(r)
                for r in await conn.execute(
                    text(
                        "SELECT conversation_id, reasoning, anonymized_at IS NOT NULL "
                        "FROM conversation_quality_judgments ORDER BY id"
                    )
                )
            ],
        }


async def test_retention_purges_and_anonymizes_expired_conversations_under_strict_rls(
    admin_engine: AsyncEngine,
) -> None:
    from app.services.widget_messages_retention import _retention_run_once

    result = await _retention_run_once()

    assert result["deleted_count"] == 2
    state = await _state(admin_engine)
    assert state["messages"] == [102]
    assert state["visitors"] == [(10, None, None), (20, None, None)]
    assert state["reasoning"] == [(10, None, True), (20, None, True)]


async def test_one_failing_org_neither_blocks_the_others_nor_loses_its_anonymization(
    admin_engine: AsyncEngine,
) -> None:
    """Org 1's anonymization fails in the database; org 2 must still be purged,
    and org 1's expired message must stay until it can be anonymized first."""
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE FUNCTION fail_org_1() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'simulated failure for org 1'; END; $$"
            )
        )
        await conn.execute(
            text(
                "CREATE TRIGGER fail_org_1 BEFORE UPDATE ON conversation_quality_judgments "
                "FOR EACH ROW WHEN (OLD.org_id = 1) EXECUTE FUNCTION fail_org_1()"
            )
        )

    with structlog.testing.capture_logs() as captured:
        result = await _retention_run_once()

    assert result["deleted_count"] == 1
    state = await _state(admin_engine)
    assert state["messages"] == [101, 102]
    assert state["visitors"] == [(10, "Anna", "anna@example.com"), (20, None, None)]
    assert state["reasoning"] == [(10, "Anna asked about invoices", False), (20, None, True)]

    failures = [e for e in captured if e.get("event") == "widget_messages_retention_org_failed"]
    assert [(e["org_id"], e["log_level"]) for e in failures] == [(1, "error")]


async def test_a_locked_org_times_out_and_is_skipped_while_the_other_org_is_purged(
    admin_engine: AsyncEngine,
) -> None:
    """A row lock held elsewhere on org 1's conversation must not stall the run:
    org 1's purge gives up, is logged, and org 2 is still purged."""
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.connect() as locker:
        await locker.begin()
        await locker.execute(text("SELECT 1 FROM widget_conversations WHERE id = 10 FOR UPDATE"))

        with structlog.testing.capture_logs() as captured:
            # Without a lock timeout the run waits on this lock forever.
            result = await asyncio.wait_for(_retention_run_once(), timeout=30)

        await locker.rollback()

    assert result["deleted_count"] == 1
    state = await _state(admin_engine)
    assert state["messages"] == [101, 102]
    assert state["visitors"] == [(10, "Anna", "anna@example.com"), (20, None, None)]
    assert state["reasoning"] == [(10, "Anna asked about invoices", False), (20, None, True)]
    failures = [e for e in captured if e.get("event") == "widget_messages_retention_org_failed"]
    assert [(e["org_id"], e["log_level"]) for e in failures] == [(1, "error")]


async def test_judge_skips_a_conversation_close_to_its_retention_cutoff(admin_engine: AsyncEngine) -> None:
    """A judgment written after retention purged the conversation would quote
    deleted messages and never be anonymized, so the judge leaves a conversation
    near its cutoff (default 7 days here) alone and still judges a younger one."""
    from app.services import conversation_judge as cj

    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO widget_conversations (id, org_id, started_at, outcome) VALUES "
                "(30, 1, now() - interval '7 days' + interval '3 hours', 'resolved'), "
                "(40, 1, now() - interval '2 days', 'resolved')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO widget_messages (id, conversation_id, org_id, created_at, role, content) VALUES "
                "(301, 30, 1, now() - interval '7 days' + interval '3 hours', 'user', 'old question'), "
                "(401, 40, 1, now() - interval '2 days', 'user', 'recent question')"
            )
        )

    verdict = {
        "outcome": "resolved",
        "failure_category": "none",
        "reasoning": "quoted",
        "confidence": "high",
        "suggested_action": None,
    }

    async def _fake_llm(*, model: str, user: str) -> str:
        return json.dumps(verdict)

    with (
        patch.object(cj, "_call_judge_llm", _fake_llm),
        patch.object(cj.settings, "widget_messages_retention_days", 7),
    ):
        judged = await cj._judge_org(1)

    assert judged == 1
    async with admin_engine.connect() as conn:
        judged_ids = set(
            (await conn.execute(text("SELECT conversation_id FROM conversation_quality_judgments"))).scalars()
        )
    assert judged_ids == {10, 20, 40}


# A refusal with nothing found, eight days old: past the 7-day message
# retention, inside the 28 days a gap question is kept. Invented text only.
_GAP_SIGNALS = json.dumps(
    {
        "decision": "refusal",
        "gap_type": "hard",
        "language": "nl",
        "case": {"product": "Wolkcentrale", "problem": "wachtmuziek na sluitingstijd"},
        "search": {"query": "wachtmuziek bel 0612345678", "variants": ["muziek in de wacht"], "found": []},
        "selection": {"open": []},
        "reply_text": "Dat staat niet in de kennisbank.",
    }
)


async def _seed_gap_conversation(
    conn: object,
    conversation_id: int,
    org_id: int,
    *,
    is_test: bool = False,
    days_old: float = 8,
    answer_days_old: float | None = None,
    contact: bool = True,
) -> None:
    """One gap turn: the visitor's message ``days_old`` days ago, the answer ``answer_days_old`` (default the same).

    ``contact`` False leaves the visitor's name and e-mail out, so the sweep's anonymizing UPDATE does not touch
    the conversation row (a race test must not be ordered by that row lock instead of the keep's own).
    """
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO widget_conversations (id, org_id, is_test, loaded_origin, first_user_query, session_key, "
            "ip_hash, visitor_name, visitor_email) VALUES (:id, :org, :is_test, 'https://shop.example.com', "
            "'wachtmuziek?', 'sessie-fictief', 'iphash-fictief', :name, :email)"
        ),
        {
            "id": conversation_id,
            "org": org_id,
            "is_test": is_test,
            "name": "Piet" if contact else None,
            "email": "piet@example.com" if contact else None,
        },
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO widget_messages (id, conversation_id, org_id, created_at, role, content, sequence, "
            "answer_signals) VALUES "
            "(:id * 10 + 1, :id, :org, now() - make_interval(secs => :secs), 'user', "
            "'Mail piet@example.com, klant 12345678: wachtmuziek na 18 uur?', 1, NULL), "
            "(:id * 10 + 2, :id, :org, now() - make_interval(secs => :answer_secs), 'assistant', "
            "'Dat staat niet in de kennisbank.', 2, CAST(:signals AS jsonb))"
        ),
        {
            "id": conversation_id,
            "org": org_id,
            "secs": days_old * 86400,
            "answer_secs": (days_old if answer_days_old is None else answer_days_old) * 86400,
            "signals": _GAP_SIGNALS,
        },
    )


async def _kept(admin: AsyncEngine) -> list[dict]:
    async with admin.connect() as conn:
        rows = await conn.execute(text("SELECT * FROM portal_gap_kept_questions ORDER BY org_id, message_id"))
        return [dict(r._mapping) for r in rows]


async def test_at_full_an_expired_gap_turn_keeps_only_its_masked_question_while_its_messages_are_deleted(
    admin_engine: AsyncEngine,
) -> None:
    from app.services.knowledge_gap_report import GapTurn, cluster_key, conversation_ref
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await _seed_gap_conversation(conn, 30, 1)
        await _seed_gap_conversation(conn, 40, 1, is_test=True)
        # Older than the 28 days a question may be kept.
        await _seed_gap_conversation(conn, 50, 1, days_old=29)

    await _retention_run_once()

    kept = await _kept(admin_engine)
    assert [(k["org_id"], k["message_id"]) for k in kept] == [(1, 302)]
    row = kept[0]
    assert set(row) == {
        "id",
        "org_id",
        "message_id",
        "conversation_ref",
        "cluster_key",
        "question",
        "signals",
        "turn_at",
    }
    assert row["question"] == "Mail … klant …: wachtmuziek na 18 uur?"
    assert row["conversation_ref"] == conversation_ref(1, 30)
    assert row["cluster_key"] == cluster_key(GapTurn("widget", json.loads(_GAP_SIGNALS), row["turn_at"]))
    stored = json.dumps(row["signals"]) + row["question"] + row["conversation_ref"]
    for gone in ("piet@example.com", "12345678", "0612345678", "Piet", "sessie-fictief", "iphash", "kennisbank"):
        assert gone not in stored
    assert row["signals"]["search"] == {"query": "", "found": []}
    # The messages are still deleted after seven days, exactly as before.
    assert not {301, 302, 401, 402, 501, 502} & set((await _state(admin_engine))["messages"])


async def test_below_full_nothing_is_kept(admin_engine: AsyncEngine) -> None:
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'off' WHERE id = 2"))
        await _seed_gap_conversation(conn, 30, 1)  # org 1 stays at the default, shadow
        await _seed_gap_conversation(conn, 60, 2)

    result = await _retention_run_once()

    assert await _kept(admin_engine) == []
    assert result["deleted_count"] == 6


async def test_a_turn_of_a_decided_group_is_not_kept(admin_engine: AsyncEngine) -> None:
    from app.services.knowledge_gap_report import GapTurn, cluster_key
    from app.services.widget_messages_retention import _retention_run_once

    key = cluster_key(GapTurn("widget", json.loads(_GAP_SIGNALS), datetime.now(UTC)))
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await conn.execute(
            text("INSERT INTO portal_gap_proposals (org_id, cluster_key, decision) VALUES (1, :key, 'rejected')"),
            {"key": key},
        )
        await _seed_gap_conversation(conn, 30, 1)

    await _retention_run_once()

    assert await _kept(admin_engine) == []


async def test_a_failing_keep_never_blocks_the_message_purge(admin_engine: AsyncEngine) -> None:
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await _seed_gap_conversation(conn, 30, 1)
        # A NOT NULL violation: Postgres answers with DETAIL "Failing row contains (...)", the question included.
        await conn.execute(
            text(
                "CREATE FUNCTION fail_keep() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN NEW.cluster_key := NULL; RETURN NEW; END; $$"
            )
        )
        await conn.execute(
            text(
                "CREATE TRIGGER fail_keep BEFORE INSERT ON portal_gap_kept_questions "
                "FOR EACH ROW EXECUTE FUNCTION fail_keep()"
            )
        )

    with structlog.testing.capture_logs() as captured:
        result = await _retention_run_once()

    assert result["deleted_count"] == 4
    assert await _kept(admin_engine) == []
    failures = [e for e in captured if e.get("event") == "widget_messages_retention_keep_failed"]
    assert [e["org_id"] for e in failures] == [1]
    # What the log renders: the event and the traceback format_exc_info would write.
    rendered = json.dumps(captured, default=str) + "".join(traceback.format_exception(failures[0]["exc_info"]))
    assert "sqlstate=23502" in rendered
    assert "File " in rendered  # the frames of the failure are there
    for text_part in ("wachtmuziek", "Mail …", "piet", "Failing row"):
        assert text_part not in rendered


async def test_the_daily_purge_drops_kept_questions_past_28_days_and_of_orgs_below_full(
    admin_engine: AsyncEngine,
) -> None:
    from app.services.telemetry_purge import purge_kept_gap_questions

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await conn.execute(
            text(
                "INSERT INTO portal_gap_kept_questions "
                "(org_id, message_id, conversation_ref, cluster_key, question, signals, turn_at) VALUES "
                "(1, 1, 'r', 'tkey', 'vraag een', '{}', now() - interval '27 days'), "
                "(1, 2, 'r', 'tkey', 'vraag twee', '{}', now() - interval '29 days'), "
                "(2, 3, 'r', 'tkey', 'vraag drie', '{}', now() - interval '1 day')"
            )
        )

    with structlog.testing.capture_logs() as captured:
        purged = await purge_kept_gap_questions()

    assert purged == 2
    assert [k["message_id"] for k in await _kept(admin_engine)] == [1]
    assert "vraag" not in json.dumps(captured, default=str)


async def _insert_kept(conn: object, message_id: int, *, org_id: int = 1, ref: str = "r", key: str = "tkey") -> None:
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO portal_gap_kept_questions "
            "(org_id, message_id, conversation_ref, cluster_key, question, signals, turn_at) "
            "VALUES (:org, :mid, :ref, :key, 'vraag', '{}', now() - interval '1 day')"
        ),
        {"org": org_id, "mid": message_id, "ref": ref, "key": key},
    )


async def test_the_daily_purge_drops_kept_questions_of_decided_groups_and_of_marked_conversations(
    admin_engine: AsyncEngine,
) -> None:
    from app.services.knowledge_gap_report import conversation_ref
    from app.services.telemetry_purge import purge_kept_gap_questions

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await _seed_gap_conversation(conn, 30, 1, is_test=True, days_old=1)
        await _seed_gap_conversation(conn, 40, 1, days_old=1)
        await conn.execute(text("UPDATE widget_conversations SET is_preview = true WHERE id = 40"))
        await _seed_gap_conversation(conn, 50, 1, days_old=1)
        await conn.execute(
            text("INSERT INTO portal_gap_proposals (org_id, cluster_key, decision) VALUES (1, 'tdecided', 'rejected')")
        )
        await _insert_kept(conn, 1, ref=conversation_ref(1, 30) or "")
        await _insert_kept(conn, 2, ref=conversation_ref(1, 40) or "")
        await _insert_kept(conn, 3, ref=conversation_ref(1, 50) or "", key="tdecided")
        await _insert_kept(conn, 4, ref=conversation_ref(1, 50) or "")

    assert await purge_kept_gap_questions() == 3

    assert [k["message_id"] for k in await _kept(admin_engine)] == [4]


async def test_without_a_widget_secret_nothing_is_kept(admin_engine: AsyncEngine) -> None:
    from app.services import widget_messages_retention as retention

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await _seed_gap_conversation(conn, 30, 1)

    with patch.object(retention.settings, "widget_jwt_secret", ""):
        result = await retention._retention_run_once()

    assert await _kept(admin_engine) == []
    assert result["deleted_count"] == 4


async def test_a_question_deleted_a_run_before_its_answer_keeps_the_visitors_words(admin_engine: AsyncEngine) -> None:
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        # The cutoff falls between the visitor's message and the answer.
        await _seed_gap_conversation(conn, 30, 1, days_old=7.01, answer_days_old=6.99)

    await _retention_run_once()

    kept = await _kept(admin_engine)
    assert [(k["message_id"], k["question"]) for k in kept] == [(302, "Mail … klant …: wachtmuziek na 18 uur?")]
    assert 302 in (await _state(admin_engine))["messages"]

    # A day later the answer goes too; the kept question stays the visitor's.
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE widget_messages SET created_at = created_at - interval '1 day' WHERE id = 302"))
    await _retention_run_once()

    assert [(k["message_id"], k["question"]) for k in await _kept(admin_engine)] == [
        (302, "Mail … klant …: wachtmuziek na 18 uur?")
    ]
    assert 302 not in (await _state(admin_engine))["messages"]


async def _sweep_while(admin: AsyncEngine, change: list[str]) -> None:
    """Run the sweep while another transaction holds a removal's state change and the exclusive keep lock.

    That transaction commits after the sweep has started waiting; the sweep must then see the change.
    """
    from app.services.knowledge_gap_report import KEPT_QUESTIONS_LOCK
    from app.services.widget_messages_retention import _retention_run_once

    async with admin.connect() as other:
        await other.begin()
        for stmt in change:
            await other.execute(text(stmt))
        await other.execute(text("SELECT pg_advisory_xact_lock(:lock, 1)"), {"lock": KEPT_QUESTIONS_LOCK})
        sweep = asyncio.create_task(_retention_run_once())
        await asyncio.sleep(1)
        assert not sweep.done(), "the sweep did not wait for the removal's lock"
        await other.commit()
        await asyncio.wait_for(sweep, timeout=30)


async def _seed_race(admin: AsyncEngine) -> None:
    async with admin.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level = 'full' WHERE id = 1"))
        await _seed_gap_conversation(conn, 30, 1, contact=False)


async def test_a_conversation_marked_test_while_the_sweep_runs_is_not_kept(admin_engine: AsyncEngine) -> None:
    await _seed_race(admin_engine)

    await _sweep_while(admin_engine, ["UPDATE widget_conversations SET is_test = true WHERE id = 30"])

    assert await _kept(admin_engine) == []


async def test_a_group_decided_while_the_sweep_runs_is_not_kept(admin_engine: AsyncEngine) -> None:
    from app.services.knowledge_gap_report import GapTurn, cluster_key

    await _seed_race(admin_engine)
    key = cluster_key(GapTurn("widget", json.loads(_GAP_SIGNALS), datetime.now(UTC)))

    await _sweep_while(
        admin_engine,
        [f"INSERT INTO portal_gap_proposals (org_id, cluster_key, decision) VALUES (1, '{key}', 'rejected')"],  # noqa: S608
    )

    assert await _kept(admin_engine) == []


async def test_an_org_leaving_full_while_the_sweep_runs_keeps_nothing(admin_engine: AsyncEngine) -> None:
    await _seed_race(admin_engine)

    await _sweep_while(admin_engine, ["UPDATE portal_orgs SET telemetry_level = 'shadow' WHERE id = 1"])

    assert await _kept(admin_engine) == []


@pytest.mark.parametrize(
    "removal",
    [{"cluster_key": "tkey"}, {"conversation_id": 30}, {}],
    ids=["decision", "test-mark", "leave-full"],
)
async def test_each_removal_deletes_its_rows_including_those_a_running_sweep_commits(
    admin_engine: AsyncEngine, removal: dict
) -> None:
    from app.core.database import tenant_scoped_session
    from app.services.knowledge_gap_report import KEPT_QUESTIONS_LOCK, conversation_ref, drop_kept_questions

    ref = conversation_ref(1, 30) or ""
    async with admin_engine.begin() as conn:
        await _insert_kept(conn, 1, ref=ref)
        await _insert_kept(conn, 2, org_id=2, ref=ref)  # another org's row is never touched

    async def _remove() -> int:
        async with tenant_scoped_session(1) as db:
            removed = await drop_kept_questions(db, 1, **removal)
            await db.commit()
        return removed

    # A sweep in flight: it holds the shared lock and has inserted a row it has not committed yet.
    async with admin_engine.connect() as sweep:
        await sweep.begin()
        await sweep.execute(text("SELECT pg_advisory_xact_lock_shared(:lock, 1)"), {"lock": KEPT_QUESTIONS_LOCK})
        await _insert_kept(sweep, 3, ref=ref)
        removal_task = asyncio.create_task(_remove())
        await asyncio.sleep(1)
        assert not removal_task.done(), "the removal did not wait for the sweep"
        await sweep.commit()
        removed = await asyncio.wait_for(removal_task, timeout=30)

    assert removed == 2
    assert [(k["org_id"], k["message_id"]) for k in await _kept(admin_engine)] == [(2, 2)]
