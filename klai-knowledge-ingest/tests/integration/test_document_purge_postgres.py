"""A deleted document is gone from every store; a replaced version is kept.

Runs against a disposable real PostgreSQL (``DOCUMENT_PURGE_TEST_DSN``).
Qdrant and the graph are fakes that record what was asked of them: the Qdrant
fake evaluates the delete filter against synthetic point payloads, so a test
fails when the filter misses a version or reaches another tenant.

Every document here is seeded with several versions linked by
``superseded_by`` (the active row last), each carrying ``document_text``, a
graph episode id, parent chunks, an image row and an embedding-queue row, plus
crawl metadata for its path. A second document in the same knowledge base
(replaced once, still live) and a copy of the deleted document in another
organisation must survive every delete.
"""

from __future__ import annotations

import itertools
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi import HTTPException
from klai_kb_slugs import personal_kb_slug
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from knowledge_ingest import graph as graph_module
from knowledge_ingest import qdrant_store
from knowledge_ingest.config import settings

_DSN = os.environ.get("DOCUMENT_PURGE_TEST_DSN")
_SENTINEL = 253402300800
# The routes under test only hand the request to the identity checks patched below.
_REQUEST = cast(Any, SimpleNamespace(headers={}))
_SCRIBE_REQUEST = cast(Any, SimpleNamespace(headers={"x-caller-service": "scribe"}))
_PORTAL_REQUEST = cast(Any, SimpleNamespace(headers={"x-caller-service": "portal-api"}))

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="DOCUMENT_PURGE_TEST_DSN is not configured"),
]


# -- harness ----------------------------------------------------------------


async def _connect(org_id: str) -> asyncpg.Connection:
    assert _DSN is not None
    conn = await asyncpg.connect(_DSN)
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
    return conn


@asynccontextmanager
async def _tenant_connection(org_id: str):
    conn = await _connect(org_id)
    try:
        yield conn
    finally:
        await conn.close()


def _condition_matches(cond, payload: dict) -> bool:
    if isinstance(cond, Filter):
        return _filter_matches(cond, payload)
    assert isinstance(cond, FieldCondition)
    value = payload.get(cond.key)
    match = cond.match
    if isinstance(match, MatchAny):
        return value in match.any
    assert isinstance(match, MatchValue)
    return value == match.value


def _filter_matches(flt: Filter, payload: dict) -> bool:
    if flt.must and not all(_condition_matches(c, payload) for c in flt.must):
        return False
    if flt.should and not any(_condition_matches(c, payload) for c in flt.should):
        return False
    return not (flt.must_not and any(_condition_matches(c, payload) for c in flt.must_not))


class _FakeQdrant:
    def __init__(self) -> None:
        self.filters: list[Filter] = []
        self.delete = AsyncMock(side_effect=self._delete)
        self.fail_next = False

    async def _delete(self, _collection, *, points_selector, **_kwargs) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("vector store unavailable")
        self.filters.append(points_selector)

    def deletes(self, payload: dict) -> bool:
        return any(_filter_matches(f, payload) for f in self.filters)


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, set[str]]] = []
        self.fail_next = False

    async def delete_kb_episodes(self, org_id: str, episode_ids: list[str]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("graph unavailable")
        self.calls.append((org_id, set(episode_ids)))

    def deleted(self, org_id: str) -> set[str]:
        return set().union(*(ids for org, ids in self.calls if org == org_id))


@pytest.fixture
def stores(monkeypatch):
    qdrant = _FakeQdrant()
    graph = _FakeGraph()
    monkeypatch.setattr(qdrant_store, "get_client", lambda: qdrant)
    monkeypatch.setattr(graph_module, "delete_kb_episodes", graph.delete_kb_episodes)
    monkeypatch.setattr(settings, "graphiti_enabled", True)
    for module in (
        "knowledge_ingest.routes.kb_sources",
        "knowledge_ingest.routes.personal",
        "knowledge_ingest.routes.ingest",
    ):
        monkeypatch.setattr(f"{module}.tenant_scoped_connection", _tenant_connection)
    for module in ("knowledge_ingest.routes.kb_sources", "knowledge_ingest.routes.ingest"):
        monkeypatch.setattr(
            f"{module}.assert_caller_identity_tenant_only",
            AsyncMock(side_effect=lambda _request, *, claimed_org_id: claimed_org_id),
        )
    monkeypatch.setattr("knowledge_ingest.routes.ingest._verify_internal_secret", lambda _r: None)
    return SimpleNamespace(qdrant=qdrant, graph=graph)


async def _seed(
    org_id: str,
    kb_slug: str,
    path: str,
    *,
    versions: int = 3,
    active: bool = True,
    extra: dict | None = None,
    user_id: str | None = None,
    text: bool = True,
    index_status: str = "synced",
) -> list[str]:
    """Insert one document with ``versions`` rows, oldest first, newest last."""
    ids = [str(uuid.uuid4()) for _ in range(versions)]
    now = int(time.time())
    conn = await _connect(org_id)
    try:
        for i, artifact_id in enumerate(ids):
            is_head = i == versions - 1
            row_extra = {**(extra or {}), "graphiti_episode_ids": [f"ep-{artifact_id}"]}
            if text:
                row_extra["document_text"] = f"synthetic body {i} of {path}"
            await conn.execute(
                """
                INSERT INTO knowledge.artifacts (
                    id, org_id, user_id, provenance_type, assertion_mode,
                    belief_time_start, belief_time_end, created_at, kb_slug, path, extra,
                    index_status
                ) VALUES ($1, $2, $3, 'observed', 'factual', $4, $5, $4, $6, $7, $8::jsonb, $9)
                """,
                artifact_id,
                org_id,
                user_id,
                now - 100 + i,
                _SENTINEL if is_head and active else now - 50 + i,
                kb_slug,
                path,
                json.dumps(row_extra),
                index_status,
            )
            await conn.execute(
                """INSERT INTO knowledge.parent_chunks
                       (artifact_id, org_id, text, token_count, position)
                   VALUES ($1, $2, 'synthetic parent', 2, 0)""",
                artifact_id,
                org_id,
            )
            await conn.execute(
                "INSERT INTO knowledge.artifact_images VALUES ($1, $2, 'hash')",
                artifact_id,
                f"{org_id}/images/{kb_slug}/{artifact_id}.png",
            )
            await conn.execute(
                "INSERT INTO knowledge.embedding_queue VALUES ($1, $2, 'upsert', $3, NULL)",
                str(uuid.uuid4()),
                artifact_id,
                now,
            )
        for older, newer in itertools.pairwise(ids):
            await conn.execute(
                "UPDATE knowledge.artifacts SET superseded_by = $2 WHERE id = $1", older, newer
            )
        await conn.execute(
            """INSERT INTO knowledge.crawled_pages
                   (org_id, kb_slug, url, content_hash, raw_markdown, crawled_at)
               VALUES ($1, $2, $3, 'h', 'synthetic', $4) ON CONFLICT DO NOTHING""",
            org_id,
            kb_slug,
            path,
            now,
        )
        await conn.execute(
            """INSERT INTO knowledge.page_links (org_id, kb_slug, from_url, to_url)
               VALUES ($1, $2, $3, 'https://example.com/elsewhere') ON CONFLICT DO NOTHING""",
            org_id,
            kb_slug,
            path,
        )
    finally:
        await conn.close()
    return ids


async def _remaining(org_id: str, ids: list[str], kb_slug: str, path: str) -> dict[str, int]:
    conn = await _connect(org_id)
    try:
        counts = {}
        for table, column in (
            ("artifacts", "id"),
            ("parent_chunks", "artifact_id"),
            ("artifact_images", "artifact_id"),
            ("embedding_queue", "artifact_id"),
        ):
            counts[table] = await conn.fetchval(
                f"SELECT count(*) FROM knowledge.{table} WHERE {column} = ANY($1::uuid[])",  # noqa: S608
                ids,
            )
        counts["crawled_pages"] = await conn.fetchval(
            "SELECT count(*) FROM knowledge.crawled_pages "
            "WHERE org_id=$1 AND kb_slug=$2 AND url=$3",
            org_id,
            kb_slug,
            path,
        )
        counts["page_links"] = await conn.fetchval(
            "SELECT count(*) FROM knowledge.page_links "
            "WHERE org_id=$1 AND kb_slug=$2 AND from_url=$3",
            org_id,
            kb_slug,
            path,
        )
        return counts
    finally:
        await conn.close()


_GONE = {
    "artifacts": 0,
    "parent_chunks": 0,
    "artifact_images": 0,
    "embedding_queue": 0,
    "crawled_pages": 0,
    "page_links": 0,
}


def _kept(versions: int) -> dict[str, int]:
    return {
        "artifacts": versions,
        "parent_chunks": versions,
        "artifact_images": versions,
        "embedding_queue": versions,
        "crawled_pages": 1,
        "page_links": 1,
    }


def _payload(org_id: str, kb_slug: str, path: str, artifact_id: str) -> dict:
    return {"org_id": org_id, "kb_slug": kb_slug, "path": path, "artifact_id": artifact_id}


async def _world(kb_slug: str, path: str, *, user_id: str | None = None, extra=None):
    """The document to delete, a replaced neighbour, and another tenant's copy."""
    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    foreign_org = f"org-b-{uuid.uuid4().hex[:8]}"
    doc = await _seed(org_id, kb_slug, path, user_id=user_id, extra=extra)
    neighbour_path = f"{path}-neighbour"
    neighbour = await _seed(org_id, kb_slug, neighbour_path, versions=2, user_id=user_id)
    foreign = await _seed(foreign_org, kb_slug, path, user_id=user_id, extra=extra)
    return SimpleNamespace(
        org_id=org_id,
        foreign_org=foreign_org,
        kb=kb_slug,
        path=path,
        doc=doc,
        neighbour_path=neighbour_path,
        neighbour=neighbour,
        foreign=foreign,
    )


async def _assert_document_gone(w, stores, *, episodes: set[str] | None = None) -> None:
    assert await _remaining(w.org_id, w.doc, w.kb, w.path) == _GONE
    for artifact_id in w.doc:
        assert stores.qdrant.deletes(_payload(w.org_id, w.kb, w.path, artifact_id))
    assert stores.graph.deleted(w.org_id) == (episodes or {f"ep-{i}" for i in w.doc})


async def _assert_others_untouched(w, stores) -> None:
    # The replaced neighbour keeps both versions, including the old text.
    assert await _remaining(w.org_id, w.neighbour, w.kb, w.neighbour_path) == _kept(2)
    conn = await _connect(w.org_id)
    try:
        old = await conn.fetchrow(
            "SELECT superseded_by, extra->>'document_text' AS text "
            "FROM knowledge.artifacts WHERE id=$1",
            w.neighbour[0],
        )
    finally:
        await conn.close()
    assert old is not None
    assert str(old["superseded_by"]) == w.neighbour[1]
    assert old["text"] == f"synthetic body 0 of {w.neighbour_path}"
    for artifact_id in w.neighbour:
        assert not stores.qdrant.deletes(_payload(w.org_id, w.kb, w.neighbour_path, artifact_id))
    # Another tenant's identical document is not touched in any store.
    assert await _remaining(w.foreign_org, w.foreign, w.kb, w.path) == _kept(3)
    for artifact_id in w.foreign:
        assert not stores.qdrant.deletes(_payload(w.foreign_org, w.kb, w.path, artifact_id))
    assert stores.graph.deleted(w.foreign_org) == set()
    assert not stores.graph.deleted(w.org_id) & {f"ep-{i}" for i in w.neighbour + w.foreign}


# -- the history walk stays inside the tenant -----------------------------------


@pytest.mark.asyncio
async def test_history_walk_never_follows_a_link_into_another_tenant() -> None:
    from knowledge_ingest import pg_store

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    doc = await _seed(org_id, "kb", "guide.md", versions=2)
    # A corrupt row in another tenant claiming to be replaced by this document,
    # and an unrelated row of this tenant claiming to be replaced by that one.
    # Following the foreign link would reach and delete the unrelated row.
    intruder = await _seed(f"org-b-{uuid.uuid4().hex[:8]}", "kb", "guide.md", versions=1)
    stray = await _seed(org_id, "kb", "other.md", versions=1, active=False)
    conn = await _connect(org_id)
    try:
        await conn.execute(
            "UPDATE knowledge.artifacts SET superseded_by = $1 WHERE id = $2", doc[-1], intruder[0]
        )
        await conn.execute(
            "UPDATE knowledge.artifacts SET superseded_by = $1 WHERE id = $2", intruder[0], stray[0]
        )
        history, _ = await pg_store.list_document_history(conn, org_id, [doc[-1]])
        episodes = await pg_store.get_episode_ids_for_document_history(conn, org_id, [doc[-1]])
    finally:
        await conn.close()

    assert sorted(history) == sorted(doc)
    assert set(episodes) == {f"ep-{i}" for i in doc}


# -- path a: one uploaded file ------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_an_upload_removes_every_version_from_every_store(stores) -> None:
    from knowledge_ingest.routes.kb_sources import delete_upload

    w = await _world("kb-uploads", f"file:sha256:{uuid.uuid4().hex}")

    await delete_upload(_REQUEST, w.kb, w.doc[-1], org_id=w.org_id, user_id=None)

    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


@pytest.mark.asyncio
async def test_a_replaced_version_cannot_be_deleted_on_its_own(stores) -> None:
    from knowledge_ingest.routes.kb_sources import delete_upload

    w = await _world("kb-uploads", f"file:sha256:{uuid.uuid4().hex}")

    with pytest.raises(HTTPException) as exc:
        await delete_upload(_REQUEST, w.kb, w.doc[0], org_id=w.org_id, user_id=None)

    assert exc.value.status_code == 404
    assert await _remaining(w.org_id, w.doc, w.kb, w.path) == _kept(3)


@pytest.mark.asyncio
async def test_failed_external_delete_keeps_rows_closed_and_a_retry_completes(stores) -> None:
    from knowledge_ingest.routes.kb_sources import delete_upload

    w = await _world("kb-uploads", f"file:sha256:{uuid.uuid4().hex}")
    stores.graph.fail_next = True

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await delete_upload(_REQUEST, w.kb, w.doc[-1], org_id=w.org_id, user_id=None)

    conn = await _connect(w.org_id)
    try:
        open_rows = await conn.fetchval(
            "SELECT count(*) FROM knowledge.artifacts "
            "WHERE id = ANY($1::uuid[]) AND belief_time_end = $2",
            w.doc,
            _SENTINEL,
        )
    finally:
        await conn.close()
    assert await _remaining(w.org_id, w.doc, w.kb, w.path) == _kept(3)
    assert open_rows == 0, "the document must be invisible while the stores are dirty"

    await delete_upload(_REQUEST, w.kb, w.doc[-1], org_id=w.org_id, user_id=None)

    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


# -- path b2: a crawled page that no longer exists ----------------------------

_CRAWL = {"source_connector_id": "connector-1", "source_type": "crawl"}


async def _crawl_world():
    return await _world("kb-site", f"https://example.com/{uuid.uuid4().hex}", extra=_CRAWL)


async def _purge_stale(w, stale_paths: list[str]) -> int:
    from knowledge_ingest.adapters import crawler

    conn = await _connect(w.org_id)
    try:
        return await crawler._purge_stale_paths(conn, w.org_id, w.kb, "connector-1", stale_paths)
    finally:
        await conn.close()


async def _assert_closed_pending_and_kept(w) -> None:
    conn = await _connect(w.org_id)
    try:
        head = await conn.fetchrow(
            "SELECT belief_time_end, index_status FROM knowledge.artifacts WHERE id = $1",
            w.doc[-1],
        )
    finally:
        await conn.close()
    assert head is not None
    assert head["belief_time_end"] != _SENTINEL, "a half-cleaned page must not stay active"
    assert head["index_status"] == "deleting"
    assert await _remaining(w.org_id, w.doc, w.kb, w.path) == _kept(3)


@pytest.mark.asyncio
async def test_a_crawled_page_that_is_gone_is_removed_from_every_store(stores) -> None:
    w = await _crawl_world()

    assert await _purge_stale(w, [w.path]) == 1

    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


@pytest.mark.asyncio
async def test_crawled_page_failing_after_the_graph_delete_is_finished_by_the_next_crawl(
    stores,
) -> None:
    w = await _crawl_world()
    stores.qdrant.fail_next = True

    assert await _purge_stale(w, [w.path]) == 0

    await _assert_closed_pending_and_kept(w)
    assert stores.graph.deleted(w.org_id) == {f"ep-{i}" for i in w.doc}
    assert not any(stores.qdrant.deletes(_payload(w.org_id, w.kb, w.path, i)) for i in w.doc)

    # The next complete crawl no longer lists the page as active and stale.
    assert await _purge_stale(w, []) == 1

    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


@pytest.mark.asyncio
async def test_crawled_page_failing_after_the_vector_delete_is_finished_by_the_next_crawl(
    stores, monkeypatch
) -> None:
    from knowledge_ingest import pg_store

    w = await _crawl_world()
    real_delete = pg_store.delete_artifacts
    calls = 0

    async def _fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database unavailable")
        return await real_delete(*args, **kwargs)

    monkeypatch.setattr(pg_store, "delete_artifacts", _fail_once)

    assert await _purge_stale(w, [w.path]) == 0

    await _assert_closed_pending_and_kept(w)
    assert all(stores.qdrant.deletes(_payload(w.org_id, w.kb, w.path, i)) for i in w.doc)

    assert await _purge_stale(w, []) == 1

    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


@pytest.mark.asyncio
async def test_crawl_retry_only_picks_up_this_connectors_unfinished_purges(stores) -> None:
    w = await _crawl_world()
    other_path = f"https://example.com/{uuid.uuid4().hex}"
    other = await _seed(
        w.org_id,
        w.kb,
        other_path,
        versions=1,
        active=False,
        index_status="deleting",
        extra={"source_connector_id": "connector-2"},
    )

    # Another connector's closed document under the very path that went stale.
    same_path_other = await _seed(
        w.org_id,
        w.kb,
        w.path,
        versions=1,
        active=False,
        extra={"source_connector_id": "connector-2"},
    )

    assert await _purge_stale(w, []) == 0

    assert await _remaining(w.org_id, other, w.kb, other_path) == _kept(1)
    assert await _remaining(w.org_id, w.doc, w.kb, w.path) == _kept(3)

    assert await _purge_stale(w, [w.path]) == 1

    conn = await _connect(w.org_id)
    try:
        survivors = await conn.fetchval(
            "SELECT count(*) FROM knowledge.artifacts WHERE id = ANY($1::uuid[])",
            [*other, *same_path_other],
        )
    finally:
        await conn.close()
    assert survivors == 2


# -- path b3: a docs page removed through the gitea webhook --------------------


@pytest.mark.asyncio
async def test_a_removed_docs_page_is_removed_from_every_store(stores) -> None:
    from knowledge_ingest.routes import ingest

    w = await _world("kb-docs", f"guides/{uuid.uuid4().hex}.md")

    deleted = await ingest._purge_removed_paths(w.org_id, w.kb, [w.path])

    assert deleted == 1
    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


# -- path g1: a personal item -------------------------------------------------


def _asserter(monkeypatch, *, member: bool) -> AsyncMock:
    from knowledge_ingest import identity

    verify = AsyncMock(
        side_effect=lambda **kw: SimpleNamespace(
            verified=member,
            org_id=kw["claimed_org_id"] if member else None,
            reason=None if member else "no_membership",
        )
    )
    monkeypatch.setattr(identity, "_get_asserter", lambda: SimpleNamespace(verify=verify))
    return verify


@pytest.mark.asyncio
async def test_deleting_a_personal_item_reaches_the_personal_kb_rows(stores, monkeypatch) -> None:
    from knowledge_ingest.routes.personal import delete_personal_item

    verify = _asserter(monkeypatch, member=True)
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    w = await _world(personal_kb_slug(user_id), "notes/synthetic.md", user_id=user_id)

    await delete_personal_item(_PORTAL_REQUEST, w.doc[-1], org_id=w.org_id, user_id=user_id)

    assert verify.await_args is not None
    assert verify.await_args.kwargs["claimed_user_id"] == user_id
    assert verify.await_args.kwargs["claimed_org_id"] == w.org_id
    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


@pytest.mark.asyncio
async def test_personal_item_delete_for_a_user_outside_the_org_removes_nothing(
    stores, monkeypatch
) -> None:
    from knowledge_ingest.routes.personal import delete_personal_item

    _asserter(monkeypatch, member=False)
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    w = await _world(personal_kb_slug(user_id), "notes/synthetic.md", user_id=user_id)

    with pytest.raises(HTTPException) as exc:
        await delete_personal_item(_PORTAL_REQUEST, w.doc[-1], org_id=w.org_id, user_id=user_id)

    assert exc.value.status_code == 403
    assert await _remaining(w.org_id, w.doc, w.kb, w.path) == _kept(3)
    assert stores.qdrant.filters == []
    assert stores.graph.calls == []


# -- path g2: a scribe transcript ----------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_document_by_path_removes_it_from_every_knowledge_base(stores) -> None:
    from knowledge_ingest.routes import ingest

    transcript_path = f"scribe/txn_{uuid.uuid4().hex}"
    w = await _world("kb-meetings", transcript_path)
    second_kb_copy = await _seed(w.org_id, "kb-other", transcript_path, versions=1)

    result = await ingest.delete_document_route(_SCRIBE_REQUEST, org_id=w.org_id, path=w.path)

    assert result["artifacts_deleted"] == 4
    await _assert_document_gone(w, stores, episodes={f"ep-{i}" for i in w.doc + second_kb_copy})
    assert await _remaining(w.org_id, second_kb_copy, "kb-other", transcript_path) == _GONE
    await _assert_others_untouched(w, stores)


# -- path c stays complete -----------------------------------------------------


@pytest.mark.asyncio
async def test_connector_document_delete_still_removes_every_version(stores) -> None:
    from knowledge_ingest.routes import ingest

    w = await _world(
        "kb-feed",
        "feed/group-a",
        extra={"source_connector_id": "connector-1", "source_ref": "json-feed:group-a"},
    )

    result = await ingest.delete_connector_document_route(
        _REQUEST,
        org_id=w.org_id,
        kb_slug=w.kb,
        connector_id="connector-1",
        source_ref="json-feed:group-a",
    )

    assert result == {"status": "ok", "artifacts_deleted": 3, "episodes_deleted": 3}
    await _assert_document_gone(w, stores)
    await _assert_others_untouched(w, stores)


# -- reindex reopens a closed head, never a replaced version ------------------


@pytest.fixture
def enrichment_queue(monkeypatch):
    from knowledge_ingest import enrichment_tasks

    job = MagicMock()
    job.configure.return_value.defer_async = AsyncMock()
    monkeypatch.setattr(
        enrichment_tasks, "get_app", lambda: SimpleNamespace(enrich_document_interactive=job)
    )
    monkeypatch.setattr(
        "knowledge_ingest.routes.kb_sources.maybe_refresh_stale_graph",
        AsyncMock(return_value=None),
    )
    return job


async def _active_ids(org_id: str, ids: list[str]) -> set[str]:
    conn = await _connect(org_id)
    try:
        rows = await conn.fetch(
            "SELECT id::text FROM knowledge.artifacts "
            "WHERE id = ANY($1::uuid[]) AND belief_time_end = $2",
            ids,
            _SENTINEL,
        )
    finally:
        await conn.close()
    return {row["id"] for row in rows}


@pytest.mark.asyncio
async def test_reindex_refuses_a_version_that_has_a_successor(stores, enrichment_queue) -> None:
    from knowledge_ingest.routes.kb_sources import reindex_upload

    w = await _world("kb-uploads", f"file:sha256:{uuid.uuid4().hex}")

    with pytest.raises(HTTPException) as exc:
        await reindex_upload(_REQUEST, w.doc[0], org_id=w.org_id)

    assert exc.value.status_code == 404
    assert await _active_ids(w.org_id, w.doc) == {w.doc[-1]}


@pytest.mark.asyncio
async def test_reindex_of_a_closed_document_reopens_its_head_and_never_an_older_version(
    stores, enrichment_queue
) -> None:
    """With nothing live on the path, only the successor check guards the old version."""
    from knowledge_ingest.routes.kb_sources import reindex_upload

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    doc = await _seed(org_id, "kb-uploads", "file:sha256:two", versions=2, active=False, text=False)

    with pytest.raises(HTTPException) as exc:
        await reindex_upload(_REQUEST, doc[0], org_id=org_id)
    assert exc.value.status_code == 404
    assert await _active_ids(org_id, doc) == set()

    await reindex_upload(_REQUEST, doc[1], org_id=org_id)
    assert await _active_ids(org_id, doc) == {doc[1]}


@pytest.mark.asyncio
async def test_reindex_refuses_a_closed_row_whose_path_is_live(stores, enrichment_queue) -> None:
    from knowledge_ingest.routes.kb_sources import reindex_upload

    w = await _world("kb-uploads", f"file:sha256:{uuid.uuid4().hex}")
    unlinked = await _seed(w.org_id, w.kb, w.path, versions=1, active=False)

    with pytest.raises(HTTPException) as exc:
        await reindex_upload(_REQUEST, unlinked[0], org_id=w.org_id)

    assert exc.value.status_code == 404
    assert await _active_ids(w.org_id, [*w.doc, *unlinked]) == {w.doc[-1]}


@pytest.mark.asyncio
async def test_reindex_refuses_a_document_whose_delete_did_not_finish(
    stores, enrichment_queue
) -> None:
    from knowledge_ingest.routes.kb_sources import reindex_upload

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    pending = await _seed(
        org_id,
        "kb-uploads",
        "file:sha256:pending",
        versions=1,
        active=False,
        index_status="deleting",
    )

    with pytest.raises(HTTPException) as exc:
        await reindex_upload(_REQUEST, pending[0], org_id=org_id)

    assert exc.value.status_code == 404
    assert await _active_ids(org_id, pending) == set()


@pytest.mark.asyncio
async def test_reindex_reopens_a_closed_upload_with_no_successor(stores, enrichment_queue) -> None:
    """The portal offers reindex for an upload whose artifact was closed (drift repair)."""
    from knowledge_ingest.routes.kb_sources import reindex_upload

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    retired = await _seed(
        org_id, "kb-uploads", "file:sha256:retired", versions=1, active=False, text=False
    )

    response = await reindex_upload(_REQUEST, retired[0], org_id=org_id)

    assert response.index_status == "pending"
    assert await _active_ids(org_id, retired) == {retired[0]}
    enrichment_queue.configure.return_value.defer_async.assert_awaited_once_with(
        artifact_id=retired[0]
    )


# -- the history walk reaches the oldest version ------------------------------


@pytest.mark.asyncio
async def test_delete_reaches_every_version_of_a_long_history(stores) -> None:
    from knowledge_ingest import document_purge

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    doc = await _seed(org_id, "kb", "long.md", versions=130)

    conn = await _connect(org_id)
    try:
        deleted, episodes = await document_purge.purge_documents(conn, org_id, [doc[-1]])
    finally:
        await conn.close()

    assert (deleted, episodes) == (130, 130)
    assert await _remaining(org_id, doc, "kb", "long.md") == _GONE


@pytest.mark.asyncio
async def test_history_walk_ends_on_a_cycle() -> None:
    from knowledge_ingest import pg_store

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    doc = await _seed(org_id, "kb", "cycle.md", versions=2)
    conn = await _connect(org_id)
    try:
        await conn.execute(
            "UPDATE knowledge.artifacts SET superseded_by = $1 WHERE id = $2", doc[0], doc[1]
        )
        history, _ = await pg_store.list_document_history(conn, org_id, [doc[1]])
    finally:
        await conn.close()

    assert sorted(history) == sorted(doc)


# -- enrichment that outlives its artifact --------------------------------------


@pytest.mark.asyncio
async def test_enrichment_removes_points_written_after_its_artifact_was_deleted(
    stores, monkeypatch
) -> None:
    from knowledge_ingest import document_purge, enrichment_tasks
    from knowledge_ingest.enrichment import EnrichedChunk

    org_id = f"org-a-{uuid.uuid4().hex[:8]}"
    doc = await _seed(org_id, "kb", "late.md", versions=1)
    written: list[str] = []

    async def _upsert_after_a_delete_landed(**kwargs) -> None:
        # The delete runs between the job's active check and its upsert.
        conn = await _connect(org_id)
        try:
            await document_purge.purge_documents(conn, org_id, [doc[0]])
        finally:
            await conn.close()
        written.append(kwargs["artifact_id"])

    monkeypatch.setattr(enrichment_tasks, "tenant_scoped_connection", _tenant_connection)
    monkeypatch.setattr(
        enrichment_tasks.enrichment,
        "enrich_chunks",
        AsyncMock(return_value=[EnrichedChunk("body", "ctx\n\nbody", "ctx", ["q?"])]),
    )
    monkeypatch.setattr(enrichment_tasks.embedder, "embed", AsyncMock(return_value=[[1.0]]))
    monkeypatch.setattr(
        enrichment_tasks.sparse_embedder, "embed_sparse_batch", AsyncMock(return_value=[None])
    )
    for name, value in (
        ("get_kb_aliases", []),
        ("get_kb_product_rules", []),
        ("get_kb_visibility", "internal"),
    ):
        monkeypatch.setattr(enrichment_tasks.kb_config, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        enrichment_tasks.qdrant_store, "upsert_enriched_chunks", _upsert_after_a_delete_landed
    )
    status = AsyncMock()
    monkeypatch.setattr(enrichment_tasks, "_set_direct_upload_index_status", status)

    result = await enrichment_tasks._enrich_document(
        org_id=org_id,
        kb_slug="kb",
        path="late.md",
        document_text="body",
        chunks=["body"],
        title="T",
        artifact_id=doc[0],
        user_id=None,
        extra_payload={"document_summary": "s", "document_language": "en"},
        synthesis_depth=2,
    )

    assert written == [doc[0]]
    assert result is False
    assert stores.qdrant.deletes(_payload(org_id, "kb", "late.md", doc[0]))
    # The last delete is the one the job issued after its own upsert.
    assert _filter_matches(stores.qdrant.filters[-1], {"org_id": org_id, "artifact_id": doc[0]})
    assert len(stores.qdrant.filters) == 2
    status.assert_not_awaited()


# -- backlog report and purge -------------------------------------------------


@pytest.mark.asyncio
async def test_backlog_report_counts_only_deleted_documents_and_execute_purges_them(
    stores, monkeypatch
) -> None:
    from scripts import purge_deleted_documents as script

    monkeypatch.setattr(script, "tenant_scoped_connection", _tenant_connection)
    org_id = f"org-backlog-{uuid.uuid4().hex[:8]}"
    kb = "kb-backlog"
    # Deleted: closed head, nothing live under its path.
    deleted = await _seed(org_id, kb, "https://example.com/removed", active=False)
    # Replaced: active head with one older version.
    replaced = await _seed(org_id, kb, "https://example.com/live", versions=2)
    # Unlinked old version of a live page: the old crawler cleanup nulled the
    # link, but the same path is live.
    nulled = await _seed(org_id, kb, "https://example.com/live", versions=1, active=False)
    # Renamed connector page whose old row lost its link: different path, but
    # the same connector source_ref is live.
    identity = {"source_connector_id": "connector-1", "source_ref": "page-7"}
    await _seed(org_id, kb, "https://example.com/new-title", versions=1, extra=identity)
    renamed_old = await _seed(
        org_id, kb, "https://example.com/old-title", versions=1, active=False, extra=identity
    )
    # Retired by the drift repair: closed, no successor, no text. The portal
    # still shows it with a reindex action, so it must never be purged.
    retired = await _seed(org_id, kb, "file:sha256:retired", versions=1, active=False, text=False)
    # A delete that closed the row but did not finish: no text, but marked.
    unfinished = await _seed(
        org_id,
        kb,
        "file:sha256:unfinished",
        versions=1,
        active=False,
        text=False,
        index_status="deleting",
    )
    other_tenant = await _seed(f"org-other-{uuid.uuid4().hex[:8]}", kb, "x", active=False)

    async with _tenant_connection(org_id) as conn:
        report = await script.collect(conn, org_id)

    assert report.deleted_heads == {org_id: sorted([deleted[-1], unfinished[0]])}
    (group,) = report.deleted.values()
    assert (len(group.documents), group.rows, group.rows_with_text, group.parent_chunks) == (
        2,
        4,
        3,
        4,
    )
    assert group.episode_ids == {f"ep-{i}" for i in [*deleted, *unfinished]}
    assert sum(len(g.documents) for g in report.kept_unlinked.values()) == 2
    (without_text,) = report.closed_without_text.values()
    assert without_text.documents == {retired[0]}
    assert report.closed_rows == {org_id: 3 + 1 + 1 + 1 + 1 + 1}
    assert "closed_without_text" in script.render(report)
    assert other_tenant[-1] not in str(report)

    purged, failed = await script.purge(org_id, report.deleted_heads[org_id])

    assert (purged, failed) == (2, 0)
    assert await _remaining(org_id, deleted, kb, "https://example.com/removed") == _GONE
    conn = await _connect(org_id)
    try:
        kept = await conn.fetchval(
            "SELECT count(*) FROM knowledge.artifacts WHERE id = ANY($1::uuid[])",
            [*replaced, *nulled, *renamed_old, *retired],
        )
    finally:
        await conn.close()
    assert kept == 5
