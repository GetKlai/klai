"""SPEC-PORTAL-KENNIS-001 — sources endpoints unit tests.

Pin the wiring of:
  - `list_kb_sources` — merges portal connectors + knowledge-ingest aggregates
    into one uniform list, including newly-created connectors with zero items.
  - `get_source_content` — drill-down with kind=connector | kind=upload, plus
    the input-validation guards (kind, limit, offset).

The label helpers are pure functions — covered with table-style tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _make_perms() -> object:
    """Synthetic UserPermissions matching the historic
    ``_get_caller_org`` tuple ``("user-1", org_id=1)``.
    """
    return make_perms(role="admin", user_id="user-1", org_id=1)


# -- Label helpers ----------------------------------------------------------


def test_connector_type_label_known_types() -> None:
    from app.api.app_knowledge_bases import _connector_type_label

    assert _connector_type_label("github") == "GitHub"
    assert _connector_type_label("notion") == "Notion"
    assert _connector_type_label("google_drive") == "Google Drive"
    assert _connector_type_label("ms_docs") == "Microsoft Docs"
    assert _connector_type_label("web_crawler") == "Website (pagina's)"
    assert _connector_type_label("airtable") == "Airtable"
    assert _connector_type_label("confluence") == "Confluence"
    assert _connector_type_label("mcp_connector") == "MCP"


def test_connector_type_label_unknown_type_falls_back_to_titled() -> None:
    from app.api.app_knowledge_bases import _connector_type_label

    assert _connector_type_label("custom_thing") == "Custom Thing"
    assert _connector_type_label("xyz") == "Xyz"


def test_upload_type_label_known_content_types() -> None:
    from app.api.app_knowledge_bases import _upload_type_label

    assert _upload_type_label("pdf") == "PDF"
    assert _upload_type_label("application/pdf") == "PDF"
    assert _upload_type_label("html") == "Link"
    assert _upload_type_label("text/html") == "Link"
    assert _upload_type_label("web_page") == "Websitepagina"
    assert _upload_type_label("text") == "Tekst"
    assert _upload_type_label("text/markdown") == "Tekst"
    assert _upload_type_label("image/png") == "Afbeelding"
    assert _upload_type_label("") == "Bestand"
    assert _upload_type_label("unknown") == "Bestand"


def test_upload_type_label_fallback_replaces_underscores() -> None:
    """Regression: bare-underscore content_type slugs previously rendered as
    "Plain_Text" because str.title() does not split on underscores. The
    fallback now replaces underscores with spaces before title-casing so
    the user-visible meta line reads "Plain Text" / "Rich Text Format" /
    etc."""
    from app.api.app_knowledge_bases import _upload_type_label

    assert _upload_type_label("plain_text") == "Plain Text"
    assert _upload_type_label("rich_text_format") == "Rich Text Format"
    # MIME-shaped fallback still uppercases the subtype.
    assert _upload_type_label("application/octet-stream") == "OCTET-STREAM"


# -- list_kb_sources --------------------------------------------------------


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb(slug: str = "kb-a") -> MagicMock:
    kb = MagicMock()
    kb.id = 42
    kb.slug = slug
    kb.org_id = 1
    return kb


def _make_connector(
    cid: str,
    ctype: str = "notion",
    name: str | None = "My Notion",
    status: str = "ok",
    state: str = "active",
) -> MagicMock:
    c = MagicMock()
    c.id = cid
    c.kb_id = 42
    c.org_id = 1
    c.connector_type = ctype
    c.name = name
    c.last_sync_status = status
    c.last_sync_at = datetime(2026, 1, 1, tzinfo=UTC)
    # SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-02: list_kb_sources filters
    # by state == "active". MagicMock's default attribute is a MagicMock
    # instance (truthy but != "active"), so without an explicit assignment
    # every fixture connector silently lands in the "deleting" bucket and
    # disappears from the response.
    c.state = state
    return c


def _make_upload(
    filename: str = "Chemie Overal 4VWO.pdf",
    status: str = "processing",
    *,
    artifact_id: str | None = None,
    source_ref: str = "file:sha256:upload",
) -> MagicMock:
    upload = MagicMock()
    upload.id = uuid4()
    upload.kb_id = 42
    upload.org_id = 1
    upload.filename = filename
    upload.extension = ".pdf"
    upload.mime = "application/pdf"
    upload.status = status
    upload.artifact_id = artifact_id
    upload.source_ref = source_ref
    upload.created_at = datetime(2026, 5, 10, tzinfo=UTC)
    return upload


def _make_sources_db(
    connectors: list[MagicMock],
    uploads: list[MagicMock] | None = None,
    done_uploads: list[MagicMock] | None = None,
) -> AsyncMock:
    db = AsyncMock()
    conn_query_result = MagicMock()
    conn_query_result.scalars.return_value.all.return_value = connectors
    done_upload_query_result = MagicMock()
    done_upload_query_result.scalars.return_value.all.return_value = done_uploads or []
    upload_query_result = MagicMock()
    upload_query_result.scalars.return_value.all.return_value = uploads or []
    db.execute.side_effect = [conn_query_result, done_upload_query_result, upload_query_result]
    return db


@pytest.mark.asyncio
async def test_list_kb_sources_merges_connectors_and_uploads() -> None:
    """Happy path: 1 connector with items, 1 connector with no items, 1 upload."""
    from app.api.app_knowledge_bases import list_kb_sources

    org = _make_org()
    kb = _make_kb()
    conn_with_items = _make_connector("conn-1", "notion", "Productdocs")
    conn_empty = _make_connector("conn-2", "github", "Mono-repo", status="pending")

    db = _make_sources_db([conn_with_items, conn_empty])

    aggregates = {
        "connectors": [
            {"connector_id": "conn-1", "items_count": 5, "chunks_count": 23, "items_failed_count": 3},
        ],
        "uploads": [
            {
                "id": "art-1",
                "path": "report.pdf",
                "display_name": "Annual report",
                "source_url": None,
                "content_type": "pdf",
                "created_at": 1700000000,
                "chunks_count": 7,
                "index_status_changed_at": 1700005000,
            },
        ],
    }

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value=aggregates),
        ),
    ):
        result = await list_kb_sources(
            kb_slug="kb-a",
            perms=_make_perms(),
            db=db,
        )

    sources = result.sources
    assert len(sources) == 3  # conn-1 (with items), conn-2 (empty), 1 upload

    by_id = {b.id: b for b in sources}
    assert by_id["conn-1"].kind == "connector"
    assert by_id["conn-1"].items_count == 5
    assert by_id["conn-1"].chunks_count == 23
    assert by_id["conn-1"].items_failed_count == 3
    assert by_id["conn-1"].name == "Productdocs"
    assert by_id["conn-1"].type_label == "Notion"

    assert by_id["conn-2"].items_count == 0
    assert by_id["conn-2"].chunks_count == 0
    assert by_id["conn-2"].items_failed_count == 0
    assert by_id["conn-2"].status == "pending"

    assert by_id["art-1"].kind == "upload"
    assert by_id["art-1"].name == "Annual report"
    assert by_id["art-1"].type_label == "PDF"
    assert by_id["art-1"].source_url is None
    assert by_id["art-1"].chunks_count == 7
    assert by_id["art-1"].items_failed_count == 0
    assert by_id["art-1"].created_at is not None
    # index_status_changed_at (epoch) surfaces as last_sync_at so the frontend
    # measures "Bezig sinds Xm" from the (re)sync start, not artifact creation.
    assert by_id["art-1"].last_sync_at == datetime.fromtimestamp(1700005000, tz=UTC)


@pytest.mark.asyncio
async def test_list_kb_sources_labels_url_upload_as_website_with_source_url() -> None:
    from app.api.app_knowledge_bases import list_kb_sources

    org = _make_org()
    kb = _make_kb()
    db = _make_sources_db([])

    aggregates = {
        "connectors": [],
        "uploads": [
            {
                "id": "art-url",
                "path": "https://example.com/",
                "display_name": None,
                "source_url": "https://example.com/",
                "content_type": "web_page",
                "created_at": 1700000000,
                "chunks_count": 0,
                "index_status": "synced",
            },
        ],
    }

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value=aggregates),
        ),
    ):
        result = await list_kb_sources(
            kb_slug="kb-a",
            perms=_make_perms(),
            db=db,
        )

    bron = result.sources[0]
    assert bron.name == "https://example.com/"
    assert bron.type_label == "Websitepagina"
    assert bron.source_url == "https://example.com/"


@pytest.mark.asyncio
async def test_list_kb_sources_handles_orphan_connector_id() -> None:
    """Aggregates reference a connector that no longer exists in portal DB.

    Surface the row anyway so the user can clean it up via Geavanceerd.
    """
    from app.api.app_knowledge_bases import list_kb_sources

    org = _make_org()
    kb = _make_kb()

    db = _make_sources_db([])

    aggregates = {
        "connectors": [
            {"connector_id": "deleted-conn", "items_count": 3, "chunks_count": 10},
        ],
        "uploads": [],
    }

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value=aggregates),
        ),
    ):
        result = await list_kb_sources(
            kb_slug="kb-a",
            perms=_make_perms(),
            db=db,
        )

    assert len(result.sources) == 1
    bron = result.sources[0]
    assert bron.id == "deleted-conn"
    assert bron.status == "orphan"
    # Older knowledge-ingest versions may not emit items_failed_count yet —
    # absence must not crash and must default to 0.
    assert bron.items_failed_count == 0
    assert "verwijderde" in bron.name


@pytest.mark.asyncio
async def test_list_kb_sources_raises_503_on_ingest_failure() -> None:
    """Knowledge-ingest unreachable → 503, NOT a misleading empty list.

    Regression test for 2026-05-28 incident: a 1s portal-api timeout on the
    canonical Sources query (knowledge-ingest's join over artifacts +
    parent_chunks measured 14-28s cold) caused every upload to appear to
    silently vanish. The old fall-back-to-empty behavior is the bug — the
    user thinks their just-uploaded source disappeared. Fail-loud lets the
    frontend's isError branch render "Kon bronnen niet laden — probeer
    opnieuw" so the user knows it is a transient problem, not data loss.
    """
    from fastapi import HTTPException

    from app.api.app_knowledge_bases import list_kb_sources

    org = _make_org()
    kb = _make_kb()
    conn = _make_connector("conn-1", "notion", "Productdocs")

    db = _make_sources_db([conn])

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value=None),  # transport failure
        ),
        pytest.raises(HTTPException) as excinfo,
    ):
        await list_kb_sources(
            kb_slug="kb-a",
            perms=_make_perms(),
            db=db,
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == {"error_code": "knowledge_ingest_unreachable"}


@pytest.mark.asyncio
async def test_list_kb_sources_includes_processing_uploads_before_ingest_artifact_exists() -> None:
    """A 202-accepted file must be visible while docling is still processing."""

    from app.api.app_knowledge_bases import list_kb_sources

    org = _make_org()
    kb = _make_kb()
    upload = _make_upload()
    db = _make_sources_db([], [upload])

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value={"connectors": [], "uploads": []}),
        ),
    ):
        result = await list_kb_sources(
            kb_slug="kb-a",
            perms=_make_perms(),
            db=db,
        )

    assert len(result.sources) == 1
    bron = result.sources[0]
    assert bron.kind == "upload"
    assert bron.name == "Chemie Overal 4VWO.pdf"
    assert bron.type_label == "PDF"
    assert bron.items_count == 1
    assert bron.chunks_count == 0
    assert bron.status == "processing"


@pytest.mark.asyncio
async def test_list_kb_sources_includes_done_upload_when_active_artifact_missing() -> None:
    """Done uploads must not disappear when knowledge-ingest lost the active artifact."""

    from app.api.app_knowledge_bases import list_kb_sources

    org = _make_org()
    kb = _make_kb()
    upload = _make_upload(
        filename="Verantwoordelijkheden.pdf",
        status="done",
        artifact_id="artifact-stale",
        source_ref="file:sha256:stale",
    )
    db = _make_sources_db([], done_uploads=[upload])

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value={"connectors": [], "uploads": []}),
        ),
    ):
        result = await list_kb_sources(
            kb_slug="kb-a",
            perms=_make_perms(),
            db=db,
        )

    assert len(result.sources) == 1
    bron = result.sources[0]
    assert bron.kind == "upload"
    assert bron.id == "artifact-stale"
    assert bron.name == "Verantwoordelijkheden.pdf"
    assert bron.status == "done"
    assert bron.index_status == "not_synced"


# -- get_source_content -------------------------------------------------------


@pytest.mark.asyncio
async def test_get_source_content_rejects_invalid_kind() -> None:
    from app.api.app_knowledge_bases import get_source_content

    with pytest.raises(HTTPException) as exc:
        await get_source_content(
            kb_slug="kb-a",
            source_id="sid",
            kind="banana",
            limit=50,
            offset=0,
            perms=_make_perms(),
            db=AsyncMock(),
        )
    assert exc.value.status_code == 400
    assert "kind" in exc.value.detail


@pytest.mark.asyncio
async def test_get_source_content_rejects_limit_out_of_range() -> None:
    from app.api.app_knowledge_bases import get_source_content

    with pytest.raises(HTTPException) as exc:
        await get_source_content(
            kb_slug="kb-a",
            source_id="sid",
            kind="upload",
            limit=500,
            offset=0,
            perms=_make_perms(),
            db=AsyncMock(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_source_content_connector_returns_items() -> None:
    from app.api.app_knowledge_bases import get_source_content

    org = _make_org()
    kb = _make_kb()
    conn = _make_connector("conn-1", "notion")

    db = AsyncMock()
    conn_query_result = MagicMock()
    conn_query_result.scalar_one_or_none.return_value = conn
    db.execute.return_value = conn_query_result

    ingest_response = {
        "items": [
            {
                "id": "art-1",
                "path": "page-1",
                "content_type": "html",
                "created_at": 1700000000,
                "chunks_count": 3,
            }
        ],
        "total": 1,
        "limit": 50,
        "offset": 0,
    }

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.list_connector_items",
            new=AsyncMock(return_value=ingest_response),
        ),
    ):
        result = await get_source_content(
            kb_slug="kb-a",
            source_id="conn-1",
            kind="connector",
            limit=50,
            offset=0,
            perms=_make_perms(),
            db=db,
        )

    assert result.kind == "connector"
    assert len(result.items) == 1
    assert result.items[0].path == "page-1"
    assert result.items[0].chunks_count == 3
    assert result.total == 1
    assert result.chunks == []


@pytest.mark.asyncio
async def test_get_source_content_connector_404_when_not_in_kb() -> None:
    """Cross-tenant guard: connector ID that doesn't belong to this KB → 404."""
    from app.api.app_knowledge_bases import get_source_content

    org = _make_org()
    kb = _make_kb()

    db = AsyncMock()
    conn_query_result = MagicMock()
    conn_query_result.scalar_one_or_none.return_value = None
    db.execute.return_value = conn_query_result

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await get_source_content(
            kb_slug="kb-a",
            source_id="other-org-connector",
            kind="connector",
            limit=50,
            offset=0,
            perms=_make_perms(),
            db=db,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_source_content_upload_returns_chunks() -> None:
    from app.api.app_knowledge_bases import get_source_content

    org = _make_org()
    kb = _make_kb()
    db = AsyncMock()
    db.execute = AsyncMock()  # not used for uploads

    ingest_response = {
        "chunks": [
            {"id": 1, "position": 0, "text": "First chunk", "token_count": 50},
            {"id": 2, "position": 1, "text": "Second chunk", "token_count": 60},
        ],
        "total": 2,
        "limit": 50,
        "offset": 0,
    }

    with (
        patch(
            "app.api.app_knowledge_bases._load_org_or_500",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.api.app_knowledge_bases._get_kb_or_404",
            new=AsyncMock(return_value=kb),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.list_upload_chunks",
            new=AsyncMock(return_value=ingest_response),
        ),
    ):
        result = await get_source_content(
            kb_slug="kb-a",
            source_id="art-1",
            kind="upload",
            limit=50,
            offset=0,
            perms=_make_perms(),
            db=db,
        )

    assert result.kind == "upload"
    assert len(result.chunks) == 2
    assert result.chunks[0].text == "First chunk"
    assert result.chunks[0].position == 0
    assert result.total == 2
    assert result.items == []


# -- Timeout choice regression (2026-05-28 personal-KB silent-empty incident) --


def test_get_kb_sources_uses_canonical_not_derived_timeout() -> None:
    """``get_kb_sources`` MUST use the canonical (30s) timeout, not the
    derived-metric (1s) one.

    The Sources tab IS this response, not an enrichment badge. A 1s budget
    guaranteed every cold call timed out (production query measured 14-28s
    cold, 13ms warm on 2026-05-28). The portal silently swallowed the
    timeout and returned an empty list, making every just-uploaded source
    appear to vanish.

    This pin protects against future refactors that switch the call back
    to ``_DERIVED_READ_TIMEOUT`` thinking it is "just enrichment".
    """
    from pathlib import Path

    client_file = Path(__file__).parent.parent / "app" / "services" / "knowledge_ingest_client.py"
    source = client_file.read_text(encoding="utf-8")

    # Find the get_kb_sources function body and assert it carries the
    # canonical timeout name. We do not assert the literal number — the
    # value can be re-tuned without changing the contract this test pins.
    fn_start = source.index("async def get_kb_sources(")
    fn_end = source.index("\nasync def ", fn_start + 1)
    body = source[fn_start:fn_end]

    assert "timeout=_CANONICAL_READ_TIMEOUT" in body, (
        "get_kb_sources must use _CANONICAL_READ_TIMEOUT (30s) — "
        "this endpoint backs the Sources tab and 1s guarantees cold-call "
        "timeouts on the artifacts+parent_chunks join. See "
        "klai-portal/backend/app/services/knowledge_ingest_client.py."
    )
    assert "timeout=_DERIVED_READ_TIMEOUT" not in body, (
        "get_kb_sources must NOT use _DERIVED_READ_TIMEOUT (1s) — see 2026-05-28 personal-KB silent-empty regression."
    )
