"""
Tests for pg_store functions.

SPEC-TI-003-FOLLOWUP-001: pg_store helpers no longer acquire pool
connections themselves -- callers pass the GUC-pinned ``asyncpg.Connection``
in. These unit tests therefore construct a mock conn and assert against
``conn.execute`` / ``conn.fetch`` / ``conn.fetchval`` / ``conn.fetchrow``
directly.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import pg_store

_SENTINEL = 253402300800


def _make_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _tx():
        yield None

    conn.transaction = MagicMock(side_effect=_tx)
    return conn


@pytest.mark.asyncio
async def test_create_artifact_returns_uuid():
    conn = _make_conn()
    artifact_id = await pg_store.create_artifact(
        conn,
        org_id="100000000000000001",
        kb_slug="personal-user456",
        path="note.md",
        provenance_type="observed",
        assertion_mode="factual",
        synthesis_depth=0,
        confidence=None,
        belief_time_start=1705276800,
        belief_time_end=_SENTINEL,
    )
    assert isinstance(artifact_id, str)
    assert len(artifact_id) == 36  # UUID format
    assert artifact_id.count("-") == 4


@pytest.mark.asyncio
async def test_create_artifact_executes_insert():
    conn = _make_conn()
    await pg_store.create_artifact(
        conn,
        org_id="org123",
        kb_slug="docs",
        path="spec.md",
        provenance_type="synthesized",
        assertion_mode="belief",
        synthesis_depth=3,
        confidence="high",
        belief_time_start=1705276800,
        belief_time_end=_SENTINEL,
        user_id="user456",
    )
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    # Verify all values are passed in correct order
    assert "INSERT INTO knowledge.artifacts" in call_args[0]
    values = call_args[1:]
    assert "org123" in values
    assert "docs" in values
    assert "spec.md" in values
    assert "synthesized" in values
    assert "belief" in values
    assert 3 in values
    assert "high" in values
    assert "user456" in values
    assert "synced" in values


@pytest.mark.asyncio
async def test_create_artifact_generates_unique_ids():
    conn = _make_conn()
    id1 = await pg_store.create_artifact(
        conn, "o", "kb", "p1.md", "observed", "factual", 0, None, 0, _SENTINEL
    )
    id2 = await pg_store.create_artifact(
        conn, "o", "kb", "p2.md", "observed", "factual", 0, None, 0, _SENTINEL
    )
    assert id1 != id2


@pytest.mark.asyncio
async def test_create_artifact_accepts_initial_index_status():
    conn = _make_conn()

    await pg_store.create_artifact(
        conn,
        "o",
        "kb",
        "p.md",
        "observed",
        "factual",
        0,
        None,
        0,
        _SENTINEL,
        index_status="pending",
    )

    assert conn.execute.call_args[0][-2] == "pending"


@pytest.mark.asyncio
async def test_create_artifact_inserts_derivations_for_same_org_parents():
    conn = _make_conn()
    parent_one = "11111111-2222-4333-8444-555555555555"
    parent_two = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    conn.fetch = AsyncMock(return_value=[{"id": parent_one}, {"id": parent_two}])

    artifact_id = await pg_store.create_artifact(
        conn,
        "org",
        "kb",
        "child.md",
        "synthesized",
        "factual",
        1,
        "medium",
        0,
        _SENTINEL,
        derived_from=[parent_one, parent_one, parent_two],
    )

    conn.fetch.assert_awaited_once()
    fetch_args = conn.fetch.call_args[0]
    assert "FROM knowledge.artifacts" in fetch_args[0]
    assert fetch_args[1] == "org"
    assert fetch_args[2] == [parent_one, parent_two]

    conn.executemany.assert_awaited_once()
    sql, rows = conn.executemany.call_args[0]
    assert "INSERT INTO knowledge.derivations" in sql
    assert rows == [(artifact_id, parent_one), (artifact_id, parent_two)]


@pytest.mark.asyncio
async def test_create_artifact_rejects_missing_or_cross_org_derived_from_parent():
    conn = _make_conn()
    known_parent = "11111111-2222-4333-8444-555555555555"
    missing_parent = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    conn.fetch = AsyncMock(return_value=[{"id": known_parent}])

    with pytest.raises(ValueError, match="derived_from"):
        await pg_store.create_artifact(
            conn,
            "org",
            "kb",
            "child.md",
            "synthesized",
            "factual",
            1,
            "medium",
            0,
            _SENTINEL,
            derived_from=[known_parent, missing_parent],
        )

    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_create_artifact_with_derived_from_recovers_unique_race_inside_savepoint():
    conn = _make_conn()
    parent_id = "11111111-2222-4333-8444-555555555555"
    winning_child_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    violation = pg_store.asyncpg.UniqueViolationError("simulated unique violation")
    violation.constraint_name = "uq_artifacts_active_path"
    conn.execute = AsyncMock(side_effect=violation)
    conn.fetchval = AsyncMock(return_value=winning_child_id)
    conn.fetch = AsyncMock(return_value=[{"id": parent_id}])

    result = await pg_store.create_artifact(
        conn,
        "org",
        "kb",
        "child.md",
        "synthesized",
        "factual",
        1,
        "medium",
        0,
        _SENTINEL,
        derived_from=[parent_id],
    )

    assert result == winning_child_id
    # One outer transaction for artifact+derivations, one nested transaction
    # around INSERT so UniqueViolation rollback does not abort the outer one.
    assert conn.transaction.call_count == 2
    conn.fetchval.assert_awaited_once()
    conn.executemany.assert_awaited_once()
    assert conn.executemany.call_args[0][1] == [(winning_child_id, parent_id)]


@pytest.mark.asyncio
async def test_get_active_content_hash_only_uses_synced_artifacts():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value="sha256")

    result = await pg_store.get_active_content_hash(conn, "org", "kb", "path.md")

    assert result == "sha256"
    sql = conn.fetchval.call_args[0][0]
    assert "index_status = 'synced'" in sql


@pytest.mark.asyncio
async def test_list_active_synced_artifacts_only_returns_synced_active_rows():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "artifact_id": "artifact-1",
                "org_id": "org-1",
                "kb_slug": "kb",
                "path": "page.md",
            }
        ]
    )

    result = await pg_store.list_active_synced_artifacts(conn, created_before=1_700_000_000)

    assert result == [
        {
            "artifact_id": "artifact-1",
            "org_id": "org-1",
            "kb_slug": "kb",
            "path": "page.md",
        }
    ]
    sql = conn.fetch.call_args[0][0]
    assert "belief_time_end = $1" in sql
    assert "index_status = 'synced'" in sql
    assert "created_at <= $2" in sql
    assert conn.fetch.call_args[0][1] == _SENTINEL
    assert conn.fetch.call_args[0][2] == 1_700_000_000


@pytest.mark.asyncio
async def test_list_recent_artifact_keys_covers_created_and_closed_rows():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "artifact_id": "artifact-recent",
                "org_id": "org-1",
                "kb_slug": "kb",
                "path": "page.md",
            }
        ]
    )

    result = await pg_store.list_recent_artifact_keys(conn, since=1_700_000_000)

    assert result == [
        {
            "artifact_id": "artifact-recent",
            "org_id": "org-1",
            "kb_slug": "kb",
            "path": "page.md",
        }
    ]
    sql = conn.fetch.call_args[0][0]
    assert "created_at > $1" in sql
    # Closed-recently branch must exclude the still-active sentinel value,
    # otherwise every active row matches belief_time_end > since.
    assert "belief_time_end <> $2" in sql
    assert "belief_time_end > $1" in sql
    assert conn.fetch.call_args[0][1] == 1_700_000_000
    assert conn.fetch.call_args[0][2] == _SENTINEL


@pytest.mark.asyncio
async def test_soft_delete_updates_belief_time_end_and_returns_closed_ids():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"id": "closed-id-1", "path": "note.md"},
            {"id": "closed-id-2", "path": "old-title.md"},
        ]
    )
    with patch("knowledge_ingest.pg_store.time.time", return_value=1_700_000_000):
        result = await pg_store.soft_delete_artifact(conn, "org123", "personal", "note.md")

    # (id, path) pairs: the path lets the caller clear Qdrant points that were
    # written under a previous title.
    assert result == [("closed-id-1", "note.md"), ("closed-id-2", "old-title.md")]
    conn.fetch.assert_called_once()
    call_args = conn.fetch.call_args[0]
    assert "UPDATE knowledge.artifacts" in call_args[0]
    assert "belief_time_end" in call_args[0]
    assert "RETURNING id" in call_args[0]
    values = call_args[1:]
    assert 1_700_000_000 in values
    assert "org123" in values
    assert "personal" in values
    assert "note.md" in values
    assert _SENTINEL in values


@pytest.mark.asyncio
async def test_soft_delete_only_updates_active_records():
    """Verifies the WHERE belief_time_end = SENTINEL constraint is present."""
    conn = _make_conn()
    await pg_store.soft_delete_artifact(conn, "org", "kb", "path.md")
    sql = conn.fetch.call_args[0][0]
    # Must filter on sentinel to avoid touching already-deleted records
    assert str(_SENTINEL) in sql or "$5" in sql


@pytest.mark.asyncio
async def test_set_superseded_by_links_only_listed_unlinked_rows():
    conn = _make_conn()

    await pg_store.set_superseded_by(
        conn,
        ["closed-id-1", "closed-id-2"],
        "replacement-artifact-id",
    )

    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    sql = call_args[0]
    assert "UPDATE knowledge.artifacts" in sql
    assert "SET superseded_by = $1" in sql
    assert "id = ANY($2::uuid[])" in sql
    assert "superseded_by IS NULL" in sql
    assert call_args[1:] == (
        "replacement-artifact-id",
        ["closed-id-1", "closed-id-2"],
    )


@pytest.mark.asyncio
async def test_set_superseded_by_is_noop_for_empty_id_list():
    conn = _make_conn()

    await pg_store.set_superseded_by(conn, [], "replacement-artifact-id")

    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_stale_connector_artifact_paths_excludes_current_paths():
    """Connector reconciliation only returns active paths absent from latest crawl."""
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"path": "https://getklai.com/"},
            {"path": "https://getklai.com/contact"},
        ]
    )

    result = await pg_store.list_stale_connector_artifact_paths(
        conn,
        org_id="org",
        kb_slug="kb",
        connector_id="conn-1",
        current_paths=["https://www.getklai.com/"],
    )

    assert result == ["https://getklai.com/", "https://getklai.com/contact"]
    sql = conn.fetch.call_args[0][0]
    assert "source_connector_id" in sql
    assert "path = ANY($5::text[])" in sql
    assert "source_url" in sql
    assert conn.fetch.call_args[0][5] == ["https://www.getklai.com/"]


@pytest.mark.asyncio
async def test_has_active_connector_artifact_for_url_checks_source_url():
    """Connector artifacts may use path=index.md while source_url is the crawl URL."""
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=True)

    result = await pg_store.has_active_connector_artifact_for_url(
        conn,
        org_id="org",
        kb_slug="kb",
        connector_id="conn-1",
        url="https://jantinedoornbos.nl/",
    )

    assert result is True
    sql = conn.fetchval.call_args[0][0]
    assert "source_connector_id" in sql
    assert "source_url" in sql
    assert "belief_time_end = $5" in sql


@pytest.mark.asyncio
async def test_soft_delete_stale_connector_artifacts_scrubs_registry_and_links():
    """Stale connector cleanup retires artifacts and removes crawl metadata."""
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=2)

    deleted = await pg_store.soft_delete_stale_connector_artifacts(
        conn,
        org_id="org",
        kb_slug="kb",
        connector_id="conn-1",
        stale_paths=["https://getklai.com/", "https://getklai.com/contact"],
    )

    assert deleted == 2
    executed_sql = "\n".join(call.args[0] for call in conn.execute.await_args_list)
    final_sql = conn.fetchval.call_args[0][0]
    assert "DELETE FROM knowledge.crawled_pages" in executed_sql
    assert "DELETE FROM knowledge.page_links" in executed_sql
    assert "source_connector_id" in final_sql
    assert "belief_time_end = $6" in final_sql


@pytest.mark.asyncio
async def test_mark_stale_pending_artifacts_failed_requires_no_runnable_job():
    """The stale-pending janitor must not fail rows that still have enrich work."""
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "artifact_id": "11111111-2222-3333-4444-555555555555",
                "org_id": "org1",
                "kb_slug": "oracle",
                "path": "team.md",
                "created_at": 1700000000,
            }
        ]
    )

    result = await pg_store.mark_stale_pending_artifacts_failed(
        conn,
        cutoff_created_at=1700001800,
        limit=25,
    )

    assert result == [
        {
            "artifact_id": "11111111-2222-3333-4444-555555555555",
            "org_id": "org1",
            "kb_slug": "oracle",
            "path": "team.md",
            "created_at": 1700000000,
        }
    ]
    sql = conn.fetch.call_args[0][0]
    assert "a.index_status = 'pending'" in sql
    assert "a.belief_time_end = $2" in sql
    assert "source_connector_id" not in sql
    assert "NOT EXISTS" in sql
    assert "procrastinate_jobs" in sql
    assert "pj.status IN ('todo', 'doing')" in sql
    assert "pj.args->>'artifact_id' = a.id::text" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "SET index_status = 'failed'" in sql
    assert "index_status_changed_at = extract(epoch from now())::bigint" in sql
    assert conn.fetch.call_args[0][1:] == (
        1700001800,
        _SENTINEL,
        [
            "knowledge_ingest.enrichment_tasks.enrich_document_interactive",
            "knowledge_ingest.enrichment_tasks.enrich_document_bulk",
        ],
        25,
    )


@pytest.mark.asyncio
async def test_set_artifact_ingest_status_updates_any_artifact():
    conn = _make_conn()
    conn.fetchrow = AsyncMock(return_value={"artifact_id": "art-1", "path": "doc.md"})

    result = await pg_store.set_artifact_ingest_status(conn, "art-1", "org1", "synced")

    assert result == {"artifact_id": "art-1", "path": "doc.md"}
    sql = conn.fetchrow.call_args[0][0]
    assert "SET index_status = $1" in sql
    assert "index_status_changed_at = $4" in sql
    assert "source_connector_id" not in sql
    args = conn.fetchrow.call_args[0][1:]
    assert args[:3] == ("synced", "art-1", "org1")
    # 4th arg is the transition timestamp (epoch seconds).
    assert isinstance(args[3], int)


# -- list_personal_artifacts --------------------------------------------------


@pytest.mark.asyncio
async def test_list_personal_artifacts_queries_correct_params():
    conn = _make_conn()
    conn.fetch = AsyncMock(return_value=[])
    result = await pg_store.list_personal_artifacts(conn, "org1", "user1", limit=10, offset=5)
    assert result == []
    conn.fetch.assert_called_once()
    call_args = conn.fetch.call_args[0]
    sql = call_args[0]
    assert "knowledge.artifacts" in sql
    assert "kb_slug = $3" in sql
    values = call_args[1:]
    assert "org1" in values
    assert "user1" in values
    assert "personal-user1" in values
    assert _SENTINEL in values
    assert 10 in values
    assert 5 in values


@pytest.mark.asyncio
async def test_list_personal_artifacts_returns_dicts():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "abc",
                "path": "note.md",
                "assertion_mode": "fact",
                "created_at": 1700000000,
            }
        ]
    )
    result = await pg_store.list_personal_artifacts(conn, "org1", "user1")
    assert len(result) == 1
    assert result[0]["id"] == "abc"


# -- count_personal_artifacts -------------------------------------------------


@pytest.mark.asyncio
async def test_count_personal_artifacts_returns_int():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=42)
    count = await pg_store.count_personal_artifacts(conn, "org1", "user1")
    assert count == 42
    conn.fetchval.assert_called_once()
    sql = conn.fetchval.call_args[0][0]
    assert "COUNT(*)" in sql
    assert "kb_slug = $3" in sql


@pytest.mark.asyncio
async def test_count_personal_artifacts_returns_zero_on_none():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=None)
    count = await pg_store.count_personal_artifacts(conn, "org1", "user1")
    assert count == 0


# -- get_personal_artifact ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_personal_artifact_returns_dict_when_found():
    conn = _make_conn()
    conn.fetchrow = AsyncMock(return_value={"id": "abc-123", "path": "note.md"})
    result = await pg_store.get_personal_artifact(conn, "abc-123", "org1", "user1")
    assert result is not None
    assert result["id"] == "abc-123"
    assert result["path"] == "note.md"
    sql = conn.fetchrow.call_args[0][0]
    assert "kb_slug = $4" in sql


@pytest.mark.asyncio
async def test_get_personal_artifact_returns_none_when_not_found():
    conn = _make_conn()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await pg_store.get_personal_artifact(conn, "nonexistent", "org1", "user1")
    assert result is None


@pytest.mark.asyncio
async def test_update_artifact_extra_merges_jsonb():
    """update_artifact_extra issues a JSONB merge UPDATE (AC-2)."""
    import json as json_mod

    conn = _make_conn()
    await pg_store.update_artifact_extra(conn, "art-001", {"graphiti_episode_id": "ep-xyz"})

    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    sql = call_args[0]
    assert "UPDATE knowledge.artifacts" in sql
    assert "COALESCE" in sql
    # First positional arg after SQL is the JSON patch
    patch_arg = call_args[1]
    patch_dict = json_mod.loads(patch_arg)
    assert patch_dict["graphiti_episode_id"] == "ep-xyz"
    # Second positional arg is the artifact_id
    assert call_args[2] == "art-001"
