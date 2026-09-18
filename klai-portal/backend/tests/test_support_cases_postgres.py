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
import json
import os
import sys
import types
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

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


async def _upsert(factory, cid: str, payload: SupportCasePayload, org_id: int = 901) -> svc.UpsertResult:
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
        )


# --------------------------------------------------------------------------- #


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


async def test_manual_close_closes_the_whole_question_key_group_only(pg) -> None:
    """#A: a support group is closed by its persisted question_key, so two
    differently cased/spaced cases in one group both close, while another KB or
    diagnosis is untouched. The KB selector is required."""
    from app.api.app_gaps import GapResolveRequest, resolve_gap
    from app.services.support_cases import _question_key
    from tests.conftest import make_perms

    admin, factory, cid, _ = pg
    r = await _upsert(factory, cid, _payload())  # one real case to reference
    case_id = r.case_id
    k_target = _question_key(question="How export?", diagnosis="missing", language="en", kb_slug="kb-a", audience=None)
    k_other_diag = _question_key(
        question="How export?", diagnosis="incomplete", language="en", kb_slug="kb-a", audience=None
    )
    k_other_kb = _question_key(
        question="How export?", diagnosis="missing", language="en", kb_slug="kb-b", audience=None
    )

    async with admin.begin() as conn:
        # Two spellings, one persisted key (the store normalized both the same).
        await conn.execute(
            text(
                "INSERT INTO portal_retrieval_gaps "
                "(org_id,user_id,query_text,gap_type,nearest_kb_slug,language,diagnosis,question_key,support_case_id) "
                "VALUES "
                "(901,'u','How export?','content','kb-a','en','missing',:k,:c),"
                "(901,'u','how  export?','content','kb-a','en','missing',:k,:c),"
                "(901,'u','How export?','content','kb-a','en','incomplete',:kd,:c),"
                "(901,'u','How export?','content','kb-b','en','missing',:kk,:c)"
            ),
            {"k": k_target, "kd": k_other_diag, "kk": k_other_kb, "c": case_id},
        )

    async with factory() as db:
        await set_tenant(db, 901)
        out = await resolve_gap(
            GapResolveRequest(
                query_text="  HOW   Export? ",  # different casing/spacing than either stored row
                gap_type="content",
                language="en",
                diagnosis="missing",
                nearest_kb_slug="kb-a",
            ),
            perms=make_perms(org_id=901),
            db=db,
        )
    assert out.resolved == 2  # both spellings of the target group
    async with admin.connect() as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT diagnosis, nearest_kb_slug, resolved_at IS NOT NULL AS closed "
                    "FROM portal_retrieval_gaps WHERE org_id=901 AND support_case_id IS NOT NULL "
                    "AND question_key IN (:k,:kd,:kk) ORDER BY diagnosis, nearest_kb_slug"
                ),
                {"k": k_target, "kd": k_other_diag, "kk": k_other_kb},
            )
        )
    # incomplete@kb-a and missing@kb-b stay open; only missing@kb-a closed.
    by = {(r[0], r[1]): r[2] for r in rows}
    assert by[("missing", "kb-a")] is True
    assert by[("incomplete", "kb-a")] is False
    assert by[("missing", "kb-b")] is False


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
):
    from app.api.app_support_cases import FindingReviewRequest, review_finding
    from tests.conftest import make_perms

    async with factory() as db:
        await set_tenant(db, org_id)
        return await review_finding(
            case_id=case_id,
            finding_index=index,
            body=FindingReviewRequest(analysis_revision=revision, decision=decision, note="ok"),
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
