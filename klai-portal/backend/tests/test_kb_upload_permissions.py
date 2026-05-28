"""SPEC-PORTAL-KENNIS-002 Track 3 — KB upload permission gates (portal-api side).

Tests for the B1 viewer gate and B2 ownership routing on:
  - POST /knowledge-bases/{kb_slug}/uploads/{artifact_id}/reindex  (202)
  - DELETE /knowledge-bases/{kb_slug}/uploads/{artifact_id}        (204)

Roles under test: contributor (202/204 own) | owner (202/204 any) | viewer (403).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_KB_SLUG = "test-kb"
_ARTIFACT_ID = "art-abc123"
_ORG_ID = 1
_USER_ID = "user-contributor"
_OWNER_USER_ID = "user-owner"


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = _ORG_ID
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb() -> MagicMock:
    kb = MagicMock()
    kb.id = 42
    kb.slug = _KB_SLUG
    kb.org_id = _ORG_ID
    kb.created_by = _OWNER_USER_ID
    kb.default_org_role = "viewer"
    return kb


def _make_db(kb_upload: object | None = None) -> AsyncMock:
    """Build a mock AsyncSession.

    Pass ``kb_upload=row`` to simulate the KBUpload-by-id lookup hitting; the
    default ``None`` makes that lookup return no row so callers exercise the
    legacy artifact-id fallback path in ``delete_kb_upload``.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=kb_upload)
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# reindex_upload — B1 viewer gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reindex_upload_contributor_returns_202() -> None:
    """Contributor role returns 202 and enqueues the artifact."""
    from app.api.app_knowledge_bases import reindex_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id=_USER_ID, org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="contributor"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.reindex_artifact",
            new=AsyncMock(return_value=None),
        ) as mock_reindex,
    ):
        result = await reindex_upload(
            kb_slug=_KB_SLUG,
            artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    assert result == {"artifact_id": _ARTIFACT_ID, "status": "pending"}
    mock_reindex.assert_awaited_once_with(org.zitadel_org_id, _ARTIFACT_ID)


@pytest.mark.asyncio
async def test_reindex_upload_owner_returns_202() -> None:
    """Owner role also returns 202."""
    from app.api.app_knowledge_bases import reindex_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.reindex_artifact",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await reindex_upload(
            kb_slug=_KB_SLUG,
            artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_reindex_upload_viewer_raises_403() -> None:
    """Viewer role raises HTTP 403 — no reindex call is made."""
    from app.api.app_knowledge_bases import reindex_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id="user-viewer", org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="viewer"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.reindex_artifact",
            new=AsyncMock(return_value=None),
        ) as mock_reindex,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await reindex_upload(
                kb_slug=_KB_SLUG,
                artifact_id=_ARTIFACT_ID,
                perms=perms,
                db=_make_db(),
            )

    assert exc_info.value.status_code == 403
    mock_reindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_upload_no_role_raises_403() -> None:
    """None (not a KB member) also raises 403."""
    from app.api.app_knowledge_bases import reindex_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id="user-stranger", org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.reindex_artifact",
            new=AsyncMock(return_value=None),
        ) as mock_reindex,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await reindex_upload(
                kb_slug=_KB_SLUG,
                artifact_id=_ARTIFACT_ID,
                perms=perms,
                db=_make_db(),
            )

    assert exc_info.value.status_code == 403
    mock_reindex.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_kb_upload — B1 viewer gate + B2 ownership routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_upload_contributor_passes_user_id() -> None:
    """Contributor role: user_id is forwarded so knowledge-ingest enforces
    ownership — only the contributor's own uploads may be deleted.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id=_USER_ID, org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="contributor"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb_upload",
            new=AsyncMock(return_value=None),
        ) as mock_delete,
    ):
        result = await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    # DELETE 204 returns None
    assert result is None
    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id=_USER_ID,  # B2: contributor's own user_id forwarded
    )


@pytest.mark.asyncio
async def test_delete_upload_owner_passes_none_user_id() -> None:
    """Owner role: user_id is omitted (None) so knowledge-ingest allows
    cross-user deletes — owners may delete any upload in the KB.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb_upload",
            new=AsyncMock(return_value=None),
        ) as mock_delete,
    ):
        result = await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    assert result is None
    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id=None,  # B2: owner may delete any upload
    )


@pytest.mark.asyncio
async def test_delete_upload_viewer_raises_403() -> None:
    """Viewer role raises HTTP 403 — no delete call is made."""
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id="user-viewer", org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="viewer"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb_upload",
            new=AsyncMock(return_value=None),
        ) as mock_delete,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await delete_kb_upload(
                kb_slug=_KB_SLUG,
                upload_or_artifact_id=_ARTIFACT_ID,
                perms=perms,
                db=_make_db(),
            )

    assert exc_info.value.status_code == 403
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_upload_no_role_raises_403() -> None:
    """None role (not a KB member at all) raises 403."""
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id="user-stranger", org_id=_ORG_ID)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb_upload",
            new=AsyncMock(return_value=None),
        ) as mock_delete,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await delete_kb_upload(
                kb_slug=_KB_SLUG,
                upload_or_artifact_id=_ARTIFACT_ID,
                perms=perms,
                db=_make_db(),
            )

    assert exc_info.value.status_code == 403
    mock_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_kb_upload — KBUpload-by-id path (2026-05-28 incident, processing rows)
# ---------------------------------------------------------------------------


def _make_kb_upload(
    *,
    upload_id: str = "11111111-2222-3333-4444-555555555555",
    artifact_id: str | None = None,
    status_value: str = "processing",
    created_by: str = _USER_ID,
) -> MagicMock:
    upload = MagicMock()
    upload.id = upload_id
    upload.artifact_id = artifact_id
    upload.status = status_value
    upload.created_by = created_by
    return upload


@pytest.mark.asyncio
async def test_delete_processing_upload_deletes_kb_upload_row_no_artifact_call() -> None:
    """An in-flight upload (status=processing, no artifact) → delete the
    kb_uploads row and SKIP the knowledge-ingest call.

    Regression test for the 2026-05-28 silent-no-op incident: previously
    the kb_uploads.id was sent to knowledge-ingest as artifact_id → 404 →
    kb_uploads row never removed → row reappears on next refresh.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    upload = _make_kb_upload(artifact_id=None, status_value="processing")
    db = _make_db(kb_upload=upload)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb_upload",
            new=AsyncMock(return_value=None),
        ) as mock_delete,
    ):
        result = await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=str(upload.id),
            perms=perms,
            db=db,
        )

    assert result is None
    db.delete.assert_awaited_once_with(upload)
    db.commit.assert_awaited()
    # Critical: no artifact-side delete call because no artifact exists yet.
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_done_upload_cascades_to_artifact_then_deletes_row() -> None:
    """A finished upload (status=done, has artifact_id) → cascade-delete
    the artifact via knowledge-ingest AND delete the local kb_uploads row.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    upload = _make_kb_upload(artifact_id="art-xyz", status_value="done")
    db = _make_db(kb_upload=upload)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb_upload",
            new=AsyncMock(return_value=None),
        ) as mock_delete,
    ):
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=str(upload.id),
            perms=perms,
            db=db,
        )

    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        "art-xyz",
        user_id=None,  # owner
    )
    db.delete.assert_awaited_once_with(upload)


@pytest.mark.asyncio
async def test_delete_processing_upload_contributor_other_user_403() -> None:
    """Contributor cannot delete another contributor's in-flight upload.

    Mirrors the artifact-side X-User-ID check for kb_uploads rows that
    have not produced an artifact yet.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="personal", user_id="someone-else", org_id=_ORG_ID)
    upload = _make_kb_upload(created_by=_USER_ID)  # owned by user-contributor
    db = _make_db(kb_upload=upload)

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
            "app.api.app_knowledge_bases.get_user_role_for_kb",
            new=AsyncMock(return_value="contributor"),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await delete_kb_upload(
                kb_slug=_KB_SLUG,
                upload_or_artifact_id=str(upload.id),
                perms=perms,
                db=db,
            )

    assert exc_info.value.status_code == 403
    db.delete.assert_not_awaited()
