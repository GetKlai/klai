"""Real-PostgreSQL proof of the support-case store's durability, serialization,
RLS isolation and cascade contract (SPEC-RAG-SUPPORT-GAP).

These cannot be expressed with a mocked session: the advisory-lock
serialization, the commit-before-analyze durability, the Cat-D tenant isolation
and the ON DELETE CASCADE chains are database behaviour. Like
``test_rls_txn_context_postgres.py``, this creates its own throwaway schema,
tables, RLS policy and non-superuser role and drops them afterwards, so it runs
against any admin DSN (the CI fixture DB or a fresh one) and does not depend on
a migrated database. The service's unqualified table names resolve into the
schema via both engines' search_path; RLS applies because the role is not the
owner. (The real Alembic migration + post-deploy SQL are exercised separately.)

Run: ``uv run pytest tests/test_support_cases_postgres.py -m postgres -q``
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import types
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.api.app_gaps import _list_support_gaps
from app.core import database as db_module
from app.core.database import TenantContextSession, set_tenant
from app.schemas_support_cases import SupportCaseMessage, SupportCasePayload
from app.services import support_cases as svc

pytestmark = pytest.mark.postgres

_SCHEMA = "support_case_test_schema"
_ROLE = "support_case_test_role"
_PW = "support_case_test_pw"  # throwaway role in a disposable schema

# All DDL is schema-qualified; runtime queries are unqualified and resolve via
# search_path. Explicit minimal tables (only the columns the store reads/writes)
# instead of a full migration, so the test is self-contained.
_SETUP = [
    f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE",
    # S608: every interpolated value is a module constant, never input.
    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_ROLE}') "  # noqa: S608
    f"THEN CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PW}'; END IF; END $$;",
    f"CREATE SCHEMA {_SCHEMA}",
    f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {_ROLE}",
    # Same fail-loud body as production's public._rls_current_org_id(), schema-
    # local so it can never shadow the real one in a shared database.
    f"""
    CREATE FUNCTION {_SCHEMA}._rls_current_org_id() RETURNS integer LANGUAGE plpgsql STABLE AS $fn$
    DECLARE v_org text := current_setting('app.current_org_id', true);
            v_bypass text := current_setting('app.cross_org_admin', true);
    BEGIN
        IF v_bypass = 'true' THEN RETURN NULL; END IF;
        IF v_org IS NULL OR v_org = '' THEN
            RAISE EXCEPTION 'RLS: app.current_org_id is not set' USING ERRCODE = '42501';
        END IF;
        RETURN v_org::integer;
    END; $fn$
    """,
    f"CREATE TABLE {_SCHEMA}.portal_orgs (id integer PRIMARY KEY, telemetry_level text NOT NULL DEFAULT 'shadow', "
    f"platform_unlocked_features text[] NOT NULL DEFAULT ARRAY[]::text[])",
    f"CREATE TABLE {_SCHEMA}.portal_users (id bigserial PRIMARY KEY, org_id integer NOT NULL, "
    f"zitadel_user_id varchar(64))",
    f"CREATE TABLE {_SCHEMA}.portal_knowledge_bases (id bigserial PRIMARY KEY, org_id integer NOT NULL, "
    f"slug varchar(64) NOT NULL, UNIQUE(org_id, slug))",
    f"CREATE TABLE {_SCHEMA}.portal_taxonomy_nodes (id bigserial PRIMARY KEY, kb_id integer NOT NULL, "
    f"parent_id integer, name varchar(128) NOT NULL)",
    f"CREATE TABLE {_SCHEMA}.portal_connectors (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
    f"org_id integer NOT NULL, kb_id integer NOT NULL, config jsonb NOT NULL DEFAULT '{{}}'::jsonb, "
    f"state text NOT NULL DEFAULT 'active')",
    f"""CREATE TABLE {_SCHEMA}.portal_retrieval_gaps (
        id bigserial PRIMARY KEY, org_id integer NOT NULL, user_id text NOT NULL, query_text text NOT NULL,
        gap_type text NOT NULL, top_score double precision, nearest_kb_slug text,
        chunks_retrieved integer NOT NULL DEFAULT 0, retrieval_ms integer NOT NULL DEFAULT 0,
        taxonomy_node_ids integer[], caller_client_id varchar(64), conversation_id bigint, language varchar(8),
        occurred_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz, resolved_by varchar(16),
        resolved_by_user_id integer, support_case_id bigint, diagnosis varchar(24), question_key text,
        audience varchar(16), evidence jsonb)""",
    # Only the id is read: list_gaps joins it to link a group to its conversation.
    f"CREATE TABLE {_SCHEMA}.widget_conversations (id bigserial PRIMARY KEY, org_id integer NOT NULL)",
    f"""CREATE TABLE {_SCHEMA}.portal_support_cases (
        id bigserial PRIMARY KEY, org_id integer NOT NULL, kb_slug varchar(64) NOT NULL, connector_id uuid,
        source varchar(16) NOT NULL, account_id text NOT NULL, external_id text NOT NULL, created_by text NOT NULL,
        payload jsonb NOT NULL, content_hash varchar(64) NOT NULL, status varchar(16) NOT NULL,
        analysis_version varchar(64), analysis jsonb, reviews jsonb, imported_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(org_id, kb_slug, source, account_id, external_id))""",
    # Cat-D strict RLS on the evidence table (the production policy shape).
    f"ALTER TABLE {_SCHEMA}.portal_support_cases ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {_SCHEMA}.portal_support_cases FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation ON {_SCHEMA}.portal_support_cases "
    f"USING ({_SCHEMA}._rls_current_org_id() IS NULL OR org_id = {_SCHEMA}._rls_current_org_id()) "
    f"WITH CHECK (org_id = {_SCHEMA}._rls_current_org_id())",
    # Cascade chain: connector/KB delete and case delete remove derived findings.
    f"ALTER TABLE {_SCHEMA}.portal_retrieval_gaps ADD CONSTRAINT fk_g_case FOREIGN KEY (support_case_id) "
    f"REFERENCES {_SCHEMA}.portal_support_cases(id) ON DELETE CASCADE",
    f"ALTER TABLE {_SCHEMA}.portal_support_cases ADD CONSTRAINT fk_c_conn FOREIGN KEY (connector_id) "
    f"REFERENCES {_SCHEMA}.portal_connectors(id) ON DELETE CASCADE",
    f"ALTER TABLE {_SCHEMA}.portal_support_cases ADD CONSTRAINT fk_c_kb FOREIGN KEY (org_id, kb_slug) "
    f"REFERENCES {_SCHEMA}.portal_knowledge_bases(org_id, slug) ON DELETE CASCADE",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {_SCHEMA} TO {_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {_SCHEMA} TO {_ROLE}",
    f"GRANT EXECUTE ON FUNCTION {_SCHEMA}._rls_current_org_id() TO {_ROLE}",
]

# Two orgs (telemetry full, knowledge_gaps unlocked), one KB each; connector
# seeded in the fixture. Both gates on by default so existing tests exercise the
# happy path; the feature-gate tests below toggle knowledge_gaps off explicitly.
_SEED = [
    "INSERT INTO portal_orgs (id, telemetry_level, platform_unlocked_features) "
    "VALUES (901,'full',ARRAY['knowledge_gaps']),(902,'full',ARRAY['knowledge_gaps'])",
    "INSERT INTO portal_knowledge_bases (org_id, slug) VALUES (901,'kb-a'),(902,'kb-b')",
    "INSERT INTO portal_taxonomy_nodes (kb_id, name) VALUES "
    "((SELECT id FROM portal_knowledge_bases WHERE org_id=901 AND slug='kb-a'),'Billing'),"
    "((SELECT id FROM portal_knowledge_bases WHERE org_id=902 AND slug='kb-b'),'Foreign')",
]


def _role_dsn(dsn: str) -> str:
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{_ROLE}:{_PW}@{hostpart}"


def _payload(
    external_id: str = "t-1", text_value: str = "I cannot log in", complete: bool = True
) -> SupportCasePayload:
    p = SupportCasePayload(
        source="hubspot",
        account_id="12345",
        external_id=external_id,
        subject="Login",
        complete=complete,
        messages=[SupportCaseMessage(id="m1", kind="message", role="customer", text=text_value)],
    )
    p.bind_kb("kb-a")
    return p


@pytest.fixture
def stub_analyzer() -> Iterator[types.ModuleType]:
    module = types.ModuleType("app.services.support_case_analysis")
    module.ANALYSIS_VERSION = "vtest"  # type: ignore[attr-defined]
    module.analyze_support_case = AsyncMock(  # type: ignore[attr-defined]
        return_value=[{"question": "Reset 2FA?", "diagnosis": "missing", "gap_type": "hard", "language": "en"}]
    )
    sys.modules["app.services.support_case_analysis"] = module
    try:
        yield module
    finally:
        sys.modules.pop("app.services.support_case_analysis", None)


@pytest.fixture
async def pg(stub_analyzer) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker, str, types.ModuleType]]:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set")
    search_path = {"server_settings": {"search_path": _SCHEMA}}
    admin = create_async_engine(dsn, connect_args=search_path)
    async with admin.begin() as conn:
        for stmt in _SETUP + _SEED:
            await conn.execute(text(stmt))
        cid = (
            await conn.execute(
                text(
                    "INSERT INTO portal_connectors (org_id, kb_id, config) "
                    'SELECT 901, id, \'{"account_id":"12345"}\'::jsonb '
                    "FROM portal_knowledge_bases WHERE org_id=901 AND slug='kb-a' RETURNING id"
                )
            )
        ).scalar_one()
    role_engine = create_async_engine(_role_dsn(dsn), connect_args=search_path)
    factory = async_sessionmaker(role_engine, class_=TenantContextSession, expire_on_commit=False)
    try:
        with patch.object(db_module, "AsyncSessionLocal", factory):
            yield admin, factory, str(cid), stub_analyzer
    finally:
        await role_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await admin.dispose()


async def _counts(admin: AsyncEngine, org_id: int = 901) -> tuple[int, int]:
    async with admin.connect() as conn:
        cases = (
            await conn.execute(text("SELECT count(*) FROM portal_support_cases WHERE org_id=:o"), {"o": org_id})
        ).scalar_one()
        finds = (
            await conn.execute(
                text("SELECT count(*) FROM portal_retrieval_gaps WHERE org_id=:o AND support_case_id IS NOT NULL"),
                {"o": org_id},
            )
        ).scalar_one()
    return cases, finds


async def _upsert(
    factory, cid: str, payload: SupportCasePayload, org_id: int = 901, force: bool = False
) -> svc.UpsertResult:
    async with factory() as db:
        await set_tenant(db, org_id)
        return await svc.upsert_support_case(
            db,
            org_id=org_id,
            zitadel_org_id=f"zit-{org_id}",
            telemetry_level="full",
            connector_id=cid,
            created_by="u-901",
            kb_slug="kb-a",
            payload=payload,
            force_reanalysis=force,
        )


async def _analysis(admin: AsyncEngine, org_id: int = 901) -> tuple[str, list]:
    async with admin.connect() as conn:
        row = (
            await conn.execute(text("SELECT status, analysis FROM portal_support_cases WHERE org_id=:o"), {"o": org_id})
        ).first()
    assert row is not None
    return row[0], row[1]


# --------------------------------------------------------------------------- #


async def _gap_topics(factory, org_id: int = 901) -> list:
    async with factory() as db:
        await set_tenant(db, org_id)
        return await _list_support_gaps(
            perms=types.SimpleNamespace(org_id=org_id),
            db=db,
            cutoff=datetime.now(tz=UTC) - timedelta(days=30),
            gap_type=None,
            language=None,
            include_resolved=False,
            limit=50,
        )


async def test_support_gap_provenance_counts_cases_once_and_orders_ties(pg) -> None:
    admin, factory, _cid, _analyzer = pg
    async with admin.begin() as conn:
        case_ids = list(
            (
                await conn.execute(
                    text(
                        "INSERT INTO portal_support_cases "
                        "(org_id,kb_slug,source,account_id,external_id,created_by,payload,content_hash,status) "
                        "VALUES (901,'kb-a','audio','a','call','u','{}'::jsonb,:h,'analyzed'),"
                        "(901,'kb-a','hubspot','a','ticket','u','{}'::jsonb,:h,'analyzed') RETURNING id"
                    ),
                    {"h": "s" * 64},
                )
            ).scalars()
        )
        await conn.execute(
            text(
                "INSERT INTO portal_retrieval_gaps "
                "(org_id,user_id,query_text,gap_type,support_case_id,diagnosis,question_key,occurred_at) VALUES "
                "(901,'u','Mixed','content',:a,'missing','mixed','2026-09-18T10:00:00Z'),"
                "(901,'u','Mixed again','content',:a,'missing','mixed','2026-09-18T11:00:00Z'),"
                "(901,'u','Mixed ticket','content',:h,'missing','mixed','2026-09-18T12:00:00Z'),"
                "(901,'u','Older','content',:a,'missing','z-old','2026-09-17T10:00:00Z'),"
                "(901,'u','New B','content',:a,'missing','b-new','2026-09-19T10:00:00Z'),"
                "(901,'u','New A','content',:a,'missing','a-new','2026-09-19T10:00:00Z')"
            ),
            {"a": case_ids[0], "h": case_ids[1]},
        )

    gaps = await _gap_topics(factory)
    mixed = gaps[0]
    assert (mixed.group_key, mixed.occurrence_count) == ("mixed", 2)
    assert mixed.support_case_ids == sorted(case_ids)
    assert mixed.support_sources == ["audio", "hubspot"]
    assert [gap.group_key for gap in gaps[1:]] == ["a-new", "b-new", "z-old"]


async def test_support_gap_classified_topic_surfaces_in_gaps(pg) -> None:
    admin, factory, cid, _analyzer = pg
    async with admin.begin() as conn:
        node_a = (
            await conn.execute(
                text(
                    "SELECT n.id FROM portal_taxonomy_nodes n JOIN portal_knowledge_bases kb ON kb.id=n.kb_id "
                    "WHERE kb.org_id=901 AND kb.slug='kb-a' AND n.name='Billing'"
                )
            )
        ).scalar_one()
        primary_id = (
            await conn.execute(
                text(
                    "INSERT INTO portal_taxonomy_nodes (kb_id, name) "
                    "SELECT id, 'Number porting' FROM portal_knowledge_bases "
                    "WHERE org_id=901 AND slug='kb-a' RETURNING id"
                )
            )
        ).scalar_one()

    with patch(
        "app.services.knowledge_ingest_client.classify_gap_taxonomy",
        AsyncMock(return_value=[primary_id, node_a]),
    ):
        await _upsert(factory, cid, _payload())

    async with admin.connect() as conn:
        stored = (
            await conn.execute(
                text(
                    "SELECT taxonomy_node_ids FROM portal_retrieval_gaps "
                    "WHERE org_id=901 AND support_case_id IS NOT NULL"
                )
            )
        ).scalar_one()
    assert stored == [primary_id, node_a]

    gaps = await _gap_topics(factory)
    assert len(gaps) == 1
    assert gaps[0].topic is not None
    assert (gaps[0].topic.id, gaps[0].topic.name) == (primary_id, "Number porting")


async def test_support_topic_excludes_foreign_unknown_and_legacy_null(pg) -> None:
    admin, factory, _cid, _analyzer = pg
    async with admin.begin() as conn:
        await conn.execute(
            text("INSERT INTO portal_knowledge_bases (org_id, slug) VALUES (901, 'kb-other'), (902, 'kb-a')")
        )
        await conn.execute(
            text(
                "INSERT INTO portal_taxonomy_nodes (kb_id, name) SELECT id, 'Foreign' FROM portal_knowledge_bases WHERE (org_id=901 AND slug='kb-other') OR (org_id=902 AND slug='kb-a')"
            )
        )
        node_b = (
            (
                await conn.execute(
                    text(
                        "SELECT n.id FROM portal_taxonomy_nodes n JOIN portal_knowledge_bases kb ON kb.id=n.kb_id "
                        "WHERE n.name='Foreign'"
                    )
                )
            )
            .scalars()
            .all()
        )
        case_id = (
            await conn.execute(
                text(
                    "INSERT INTO portal_support_cases "
                    "(org_id, kb_slug, source, account_id, external_id, created_by, payload, content_hash, status) "
                    "VALUES (901,'kb-a','hubspot','a','ext-x','u','{}'::jsonb, :h, 'analyzed') RETURNING id"
                ),
                {"h": "b" * 64},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO portal_retrieval_gaps "
                "(org_id, user_id, query_text, gap_type, nearest_kb_slug, support_case_id, diagnosis, "
                "question_key, taxonomy_node_ids) VALUES "
                "(901,'u','foreign','content','kb-a',:c,'missing','k-foreign', CAST(:nb AS integer[])),"
                "(901,'u','unknown','content','kb-a',:c,'missing','k-unknown', ARRAY[999999]),"
                "(901,'u','legacy','content','kb-a',:c,'missing','k-legacy', NULL)"
            ),
            {"c": case_id, "nb": node_b},
        )

    gaps = await _gap_topics(factory)
    assert {g.group_key for g in gaps} == {"k-foreign", "k-unknown", "k-legacy"}
    assert all(g.topic is None for g in gaps)


async def test_repeat_identical_import_is_one_case_one_finding_no_reanalysis(pg) -> None:
    admin, factory, cid, analyzer = pg
    r1 = await _upsert(factory, cid, _payload())
    r2 = await _upsert(factory, cid, _payload())

    assert (r1.status, r1.changed, r1.findings_count) == ("analyzed", True, 1)
    assert (r2.status, r2.changed, r2.findings_count) == ("analyzed", False, 1)
    assert await _counts(admin) == (1, 1)
    # The successful analysis is not re-run on the byte-identical re-import.
    assert analyzer.analyze_support_case.await_count == 1


async def test_connector_import_analyses_as_tenant_only_identity(pg) -> None:
    """Org-owned connector analysis must run under a tenant-only identity, so
    offboarding/suspending the connector's creator cannot break future service
    analysis. created_by stays as audit attribution on the case + finding."""
    admin, factory, cid, analyzer = pg
    r = await _upsert(factory, cid, _payload())  # connector_id=cid, created_by="u-901"
    assert (r.status, r.findings_count) == ("analyzed", 1)

    assert analyzer.analyze_support_case.await_args.kwargs["user_id"] is None
    async with admin.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT created_by, user_id FROM portal_support_cases c "
                    "JOIN portal_retrieval_gaps g ON g.support_case_id=c.id WHERE c.org_id=901"
                )
            )
        ).first()
    assert row is not None and row[0] == "u-901" and row[1] == "u-901"  # audit attribution preserved


async def test_user_import_analyses_as_authenticated_caller(pg) -> None:
    """A user/transcript import (connector_id=None) keeps the authenticated
    caller's identity — the connector tenant-only rule must not leak to it."""
    _, factory, _, analyzer = pg
    async with factory() as db:
        await set_tenant(db, 901)
        r = await svc.upsert_support_case(
            db,
            org_id=901,
            zitadel_org_id="zit-901",
            telemetry_level="full",
            connector_id=None,
            created_by="u-901",
            kb_slug="kb-a",
            payload=_payload(external_id="transcript-1"),
        )
    assert r.status == "analyzed"
    assert analyzer.analyze_support_case.await_args.kwargs["user_id"] == "u-901"


async def test_stale_caller_policy_cannot_persist_evidence_after_downgrade(pg) -> None:
    admin, factory, cid, analyzer = pg
    async with admin.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level='shadow' WHERE id=901"))

    with pytest.raises(svc.SupportTelemetryError):
        await _upsert(factory, cid, _payload())

    assert await _counts(admin) == (0, 0)
    analyzer.analyze_support_case.assert_not_awaited()


async def test_missing_feature_blocks_import_no_persistence(pg) -> None:
    """knowledge_gaps not unlocked (telemetry still full) => the authoritative
    locked policy 403s at phase 1, before any evidence is stored or model runs."""
    from fastapi import HTTPException

    admin, factory, cid, analyzer = pg
    async with admin.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET platform_unlocked_features=ARRAY[]::text[] WHERE id=901"))

    with pytest.raises(HTTPException) as exc:
        await _upsert(factory, cid, _payload())
    assert exc.value.status_code == 403 and exc.value.detail["error_code"] == "feature_not_unlocked"
    assert await _counts(admin) == (0, 0)
    analyzer.analyze_support_case.assert_not_awaited()


async def test_feature_revoke_during_analysis_blocks_results_then_reenable_retries(pg) -> None:
    """A knowledge_gaps revoke racing a slow analysis: the phase-1 evidence stays
    the committed 'pending' (never purged here), no findings are written, and the
    caller gets 403. Re-unlocking lets the same pending evidence retry to
    'analyzed' — proving the revoke blocks results without destroying evidence."""
    from fastapi import HTTPException

    admin, factory, cid, analyzer = pg
    gate = asyncio.Event()

    async def _slow(**_: object) -> list[dict]:
        gate.set()
        await asyncio.sleep(0.3)
        return [{"question": "Q", "diagnosis": "missing", "gap_type": "hard", "language": "en"}]

    analyzer.analyze_support_case = AsyncMock(side_effect=_slow)
    task = asyncio.create_task(_upsert(factory, cid, _payload()))
    await gate.wait()  # phase-1 evidence committed 'pending', analyzer running
    async with admin.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET platform_unlocked_features=ARRAY[]::text[] WHERE id=901"))

    with pytest.raises(HTTPException) as exc:
        await task
    assert exc.value.status_code == 403 and exc.value.detail["error_code"] == "feature_not_unlocked"
    async with admin.connect() as conn:
        status = (await conn.execute(text("SELECT status FROM portal_support_cases WHERE org_id=901"))).scalar_one()
    assert status == "pending"  # evidence preserved, not purged
    assert await _counts(admin) == (1, 0)  # no findings committed post-revoke

    analyzer.analyze_support_case = AsyncMock(
        return_value=[{"question": "Q", "diagnosis": "missing", "gap_type": "hard", "language": "en"}]
    )
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE portal_orgs SET platform_unlocked_features=ARRAY['knowledge_gaps'] WHERE id=901")
        )
    r = await _upsert(factory, cid, _payload())  # same evidence, feature back on
    assert (r.status, r.findings_count) == ("analyzed", 1)
    assert await _counts(admin) == (1, 1)


async def test_concurrent_identical_import_does_not_duplicate(pg) -> None:
    admin, factory, cid, _ = pg
    await asyncio.gather(_upsert(factory, cid, _payload()), _upsert(factory, cid, _payload()))
    assert await _counts(admin) == (1, 1)


async def test_changed_evidence_invalidates_and_reanalyses(pg) -> None:
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload(text_value="original"))
    async with admin.connect() as conn:
        old_hash = (await conn.execute(text("SELECT content_hash FROM portal_support_cases"))).scalar_one()

    analyzer.analyze_support_case = AsyncMock(
        return_value=[
            {"question": "Reset 2FA?", "diagnosis": "missing", "gap_type": "hard", "language": "en"},
            {"question": "Billing?", "diagnosis": "incomplete", "gap_type": None, "language": "en"},
        ]
    )
    r = await _upsert(factory, cid, _payload(text_value="changed text"))

    assert (r.status, r.changed, r.findings_count) == ("analyzed", True, 2)
    assert await _counts(admin) == (1, 2)  # one case, old finding replaced by two
    async with admin.connect() as conn:
        new_hash = (await conn.execute(text("SELECT content_hash FROM portal_support_cases"))).scalar_one()
    assert new_hash != old_hash


async def test_failed_analysis_can_retry_on_same_payload(pg) -> None:
    """#1: a failed import is not a permanent no-op — the same payload retries."""
    admin, factory, cid, analyzer = pg
    analyzer.analyze_support_case = AsyncMock(side_effect=RuntimeError("judge down"))
    r1 = await _upsert(factory, cid, _payload())
    assert (r1.status, r1.findings_count) == ("failed", 0)
    assert await _counts(admin) == (1, 0)

    analyzer.analyze_support_case = AsyncMock(
        return_value=[{"question": "Reset 2FA?", "diagnosis": "missing", "gap_type": "hard", "language": "en"}]
    )
    r2 = await _upsert(factory, cid, _payload())  # identical payload, previously failed
    assert (r2.status, r2.findings_count) == ("analyzed", 1)
    assert await _counts(admin) == (1, 1)


async def test_force_reanalysis_reruns_an_already_analysed_case(pg) -> None:
    """A byte-identical re-import is a no-op, but force_reanalysis=True re-runs the
    analyzer against the same evidence (contract 2: reanalyse even when the payload
    and version are unchanged, because the KB behind it may have moved)."""
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload())
    await _upsert(factory, cid, _payload())  # no-op, not re-analysed
    assert analyzer.analyze_support_case.await_count == 1

    r = await _upsert(factory, cid, _payload(), force=True)
    assert (r.status, r.reanalysis_failed) == ("analyzed", False)
    assert analyzer.analyze_support_case.await_count == 2
    assert await _counts(admin) == (1, 1)


async def test_force_reanalysis_preserves_old_findings_when_analysis_fails(pg) -> None:
    """A forced reanalysis that fails (judge down) must not blank the previously
    good analysis: the old findings and status stay, the result flags the failure,
    and the case remains retryable (contract 2 + the changed-KB/failed-analysis
    semantics reported in the summary)."""
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload())
    assert await _counts(admin) == (1, 1)

    analyzer.analyze_support_case = AsyncMock(side_effect=RuntimeError("judge down"))
    r = await _upsert(factory, cid, _payload(), force=True)
    assert r.reanalysis_failed is True
    assert r.status == "analyzed"  # old analysis preserved, not dropped to 'failed'
    status, analysis = await _analysis(admin)
    assert status == "analyzed" and analysis and analysis[0]["question"] == "Reset 2FA?"
    assert await _counts(admin) == (1, 1)  # the old finding is still there


async def test_force_run_token_stops_a_stale_run_overwriting_a_fresher_one(pg) -> None:
    """Same-hash concurrency: a slow forced run must not overwrite the result of a
    newer forced run that started and finished while it was still analysing. The
    run token (row xmin) makes the older run discard its stale result rather than
    clobber the fresher one (contract 2: force must not defeat stale-run protection)."""
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload())  # seed an analysed case (1 finding)

    gate = asyncio.Event()

    async def _slow(**_: object) -> list[dict]:
        gate.set()
        await asyncio.sleep(0.4)
        return [{"question": "STALE", "diagnosis": "missing", "gap_type": "hard", "language": "en"}]

    analyzer.analyze_support_case = AsyncMock(side_effect=_slow)
    slow_run = asyncio.create_task(_upsert(factory, cid, _payload(), force=True))
    await gate.wait()  # slow run has checkpointed its token and is analysing

    # A newer forced run starts and finishes while the slow one is still analysing.
    analyzer.analyze_support_case = AsyncMock(
        return_value=[
            {"question": "FRESH-A", "diagnosis": "missing", "gap_type": "hard", "language": "en"},
            {"question": "FRESH-B", "diagnosis": "incomplete", "gap_type": None, "language": "en"},
        ]
    )
    fresh = await _upsert(factory, cid, _payload(), force=True)
    assert (fresh.status, fresh.findings_count) == ("analyzed", 2)

    await slow_run  # completes late; its token no longer matches -> discards result

    status, analysis = await _analysis(admin)
    assert status == "analyzed"
    questions = sorted(f["question"] for f in analysis)
    assert questions == ["FRESH-A", "FRESH-B"]  # the fresh run won; the stale one did not clobber it
    assert await _counts(admin) == (1, 2)


async def test_incomplete_case_is_stored_but_not_analysed(pg) -> None:
    admin, factory, cid, analyzer = pg
    r = await _upsert(factory, cid, _payload(complete=False))
    assert (r.status, r.findings_count) == ("incomplete", 0)
    assert await _counts(admin) == (1, 0)
    analyzer.analyze_support_case.assert_not_awaited()


async def test_pending_evidence_is_durable_and_visible_during_slow_analysis(pg) -> None:
    """#2: evidence is committed BEFORE analysis, so a second session sees the
    case (pending) while the analyzer is still running."""
    admin, factory, cid, analyzer = pg
    gate = asyncio.Event()

    async def _slow(**_: object) -> list[dict]:
        gate.set()
        await asyncio.sleep(0.3)
        return [{"question": "Q", "diagnosis": "missing", "gap_type": "hard", "language": "en"}]

    analyzer.analyze_support_case = AsyncMock(side_effect=_slow)
    task = asyncio.create_task(_upsert(factory, cid, _payload()))
    await gate.wait()  # analyzer started -> phase-1 evidence already committed
    async with admin.connect() as conn:
        row = (await conn.execute(text("SELECT status FROM portal_support_cases WHERE org_id=901"))).first()
    assert row is not None and row[0] == "pending"
    await task
    assert await _counts(admin) == (1, 1)


async def test_foreign_org_cannot_read_case_under_rls(pg) -> None:
    _, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())
    async with factory() as db:
        await set_tenant(db, 902)  # different tenant
        found = (
            await db.execute(text("SELECT count(*) FROM portal_support_cases WHERE id=:id"), {"id": r.case_id})
        ).scalar_one()
    assert found == 0  # Cat-D RLS hides org 901's case from org 902


async def test_kb_delete_cascades_to_cases_and_findings(pg) -> None:
    admin, factory, cid, _ = pg
    await _upsert(factory, cid, _payload())
    assert await _counts(admin) == (1, 1)
    async with admin.begin() as conn:
        await conn.execute(text("DELETE FROM portal_knowledge_bases WHERE org_id=901 AND slug='kb-a'"))
    assert await _counts(admin) == (0, 0)  # #4: audio+ticket evidence and findings gone with the KB


async def test_connector_delete_cascades_to_cases_and_findings(pg) -> None:
    admin, factory, cid, _ = pg
    await _upsert(factory, cid, _payload())
    async with admin.begin() as conn:
        await conn.execute(text("DELETE FROM portal_connectors WHERE id=:id"), {"id": cid})
    assert await _counts(admin) == (0, 0)


async def test_purge_removes_all_support_evidence(pg) -> None:
    admin, factory, cid, _ = pg
    await _upsert(factory, cid, _payload())
    async with factory() as db:
        await set_tenant(db, 901)
        removed = await svc.purge_support_cases_for_org(db, 901)
    assert removed == 1
    assert await _counts(admin) == (0, 0)


async def test_manual_close_closes_every_diagnosis_in_the_group_but_only_that_kb(pg) -> None:
    """#A: a support group is closed by its persisted question_key, so two
    differently cased/spaced cases AND a second diagnosis about the same
    question all close together — a diagnosis says how a gap was detected, not
    what the customer needs (SPEC-RAG-GAP-GROUPING). Another KB is untouched,
    and the KB selector is required."""
    from app.api.app_gaps import GapResolveRequest, resolve_gap
    from app.services.support_cases import _question_key
    from tests.conftest import make_perms

    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())  # one real case to reference
    case_id = r.case_id
    k_target = _question_key(question="How export?", language="en", kb_slug="kb-a", audience=None)
    k_other_kb = _question_key(question="How export?", language="en", kb_slug="kb-b", audience=None)

    async with admin.begin() as conn:
        # Two spellings and two diagnoses, one persisted key (the store
        # normalized them all the same); a fourth row on another KB.
        await conn.execute(
            text(
                "INSERT INTO portal_retrieval_gaps "
                "(org_id,user_id,query_text,gap_type,nearest_kb_slug,language,diagnosis,question_key,support_case_id) "
                "VALUES "
                "(901,'u','How export?','content','kb-a','en','missing',:k,:c),"
                "(901,'u','how  export?','content','kb-a','en','missing',:k,:c),"
                "(901,'u','How export?','content','kb-a','en','incomplete',:k,:c),"
                "(901,'u','How export?','content','kb-b','en','missing',:kk,:c)"
            ),
            {"k": k_target, "kk": k_other_kb, "c": case_id},
        )

    async with factory() as db:
        await set_tenant(db, 901)
        out = await resolve_gap(
            GapResolveRequest(
                query_text="  HOW   Export? ",  # different casing/spacing than any stored row
                gap_type="content",
                language="en",
                diagnosis="missing",
                nearest_kb_slug="kb-a",
            ),
            perms=make_perms(org_id=901),
            db=db,
        )
    assert out.resolved == 3  # both spellings and the second diagnosis
    async with admin.connect() as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT diagnosis, nearest_kb_slug, resolved_at IS NOT NULL AS closed "
                    "FROM portal_retrieval_gaps WHERE org_id=901 AND support_case_id IS NOT NULL "
                    "AND question_key IN (:k,:kk) ORDER BY diagnosis, nearest_kb_slug"
                ),
                {"k": k_target, "kk": k_other_kb},
            )
        )
    by = {(r[0], r[1]): r[2] for r in rows}
    assert by[("missing", "kb-a")] is True
    assert by[("incomplete", "kb-a")] is True  # same need, closed with the group
    assert by[("missing", "kb-b")] is False  # another KB is its own group


async def test_manual_close_of_support_group_requires_kb_selector(pg) -> None:
    from fastapi import HTTPException

    from app.api.app_gaps import GapResolveRequest, resolve_gap
    from tests.conftest import make_perms

    _, factory, _, _ = pg
    async with factory() as db:
        await set_tenant(db, 901)
        with pytest.raises(HTTPException) as exc:
            await resolve_gap(
                GapResolveRequest(query_text="x", gap_type="content", language="en", diagnosis="missing"),
                perms=make_perms(org_id=901),
                db=db,
            )
    assert exc.value.status_code == 422


async def test_reconcile_deletes_out_of_scope_only(pg) -> None:
    admin, factory, cid, _ = pg
    await _upsert(factory, cid, _payload(external_id="keep"))
    await _upsert(factory, cid, _payload(external_id="drop"))
    assert (await _counts(admin))[0] == 2
    async with factory() as db:
        await set_tenant(db, 901)
        deleted = await svc.reconcile_support_cases(db, org_id=901, connector_id=cid, external_ids=["keep"])
    assert deleted == 1
    async with admin.connect() as conn:
        remaining = [
            r[0] for r in await conn.execute(text("SELECT external_id FROM portal_support_cases WHERE org_id=901"))
        ]
    assert remaining == ["keep"]


async def test_reconcile_blocked_when_feature_disabled(pg) -> None:
    """Reconcile is destructive, so it is gated on the same locked policy: a
    tenant whose knowledge_gaps unlock was revoked cannot have a stale connector
    import phantom-delete its cases."""
    from fastapi import HTTPException

    admin, factory, cid, _ = pg
    await _upsert(factory, cid, _payload(external_id="keep"))
    await _upsert(factory, cid, _payload(external_id="drop"))
    async with admin.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET platform_unlocked_features=ARRAY[]::text[] WHERE id=901"))

    async with factory() as db:
        await set_tenant(db, 901)
        with pytest.raises(HTTPException) as exc:
            await svc.reconcile_support_cases(db, org_id=901, connector_id=cid, external_ids=["keep"])
    assert exc.value.status_code == 403 and exc.value.detail["error_code"] == "feature_not_unlocked"
    assert (await _counts(admin))[0] == 2  # nothing deleted


# --------------------------------------------------------------------------- #
# Human review of analysis (SPEC-RAG-SUPPORT-GAP) — real-PG serialization,
# persistence, previous-revision retention and RLS isolation.
# --------------------------------------------------------------------------- #

_ANALYSIS = [
    {"question": "Q0", "diagnosis": "missing"},
    {"question": "Q1", "diagnosis": "uncertain"},
    {"question": "Q2", "diagnosis": "covered"},
]


async def _seed_case(
    admin: AsyncEngine,
    *,
    org_id: int = 901,
    kb_slug: str = "kb-a",
    external_id: str = "rev-1",
    analysis: list | None = _ANALYSIS,
    content_hash: str = "h" * 64,
    version: str | None = "v7",
    status: str = "analyzed",
    subject: str = "Login",
) -> int:
    payload = {"subject": subject, "messages": [{"medium": "email"}, {"medium": "call"}]}
    async with admin.begin() as conn:
        return (
            await conn.execute(
                text(
                    "INSERT INTO portal_support_cases "
                    "(org_id,kb_slug,source,account_id,external_id,created_by,payload,content_hash,status,"
                    "analysis_version,analysis) VALUES "
                    "(:o,:kb,'hubspot','acc',:e,'u',CAST(:p AS jsonb),:h,:s,:v,CAST(:a AS jsonb)) RETURNING id"
                ),
                {
                    "o": org_id,
                    "kb": kb_slug,
                    "e": external_id,
                    "p": json.dumps(payload),
                    "h": content_hash,
                    "s": status,
                    "v": version,
                    "a": None if analysis is None else json.dumps(analysis),
                },
            )
        ).scalar_one()


def _kb(slug: str = "kb-a"):
    return types.SimpleNamespace(slug=slug, owner_type="org")


async def _review(
    factory,
    *,
    case_id: int,
    index: int,
    revision: str,
    org_id: int = 901,
    user_id: str = "rv-1",
    decision: str = "correct",
    corrected_diagnosis: str | None = None,
):
    from app.api.app_support_cases import FindingReviewRequest, review_finding
    from tests.conftest import make_perms

    async with factory() as db:
        await set_tenant(db, org_id)
        return await review_finding(
            case_id=case_id,
            finding_index=index,
            body=FindingReviewRequest(
                analysis_revision=revision, decision=decision, corrected_diagnosis=corrected_diagnosis, note="ok"
            ),
            kb=_kb("kb-a" if org_id == 901 else "kb-b"),
            perms=make_perms(org_id=org_id, user_id=user_id),
            db=db,
        )


async def _read_reviews(admin: AsyncEngine, case_id: int) -> dict:
    async with admin.connect() as conn:
        row = (
            await conn.execute(text("SELECT reviews FROM portal_support_cases WHERE id=:i"), {"i": case_id})
        ).scalar_one()
    return row or {}


async def test_review_persists_and_survives_a_separate_read(pg) -> None:
    from app.services.support_case_reviews import compute_analysis_revision, review_key, reviews_for_current_revision

    admin, factory, _cid, _ = pg
    case_id = await _seed_case(admin)
    rev = compute_analysis_revision(content_hash="h" * 64, analysis_version="v7", analysis=_ANALYSIS)

    out = await _review(factory, case_id=case_id, index=1, revision=rev)
    assert out.analysis_revision == rev
    assert out.review.reviewed_by == "rv-1"  # server-derived identity

    # A fresh session sees the persisted review under the current revision only.
    async with factory() as db:
        await set_tenant(db, 901)
        row = (
            await db.execute(text("SELECT reviews, analysis FROM portal_support_cases WHERE id=:i"), {"i": case_id})
        ).first()
    reviews, analysis = row[0], row[1]
    surfaced = reviews_for_current_revision(reviews, rev, len(_ANALYSIS))
    assert surfaced[1]["decision"] == "correct" and surfaced[0] is None
    assert reviews[review_key(rev, 1)]["reviewed_by"] == "rv-1"
    assert analysis == _ANALYSIS  # the machine analysis is never mutated by a review


async def test_concurrent_distinct_finding_reviews_are_both_preserved(pg) -> None:
    from app.services.support_case_reviews import compute_analysis_revision, review_key

    admin, factory, _cid, _ = pg
    case_id = await _seed_case(admin)
    rev = compute_analysis_revision(content_hash="h" * 64, analysis_version="v7", analysis=_ANALYSIS)

    # Two reviewers, two different findings, at once: the per-case row lock
    # serialises the copy-on-write so neither overwrites the other's entry.
    await asyncio.gather(
        _review(factory, case_id=case_id, index=0, revision=rev, user_id="a"),
        _review(factory, case_id=case_id, index=1, revision=rev, user_id="b", decision="incorrect"),
    )
    reviews = await _read_reviews(admin, case_id)
    assert reviews[review_key(rev, 0)]["reviewed_by"] == "a"
    assert reviews[review_key(rev, 1)]["reviewed_by"] == "b"


async def test_previous_revision_review_is_retained_after_reanalysis(pg) -> None:
    from app.services.support_case_reviews import compute_analysis_revision, review_key, reviews_for_current_revision

    admin, factory, _cid, _ = pg
    case_id = await _seed_case(admin)
    rev_a = compute_analysis_revision(content_hash="h" * 64, analysis_version="v7", analysis=_ANALYSIS)
    await _review(factory, case_id=case_id, index=0, revision=rev_a)

    # Reanalysis: the evidence hash and analysis move, so the revision changes.
    new_analysis = [{"question": "Q0", "diagnosis": "incomplete"}]
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE portal_support_cases SET content_hash=:h, analysis=CAST(:a AS jsonb) WHERE id=:i"),
            {"h": "g" * 64, "a": json.dumps(new_analysis), "i": case_id},
        )
    rev_b = compute_analysis_revision(content_hash="g" * 64, analysis_version="v7", analysis=new_analysis)
    assert rev_b != rev_a
    await _review(factory, case_id=case_id, index=0, revision=rev_b, decision="incorrect")

    reviews = await _read_reviews(admin, case_id)
    # Both keys retained; the detail view (current revision) only surfaces rev_b.
    assert review_key(rev_a, 0) in reviews and review_key(rev_b, 0) in reviews
    surfaced = reviews_for_current_revision(reviews, rev_b, len(new_analysis))
    assert surfaced[0]["decision"] == "incorrect"


async def test_stale_revision_review_is_409_and_writes_nothing(pg) -> None:
    from fastapi import HTTPException

    admin, factory, _cid, _ = pg
    case_id = await _seed_case(admin)
    with pytest.raises(HTTPException) as exc:
        await _review(factory, case_id=case_id, index=0, revision="stale-not-current")
    assert exc.value.status_code == 409
    assert await _read_reviews(admin, case_id) == {}


async def test_reviewer_cannot_write_another_orgs_case(pg) -> None:
    from fastapi import HTTPException

    from app.services.support_case_reviews import compute_analysis_revision

    admin, factory, _cid, _ = pg
    case_id = await _seed_case(admin, org_id=901)
    rev = compute_analysis_revision(content_hash="h" * 64, analysis_version="v7", analysis=_ANALYSIS)
    # Org 902 (also full telemetry) may not reach org 901's case: RLS + the
    # explicit org predicate both hide it, so the lookup 404s and nothing writes.
    with pytest.raises(HTTPException) as exc:
        await _review(factory, case_id=case_id, index=0, revision=rev, org_id=902)
    assert exc.value.status_code == 404
    assert await _read_reviews(admin, case_id) == {}


async def test_review_and_flag_disable_serialize(pg) -> None:
    """The review write locks the org-policy row FOR SHARE before the case, so a
    concurrent knowledge_gaps disable serialises against it. The outcome is never
    torn: either the review committed (feature seen unlocked under the lock) or it
    403'd and wrote nothing — never a verdict persisted after a revoke it saw."""
    from fastapi import HTTPException

    from app.services.support_case_reviews import compute_analysis_revision, review_key

    admin, factory, _cid, _ = pg
    case_id = await _seed_case(admin)
    rev = compute_analysis_revision(content_hash="h" * 64, analysis_version="v7", analysis=_ANALYSIS)

    async def _disable() -> None:
        async with admin.begin() as conn:
            await conn.execute(text("UPDATE portal_orgs SET platform_unlocked_features=ARRAY[]::text[] WHERE id=901"))

    outcome, _ = await asyncio.gather(
        _review(factory, case_id=case_id, index=0, revision=rev),
        _disable(),
        return_exceptions=True,
    )
    reviews = await _read_reviews(admin, case_id)
    if isinstance(outcome, HTTPException):
        assert outcome.status_code == 403 and reviews == {}
    else:
        assert reviews[review_key(rev, 0)]["decision"] == "correct"


async def test_list_includes_every_status_and_outcome(pg) -> None:
    from app.api.app_support_cases import list_support_cases
    from tests.conftest import make_perms

    admin, factory, _cid, _ = pg
    covered_only = [{"question": "Q", "diagnosis": "covered"}]
    ids = {
        "gap": await _seed_case(admin, external_id="c-gap", analysis=_ANALYSIS),
        "nogap": await _seed_case(admin, external_id="c-nogap", analysis=covered_only),
        "failed": await _seed_case(admin, external_id="c-failed", analysis=None, version=None, status="failed"),
        "pending": await _seed_case(admin, external_id="c-pending", analysis=None, version=None, status="pending"),
    }
    async with factory() as db:
        await set_tenant(db, 901)
        out = await list_support_cases(kb=_kb(), limit=25, offset=0, perms=make_perms(org_id=901), db=db)

    by_id = {c.id: c for c in out.cases}
    assert out.total == 4 and set(ids.values()) <= set(by_id)
    # Newest import first: seeded in order, so ids descend.
    assert [c.id for c in out.cases] == sorted(by_id, reverse=True)
    assert (by_id[ids["gap"]].question_count, by_id[ids["gap"]].uncertain_count) == (3, 1)
    assert by_id[ids["nogap"]].question_count == 1 and by_id[ids["nogap"]].uncertain_count == 0
    assert by_id[ids["failed"]].question_count == 0
    assert by_id[ids["pending"]].question_count == 0
    assert sorted(by_id[ids["gap"]].mediums) == ["call", "email"]


# --------------------------------------------------------------------------- #
# Review-driven gap visibility — the human override, proven on real rows
# --------------------------------------------------------------------------- #


async def _apply_visibility(
    factory, case_id: int, index: int, decision: str, corrected: str | None, org: int = 901, reviewer: int = 7
) -> None:
    from app.models.support_cases import PortalSupportCase

    async with factory() as db:
        await set_tenant(db, org)
        case = (
            await db.execute(select(PortalSupportCase).where(PortalSupportCase.id == case_id).with_for_update())
        ).scalar_one()
        await svc.apply_review_visibility(
            db,
            case=case,
            finding=case.analysis[index],
            decision=decision,
            corrected_diagnosis=corrected,
            reviewer_user_id=reviewer,
        )
        await db.commit()


async def _gap_rows(admin: AsyncEngine, case_id: int, org: int = 901) -> list:
    async with admin.connect() as conn:
        return list(
            await conn.execute(
                text(
                    "SELECT diagnosis, resolved_by, (resolved_at IS NOT NULL) AS closed, question_key, query_text "
                    "FROM portal_retrieval_gaps WHERE org_id=:o AND support_case_id=:c ORDER BY id"
                ),
                {"o": org, "c": case_id},
            )
        )


async def test_review_dismiss_suppresses_the_finding_gap(pg) -> None:
    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())  # one open 'missing' finding
    await _apply_visibility(factory, r.case_id, 0, "incorrect", None)  # incorrect + no correction = dismiss
    rows = await _gap_rows(admin, r.case_id)
    assert len(rows) == 1
    assert (rows[0].closed, rows[0].resolved_by) == (True, "review")


async def test_review_correct_restores_a_dismissed_finding(pg) -> None:
    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())
    await _apply_visibility(factory, r.case_id, 0, "incorrect", None)  # dismiss
    await _apply_visibility(factory, r.case_id, 0, "correct", None)  # restore
    rows = await _gap_rows(admin, r.case_id)
    assert (rows[0].closed, rows[0].resolved_by) == (False, None)


async def test_review_correction_to_non_actionable_suppresses(pg) -> None:
    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())
    await _apply_visibility(factory, r.case_id, 0, "incorrect", "covered")
    rows = await _gap_rows(admin, r.case_id)
    assert (rows[0].closed, rows[0].resolved_by) == (True, "review")


async def test_review_correction_changes_the_diagnosis_but_not_the_group(pg) -> None:
    """A reviewer correcting 'missing' to 'outdated' says the gap was detected
    differently, not that the customer needs something else, so the row keeps
    the group it is in (SPEC-RAG-GAP-GROUPING: diagnosis is not part of the
    grouping key). Before, the correction moved it into a group of its own."""
    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())
    before = (await _gap_rows(admin, r.case_id))[0].question_key
    await _apply_visibility(factory, r.case_id, 0, "incorrect", "outdated")
    rows = await _gap_rows(admin, r.case_id)
    assert rows[0].diagnosis == "outdated" and rows[0].closed is False
    assert rows[0].question_key == before
    assert "outdated" not in rows[0].question_key


async def test_review_promotes_an_uncertain_finding_into_a_candidate(pg) -> None:
    admin, factory, cid, analyzer = pg
    analyzer.analyze_support_case = AsyncMock(
        return_value=[{"question": "Odd one?", "diagnosis": "uncertain", "gap_type": None, "language": "en"}]
    )
    r = await _upsert(factory, cid, _payload())
    assert await _counts(admin) == (1, 0)  # uncertain produced no gap row
    await _apply_visibility(factory, r.case_id, 0, "incorrect", "missing")  # promote
    rows = await _gap_rows(admin, r.case_id)
    assert len(rows) == 1
    assert rows[0].diagnosis == "missing" and rows[0].closed is False


async def test_review_leaves_a_content_closed_row_closed(pg) -> None:
    """Content closure (manual / rescorer) is separate from a review dismissal, so
    a 'correct' verdict must not resurface a finding whose answer was written."""
    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE portal_retrieval_gaps SET resolved_at=now(), resolved_by='manual' WHERE support_case_id=:c"),
            {"c": r.case_id},
        )
    await _apply_visibility(factory, r.case_id, 0, "correct", None)  # visible/restore
    rows = await _gap_rows(admin, r.case_id)
    assert (rows[0].closed, rows[0].resolved_by) == (True, "manual")  # content closure untouched


# --------------------------------------------------------------------------- #
# KB-change reanalysis — a close survives, a genuinely covered finding drops
# --------------------------------------------------------------------------- #


async def test_force_reanalysis_preserves_a_close_for_a_still_missing_finding(pg) -> None:
    """A forced reanalysis after a KB change must not redeliver a manually-closed
    finding whose answer was not actually written: if the analyzer still returns
    the same question, the prior close carries over (contract: manual close
    remains until content is genuinely covered)."""
    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())  # one 'missing' finding, open
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE portal_retrieval_gaps SET resolved_at=now(), resolved_by='manual' WHERE support_case_id=:c"),
            {"c": r.case_id},
        )
    # KB changed but the same question is still missing -> analyzer returns it again.
    await _upsert(factory, cid, _payload(), force=True)
    rows = await _gap_rows(admin, r.case_id)
    assert len(rows) == 1
    assert (rows[0].closed, rows[0].resolved_by) == (True, "manual")  # the close survived


async def test_force_reanalysis_drops_a_now_covered_finding(pg) -> None:
    """When the KB change genuinely covers the question, the analyzer stops
    returning it and the finding leaves the inbox (no stale open row)."""
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload())
    assert await _counts(admin) == (1, 1)
    analyzer.analyze_support_case = AsyncMock(
        return_value=[{"question": "Reset 2FA?", "diagnosis": "covered", "gap_type": None, "language": "en"}]
    )
    await _upsert(factory, cid, _payload(), force=True)
    assert await _counts(admin) == (1, 0)  # covered -> no gap row


# --------------------------------------------------------------------------- #
# Knowledge update -> support-case reanalysis (rescore trigger)
# --------------------------------------------------------------------------- #


async def _reanalyse_scoped(factory, kb_slug: str | None, org: int = 901) -> tuple[int, int]:
    from app.services import gap_rescorer

    async with factory() as db:
        return await gap_rescorer.reanalyse_scoped_support_cases(org, f"zit-{org}", kb_slug, db)


async def test_rescore_reanalyses_scoped_support_cases(pg) -> None:
    _, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload(external_id="a"))
    await _upsert(factory, cid, _payload(external_id="b"))
    before = analyzer.analyze_support_case.await_count
    ok, failed = await _reanalyse_scoped(factory, "kb-a")
    assert (ok, failed) == (2, 0)
    assert analyzer.analyze_support_case.await_count == before + 2  # forced, both re-run


async def test_rescore_support_reanalysis_is_gated_on_full_telemetry(pg) -> None:
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload())
    async with admin.begin() as conn:
        await conn.execute(text("UPDATE portal_orgs SET telemetry_level='shadow' WHERE id=901"))
    before = analyzer.analyze_support_case.await_count
    assert await _reanalyse_scoped(factory, "kb-a") == (0, 0)
    assert analyzer.analyze_support_case.await_count == before  # nothing re-run


async def test_rescore_support_reanalysis_is_kb_scoped(pg) -> None:
    _, factory, cid, _ = pg
    await _upsert(factory, cid, _payload())  # kb-a
    assert await _reanalyse_scoped(factory, "some-other-kb") == (0, 0)  # different KB -> no candidates


async def test_rescore_support_reanalysis_counts_failures_and_preserves(pg) -> None:
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _payload())
    analyzer.analyze_support_case = AsyncMock(side_effect=RuntimeError("judge down"))
    assert await _reanalyse_scoped(factory, "kb-a") == (0, 1)  # visible failure, not a silent success
    status, analysis = await _analysis(admin)
    assert status == "analyzed" and analysis[0]["question"] == "Reset 2FA?"  # old analysis preserved
    assert await _counts(admin) == (1, 1)


# --------------------------------------------------------------------------- #
# Grouping integration — fold a case's finding into an existing open group
# --------------------------------------------------------------------------- #


def _fake_grouping(group_key: str):
    """Inject a stand-in grouping module that folds any 'missing' finding into
    ``group_key`` when it is among the candidates. The real module imports the
    analyzer internals, which the stub analyzer here does not provide, so the
    store's lazy import picks this up from ``sys.modules`` instead."""
    module = types.ModuleType("app.services.support_gap_grouping")

    async def group_findings(findings: list[dict], candidates: list[dict]) -> list[dict]:
        out = copy.deepcopy(findings)
        keys = {c["question_key"] for c in candidates}
        for f in out:
            if group_key in keys and f.get("diagnosis") == "missing":
                f["group_question_key"] = group_key
        return out

    module.group_findings = group_findings  # type: ignore[attr-defined]
    return patch.dict(sys.modules, {"app.services.support_gap_grouping": module})


async def test_grouping_folds_finding_into_group_and_preserves_original_question(pg) -> None:
    """A verified merge stamps the new finding with the existing group's key, so it
    folds into one group while the row keeps the case's own question verbatim."""
    admin, factory, cid, analyzer = pg
    questions = iter(["How do I export data?", "Exporting my account data?"])  # same need, different wording

    async def _analyze(**_: object) -> list[dict]:
        return [{"question": next(questions), "diagnosis": "missing", "gap_type": "hard", "language": "en"}]

    analyzer.analyze_support_case = AsyncMock(side_effect=_analyze)
    ra = await _upsert(factory, cid, _payload(external_id="A"))
    key_a = (await _gap_rows(admin, ra.case_id))[0].question_key

    with _fake_grouping(key_a):
        rb = await _upsert(factory, cid, _payload(external_id="B"))

    rows_b = await _gap_rows(admin, rb.case_id)
    assert len(rows_b) == 1
    assert rows_b[0].question_key == key_a  # folded into A's group despite different wording
    assert rows_b[0].query_text == "Exporting my account data?"  # the case's own question is preserved


async def test_grouping_distinct_case_count_and_resolve_by_group_key(pg) -> None:
    admin, factory, cid, analyzer = pg
    questions = iter(["How do I export data?", "Exporting my account data?"])

    async def _analyze(**_: object) -> list[dict]:
        return [{"question": next(questions), "diagnosis": "missing", "gap_type": "hard", "language": "en"}]

    analyzer.analyze_support_case = AsyncMock(side_effect=_analyze)
    ra = await _upsert(factory, cid, _payload(external_id="A"))
    key_a = (await _gap_rows(admin, ra.case_id))[0].question_key

    with _fake_grouping(key_a):
        await _upsert(factory, cid, _payload(external_id="B"))

    async with admin.connect() as conn:
        distinct = (
            await conn.execute(
                text(
                    "SELECT count(DISTINCT support_case_id) FROM portal_retrieval_gaps "
                    "WHERE org_id=901 AND question_key=:k"
                ),
                {"k": key_a},
            )
        ).scalar_one()
    assert distinct == 2  # two cases fold into one group; the unique-case count is 2

    # Closing by the persisted group key closes both, though their wording differs.
    from app.api.app_gaps import GapResolveRequest, resolve_gap
    from tests.conftest import make_perms

    async with factory() as db:
        await set_tenant(db, 901)
        out = await resolve_gap(
            GapResolveRequest(query_text="ignored", gap_type="content", group_key=key_a),
            perms=make_perms(org_id=901),
            db=db,
        )
    assert out.resolved == 2


async def test_force_reanalysis_close_survives_a_regrouping(pg) -> None:
    """Regression: a reanalysis that folds a still-missing finding under a DIFFERENT
    group key must still preserve its manual close. Closures are matched on the
    finding's verbatim question, not the group-folded key which can move."""
    admin, factory, cid, _ = pg
    # A second case gives an open candidate group, so grouping runs on the reanalysis.
    await _upsert(factory, cid, _payload(external_id="B", text_value="other case"))
    ra = await _upsert(factory, cid, _payload(external_id="A"))
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE portal_retrieval_gaps SET resolved_at=now(), resolved_by='manual' WHERE support_case_id=:c"),
            {"c": ra.case_id},
        )
    orig_key = (await _gap_rows(admin, ra.case_id))[0].question_key

    module = types.ModuleType("app.services.support_gap_grouping")

    async def group_findings(findings: list[dict], candidates: list[dict]) -> list[dict]:
        out = copy.deepcopy(findings)
        for f in out:
            if f.get("diagnosis") == "missing":
                f["group_question_key"] = "regrouped-key"  # a key different from the finding's own
        return out

    module.group_findings = group_findings  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"app.services.support_gap_grouping": module}):
        await _upsert(factory, cid, _payload(external_id="A"), force=True)

    rows = await _gap_rows(admin, ra.case_id)
    assert len(rows) == 1
    assert rows[0].question_key == "regrouped-key" != orig_key  # the group key did move
    assert (rows[0].closed, rows[0].resolved_by) == (True, "manual")  # yet the close survived


# --------------------------------------------------------------------------- #
# Trusted speaker-role override — survives a provider re-import (#2)
# --------------------------------------------------------------------------- #


def _call_pg_payload(
    external_id: str = "call-1", text_value: str = "I cannot log in", role: str = "unknown"
) -> SupportCasePayload:
    p = SupportCasePayload(
        source="hubspot",
        account_id="12345",
        external_id=external_id,
        subject="Login",
        complete=True,
        messages=[
            SupportCaseMessage(
                id="seg-0",
                kind="message",
                role=role,  # type: ignore[arg-type]
                text=text_value,
                medium="call",
                speaker_id="spk-1",
                start_seconds=0.0,
                end_seconds=2.0,
            )
        ],
    )
    p.bind_kb("kb-a")
    return p


async def _upsert_override(
    factory, cid: str, payload: SupportCasePayload, roles: dict, reviewer: str, expected_hash: str, org_id: int = 901
) -> svc.UpsertResult:
    """The trusted store-level correction path: force a reanalysis with a
    server-owned role override (what the role-correction route passes)."""
    async with factory() as db:
        await set_tenant(db, org_id)
        return await svc.upsert_support_case(
            db,
            org_id=org_id,
            zitadel_org_id=f"zit-{org_id}",
            telemetry_level="full",
            connector_id=cid,
            created_by="u-901",
            kb_slug="kb-a",
            payload=payload,
            force_reanalysis=True,
            expected_content_hash=expected_hash,
            role_overrides=roles,
            role_override_reviewer=reviewer,
        )


async def test_role_override_persists_across_provider_reimport(pg) -> None:
    """A human corrects unknown->customer; the connector then re-imports the ORIGINAL
    unknown payload. The override lives in server-owned reviews and is overlaid before
    hashing, so the effective evidence is unchanged: the customer role survives, the
    re-import is a no-op, and no duplicate finding appears."""
    admin, factory, cid, analyzer = pg
    await _upsert(factory, cid, _call_pg_payload())  # role unknown
    async with admin.connect() as conn:
        hash1 = (await conn.execute(text("SELECT content_hash FROM portal_support_cases"))).scalar_one()

    r2 = await _upsert_override(factory, cid, _call_pg_payload(), {"seg-0": "customer"}, "rev-1", hash1)
    assert r2.status == "analyzed"
    async with admin.connect() as conn:
        row = (await conn.execute(text("SELECT payload, reviews, content_hash FROM portal_support_cases"))).first()
    payload, reviews, hash2 = row
    assert payload["messages"][0]["role"] == "customer"
    override = reviews[svc.ROLE_OVERRIDE_KEY]["seg-0"]
    assert (override["role"], override["provider_role"], override["reviewed_by"]) == ("customer", "unknown", "rev-1")
    assert hash2 != hash1  # the correction moved the effective evidence

    before = analyzer.analyze_support_case.await_count
    r3 = await _upsert(factory, cid, _call_pg_payload())  # provider re-imports the ORIGINAL unknown roles
    assert (r3.status, r3.changed) == ("analyzed", False)  # same effective evidence -> no-op
    assert analyzer.analyze_support_case.await_count == before  # not re-analysed
    async with admin.connect() as conn:
        role_now = (
            await conn.execute(text("SELECT payload->'messages'->0->>'role' FROM portal_support_cases"))
        ).scalar_one()
    assert role_now == "customer"  # the human role survived the re-import
    assert await _counts(admin) == (1, 1)  # stable, no duplicate findings


async def test_changed_text_under_same_id_does_not_inherit_override(pg) -> None:
    """A correction pins to the exact segment identity: if the provider re-imports
    CHANGED text under the same message id, the stale human role must NOT carry over."""
    admin, factory, cid, _ = pg
    await _upsert(factory, cid, _call_pg_payload())
    async with admin.connect() as conn:
        hash1 = (await conn.execute(text("SELECT content_hash FROM portal_support_cases"))).scalar_one()
    await _upsert_override(factory, cid, _call_pg_payload(), {"seg-0": "customer"}, "rev-1", hash1)

    r = await _upsert(factory, cid, _call_pg_payload(text_value="a totally different question"))
    assert r.changed is True
    async with admin.connect() as conn:
        role_now = (
            await conn.execute(text("SELECT payload->'messages'->0->>'role' FROM portal_support_cases"))
        ).scalar_one()
    assert role_now == "unknown"  # changed segment keeps the provider role


async def test_injected_provider_metadata_is_not_a_trusted_override(pg) -> None:
    """Provider-controlled payload metadata that forges role-override/review shapes
    must NOT become trusted state: overrides are read only from the server-owned
    reviews column, so an injected payload never relabels a segment or forges audit."""
    admin, factory, cid, _ = pg
    payload = _call_pg_payload()
    payload.metadata = {
        "_role_overrides": {"seg-0": {"role": "customer", "provider_role": "customer"}},
        "role_reviews": [{"reviewed_by": "attacker"}],
    }
    r = await _upsert(factory, cid, payload)
    assert r.status == "analyzed"
    async with admin.connect() as conn:
        role_now, reviews = (
            await conn.execute(text("SELECT payload->'messages'->0->>'role', reviews FROM portal_support_cases"))
        ).first()
    assert role_now == "unknown"  # provider metadata never relabels a segment
    assert reviews is None or svc.ROLE_OVERRIDE_KEY not in reviews  # no trusted override forged


@pytest.mark.parametrize(
    "machine,corrected,closed",
    [("uncertain", "missing", False), ("missing", "incomplete", False), ("uncertain", "missing", True)],
)
async def test_identical_reanalysis_preserves_current_human_correction(pg, machine, corrected, closed) -> None:
    from app.services.support_case_reviews import compute_analysis_revision, review_key

    admin, factory, cid, analyzer = pg
    analyzer.analyze_support_case.return_value = [{"question": "Reset 2FA?", "diagnosis": machine, "language": "en"}]
    result = await _upsert(factory, cid, _payload())
    async with admin.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT content_hash, analysis_version, analysis FROM portal_support_cases WHERE id=:id"),
                {"id": result.case_id},
            )
        ).one()
    revision = compute_analysis_revision(
        content_hash=row.content_hash, analysis_version=row.analysis_version, analysis=row.analysis
    )
    await _review(
        factory, case_id=result.case_id, index=0, revision=revision, decision="incorrect", corrected_diagnosis=corrected
    )
    if closed:
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE portal_retrieval_gaps SET resolved_at=now(), resolved_by='manual' WHERE support_case_id=:id"
                ),
                {"id": result.case_id},
            )
    await _upsert(factory, cid, _payload(), force=True)
    rows = await _gap_rows(admin, result.case_id)
    assert len(rows) == 1 and rows[0].diagnosis == corrected and rows[0].closed == closed
    if closed:
        assert rows[0].resolved_by == "manual"
    assert (await _read_reviews(admin, result.case_id))[review_key(revision, 0)]["corrected_diagnosis"] == corrected


async def test_seven_day_purge_keeps_case_findings_and_still_expires_query_text(pg) -> None:
    """The inbox's case evidence survives its retention job; chat text does not.

    Until 2026-09-23 the 7-day TTL deleted every readable gap row, so a
    case-backed finding disappeared a week after import while its support case
    stayed, and no theme could show a trend across weeks. Rows derived from a
    chat query keep the 7-day fence (docs/privacy/telemetry-modes.md), including
    the one a human reviewer filed.
    """
    from app.services.telemetry_purge import EXPIRED_RAW_TELEMETRY_GAPS_SQL

    admin, factory, cid, _ = pg
    result = await _upsert(factory, cid, _payload())
    async with admin.begin() as conn:
        # The finding is as old as the raw rows, so "it survived" cannot be an
        # artifact of its fresh insert timestamp.
        await conn.execute(
            text("UPDATE portal_retrieval_gaps SET occurred_at = now() - interval '30 days' WHERE support_case_id=:c"),
            {"c": result.case_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO portal_retrieval_gaps
                    (org_id, user_id, query_text, gap_type, occurred_at, caller_client_id)
                VALUES
                    (901, 'u1', 'raw chat question', 'soft', now() - interval '30 days', 'widget-chat'),
                    (901, 'u1', '[REDACTED:shadow]', 'soft', now() - interval '30 days', 'widget-chat'),
                    (901, 'u1', 'question a reviewer filed', 'hard', now() - interval '30 days', 'human-review'),
                    (901, 'u1', 'fresh chat question', 'soft', now(), 'widget-chat')
                """
            )
        )
        expired = (
            (
                await conn.execute(
                    text(EXPIRED_RAW_TELEMETRY_GAPS_SQL.replace("public.", f"{_SCHEMA}.")),
                    {"cutoff": datetime.now(UTC) - timedelta(days=7), "chunk_size": 100},
                )
            )
            .scalars()
            .all()
        )
        surviving = (
            (
                await conn.execute(
                    text("SELECT query_text FROM portal_retrieval_gaps WHERE id <> ALL(CAST(:ids AS bigint[]))"),
                    {"ids": list(expired)},
                )
            )
            .scalars()
            .all()
        )
        expired_text = (
            (
                await conn.execute(
                    text("SELECT query_text FROM portal_retrieval_gaps WHERE id = ANY(CAST(:ids AS bigint[]))"),
                    {"ids": list(expired)},
                )
            )
            .scalars()
            .all()
        )

    case_finding = (await _gap_rows(admin, result.case_id))[0]
    assert case_finding.diagnosis == "missing"
    assert sorted(expired_text) == ["question a reviewer filed", "raw chat question"]
    assert sorted(surviving) == sorted([case_finding.query_text, "[REDACTED:shadow]", "fresh chat question"])


async def test_inbox_lists_only_rows_that_show_the_visitor_was_not_helped(pg) -> None:
    """A low retrieval score on its own does not show that a visitor went
    unhelped, and a redacted row carries no question anyone could act on; on
    the first tenant measured those two made up most open rows, including the
    largest groups. The inbox keeps what does show it: a quality-judge or human-review
    verdict, or a search that found nothing at all."""
    from app.api.app_gaps import list_gaps
    from tests.conftest import make_perms

    admin, factory, _cid, _analyzer = pg
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO portal_retrieval_gaps (org_id,user_id,query_text,gap_type,caller_client_id,language) "
                "VALUES "
                "(901,'u','Hoe laat open?','soft','widget-chat','nl'),"
                "(901,'u','[REDACTED:legacy]','soft','widget-chat','nl'),"
                "(901,'u','[REDACTED:legacy]','hard','human-review','nl'),"
                "(901,'u','Kan niet bellen','soft','quality-judge','nl'),"
                "(901,'u','Nummer instellen','soft','human-review','nl'),"
                "(901,'u','Faxen','hard','widget-chat','nl')"
            )
        )

    async with factory() as db:
        await set_tenant(db, 901)
        out = await list_gaps(
            days=30,
            gap_type=None,
            language=None,
            taxonomy_node_id=None,
            limit=50,
            include_resolved=False,
            perms=make_perms(org_id=901),
            db=db,
        )

    assert sorted(g.query_text for g in out.gaps) == ["Faxen", "Kan niet bellen", "Nummer instellen"]


async def test_folding_a_group_moves_every_row_that_shares_its_key(pg) -> None:
    """A fold merges one group into another, so every row on the folded key
    moves along. Moving only the asking row split identical questions over two
    groups, and a one-off regroup over existing rows would strand whatever an
    earlier fold had already put on the key being moved."""
    import contextlib

    from app.services.gap_events import fold_into_open_group
    from app.services.support_cases import _question_key

    admin, factory, _cid, _analyzer = pg
    k_moved = _question_key(question="Nummer instellen", language="nl", kb_slug="kb-a", audience=None)
    k_target = _question_key(question="Hoe stel ik mijn nummer in?", language="nl", kb_slug="kb-a", audience=None)
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO portal_retrieval_gaps "
                "(org_id,user_id,query_text,gap_type,caller_client_id,language,nearest_kb_slug,question_key) VALUES "
                "(901,'u','Nummer instellen','soft','human-review','nl','kb-a',:m),"
                "(901,'u','nummer  instellen','hard','human-review','nl','kb-a',:m),"
                "(901,'u','Hoe stel ik mijn nummer in?','soft','quality-judge','nl','kb-a',:t)"
            ),
            {"m": k_moved, "t": k_target},
        )

    @contextlib.asynccontextmanager
    async def _scoped(org_id: int):
        async with factory() as db:
            await set_tenant(db, org_id)
            yield db

    async def _judge(findings: list[dict], candidates: list[dict]) -> list[dict]:
        assert [c["question_key"] for c in candidates] == [k_target]  # its own key is not a candidate
        return [{**findings[0], "group_question_key": k_target}]

    grouping = types.ModuleType("app.services.support_gap_grouping")
    grouping.group_findings = _judge  # type: ignore[attr-defined]
    with (
        patch("app.core.database.tenant_scoped_session", _scoped),
        patch.dict(sys.modules, {"app.services.support_gap_grouping": grouping}),
    ):
        matched = await fold_into_open_group(
            org_id=901,
            kb_slug="kb-a",
            question="Nummer instellen",
            language="nl",
            audience=None,
            base_key=k_moved,
        )

    assert matched == k_target
    async with admin.connect() as conn:
        keys = (
            (await conn.execute(text("SELECT DISTINCT question_key FROM portal_retrieval_gaps WHERE org_id=901")))
            .scalars()
            .all()
        )
    assert keys == [k_target]
