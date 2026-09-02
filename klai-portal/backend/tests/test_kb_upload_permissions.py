"""SPEC-PORTAL-KENNIS-002 Track 3 — KB upload permission gates (portal-api side).

Tests for the B1 viewer gate and B2 ownership routing on:
  - POST /knowledge-bases/{kb_slug}/uploads/{artifact_id}/reindex  (202)
  - DELETE /knowledge-bases/{kb_slug}/uploads/{artifact_id}        (204)

Roles under test: contributor (202/204 own) | owner (202/204 any) | viewer (403).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from tests.conftest import make_perms

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_KB_SLUG = "test-kb"
_ARTIFACT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ORG_ID = 1
_USER_ID = "user-contributor"
_OWNER_USER_ID = "user-owner"


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = _ORG_ID
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb(*, owner_type: str = "org", default_org_role: str | None = "viewer") -> MagicMock:
    kb = MagicMock()
    kb.id = 42
    kb.slug = _KB_SLUG
    kb.org_id = _ORG_ID
    kb.created_by = _OWNER_USER_ID
    kb.owner_type = owner_type
    kb.default_org_role = default_org_role
    return kb


def _make_db(
    kb_upload: object | None = None,
    *,
    artifact_upload: object | None = None,
) -> AsyncMock:
    """Build a mock AsyncSession.

    Pass ``kb_upload=row`` to simulate the KBUpload-by-id lookup hitting; the
    default ``None`` makes that lookup return no row so callers exercise the
    legacy artifact-id fallback path in ``delete_kb_upload``. Pass
    ``artifact_upload=row`` to simulate the by-id lookup missing and the
    artifact_id lookup hitting.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    id_result = MagicMock()
    id_result.scalar_one_or_none = MagicMock(return_value=kb_upload)
    # Deleting a tracked upload also cancels any in-flight replacement for the
    # same source, which is one more execute() — a DELETE returning a rowcount.
    id_result.rowcount = 0
    if artifact_upload is None:
        db.execute = AsyncMock(return_value=id_result)
    else:
        artifact_result = MagicMock()
        artifact_result.scalar_one_or_none = MagicMock(return_value=artifact_upload)
        cancel_result = MagicMock()
        cancel_result.rowcount = 0
        db.execute = AsyncMock(side_effect=[id_result, artifact_result, cancel_result])
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
    source_ref: str = "file:sha256:upload",
    target_path: str | None = None,
) -> MagicMock:
    upload = MagicMock()
    upload.id = upload_id
    upload.artifact_id = artifact_id
    upload.status = status_value
    upload.created_by = created_by
    upload.source_ref = source_ref
    upload.target_path = target_path
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
async def test_delete_done_upload_by_artifact_id_deletes_kb_upload_row() -> None:
    """A finished upload rendered in the Sources tab sends artifact_id.

    The endpoint must still delete the matching kb_uploads row; otherwise
    list_kb_sources re-surfaces it as a stale done upload after the artifact
    is removed.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    upload = _make_kb_upload(artifact_id=_ARTIFACT_ID, status_value="done")
    db = _make_db(kb_upload=None, artifact_upload=upload)

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
            db=db,
        )

    assert result is None
    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id=None,
    )
    db.delete.assert_awaited_once_with(upload)
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_delete_stale_done_upload_by_artifact_id_treats_ingest_404_as_success() -> None:
    """A stale done upload has a kb_uploads row but no active ingest artifact.

    Deleting it should remove the local row even when knowledge-ingest says
    the artifact is already gone.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    upload = _make_kb_upload(artifact_id=_ARTIFACT_ID, status_value="done")
    db = _make_db(kb_upload=None, artifact_upload=upload)
    not_found = httpx.HTTPStatusError(
        "not found",
        request=httpx.Request("DELETE", "http://knowledge-ingest/uploads/art"),
        response=httpx.Response(404),
    )

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
            new=AsyncMock(side_effect=not_found),
        ) as mock_delete,
    ):
        result = await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=db,
        )

    assert result is None
    mock_delete.assert_awaited_once()
    db.delete.assert_awaited_once_with(upload)
    db.commit.assert_awaited()


# ---------------------------------------------------------------------------
# delete_kb_upload — kb_manager source-manage pad (2026-08-19)
#
# A kb_manager could already delete a CONNECTOR on an org KB it did not create
# (require_connector_manage_access, Voys/Ascend fix 2026-08-14) but not the
# upload sitting next to it in the same Sources list, because the upload's
# delete forwarded user_id and knowledge-ingest rejected the cross-user delete.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_upload_kb_manager_contributor_on_org_kb_passes_none_user_id() -> None:
    """kb_manager with contributor access on an org KB deletes ANY upload.

    Regression: previously user_id was forwarded, so knowledge-ingest 403'd
    on a source uploaded by a colleague.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb(default_org_role="contributor")
    perms = make_perms(role="kb_manager", user_id="user-kb-manager", org_id=_ORG_ID)

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

    assert result is None
    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id=None,  # cross-user delete allowed
    )


@pytest.mark.asyncio
async def test_delete_upload_kb_manager_resolved_viewer_role_still_403() -> None:
    """A resolved viewer KB-role still wins — the KB-role layer is preserved.

    Note the resolver, not the grant, is what this pins. ``get_user_role_for_kb``
    returns max(explicit grant, default_org_role), so on a KB with
    ``default_org_role='contributor'`` an explicit viewer grant does NOT
    resolve to viewer. That is a pre-existing property of the resolver shared
    with connector CRUD, not something this gate can compensate for.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="kb_manager", user_id="user-kb-manager", org_id=_ORG_ID)

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
async def test_delete_upload_kb_manager_on_chat_seat_stays_own_uploads_only() -> None:
    """Seat filtering applies: a kb_manager on a CHAT seat loses the widening.

    ``app.core.seats.effective_capabilities`` drops KB_CONNECTORS_EXTERNAL for
    (kb_manager, CHAT) — reachable via the PATCH /seat escape hatch. The gate
    must read ``perms.effective_capabilities`` (what ``require_capability``
    gates on), not re-derive capabilities from the profile role.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb(default_org_role="contributor")
    perms = make_perms(
        role="kb_manager",
        seat_type="chat",
        user_id="user-kb-manager",
        org_id=_ORG_ID,
    )
    assert "kb.connectors.external" not in perms.effective_capabilities

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
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id="user-kb-manager",  # still ownership-scoped
    )


@pytest.mark.asyncio
async def test_delete_upload_kb_manager_on_personal_kb_stays_own_uploads_only() -> None:
    """Personal KBs get no kb_manager widening — owner_type='user' is excluded."""
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb(owner_type="user", default_org_role=None)
    perms = make_perms(role="kb_manager", user_id="user-kb-manager", org_id=_ORG_ID)

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
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id="user-kb-manager",  # still ownership-scoped
    )


@pytest.mark.asyncio
async def test_delete_upload_company_profile_contributor_still_own_uploads_only() -> None:
    """A company profile lacks KB_CONNECTORS_EXTERNAL — no widening for it."""
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb(default_org_role="contributor")
    perms = make_perms(role="company", user_id=_USER_ID, org_id=_ORG_ID)

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
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=_ARTIFACT_ID,
            perms=perms,
            db=_make_db(),
        )

    mock_delete.assert_awaited_once_with(
        org.zitadel_org_id,
        kb.slug,
        _ARTIFACT_ID,
        user_id=_USER_ID,
    )


@pytest.mark.asyncio
async def test_delete_processing_upload_kb_manager_deletes_other_users_row() -> None:
    """kb_manager may delete a colleague's still-processing kb_uploads row.

    The in-flight branch has its own created_by check; it must follow the same
    pad as the artifact branch or the two disagree mid-lifecycle.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb(default_org_role="contributor")
    perms = make_perms(role="kb_manager", user_id="user-kb-manager", org_id=_ORG_ID)
    upload = _make_kb_upload(created_by=_USER_ID)  # someone else's upload
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
        result = await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=str(upload.id),
            perms=perms,
            db=db,
        )

    assert result is None
    db.delete.assert_awaited_once_with(upload)
    db.commit.assert_awaited()


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


@pytest.mark.asyncio
async def test_delete_clears_every_tracking_row_for_the_deleted_document() -> None:
    """Deleting a source drops every version row AND any pending replacement.

    A version left behind resurfaces the deleted source as a stale row; a
    replacement left behind gets finished by the poller minutes later and
    re-creates the source the user removed, with content they never approved.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    upload = _make_kb_upload(
        artifact_id=_ARTIFACT_ID,
        status_value="done",
        source_ref="file:sha256:original",
    )
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
        ),
        patch(
            "app.api.app_knowledge_bases.kb_uploads_repo.delete_rows_for_document",
            new=AsyncMock(return_value=1),
        ) as mock_cancel,
    ):
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=str(upload.id),
            perms=perms,
            db=db,
        )

    mock_cancel.assert_awaited_once()
    assert mock_cancel.await_args.kwargs["path"] == "file:sha256:original"
    assert mock_cancel.await_args.kwargs["except_id"] == upload.id


@pytest.mark.asyncio
async def test_delete_keys_cleanup_on_the_document_path_not_the_row_hash() -> None:
    """The document key of a replacement row is its target_path, not source_ref."""
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    upload = _make_kb_upload(
        artifact_id=_ARTIFACT_ID,
        status_value="done",
        source_ref="file:sha256:second-version",
        target_path="file:sha256:original",
    )
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
        ),
        patch(
            "app.api.app_knowledge_bases.kb_uploads_repo.delete_rows_for_document",
            new=AsyncMock(return_value=0),
        ) as mock_cancel,
    ):
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=str(upload.id),
            perms=perms,
            db=db,
        )

    assert mock_cancel.await_args.kwargs["path"] == "file:sha256:original"


@pytest.mark.asyncio
async def test_discarding_a_failed_replacement_leaves_the_retry_alone() -> None:
    """Clearing a failed attempt must not cancel the retry for the same source.

    The failed row carries no artifact of its own — the live source is still
    there and untouched. Running the document-wide cleanup here would delete
    the replacement the user started right after the failure.
    """
    from app.api.app_knowledge_bases import delete_kb_upload

    org = _make_org()
    kb = _make_kb()
    perms = make_perms(role="admin", user_id=_OWNER_USER_ID, org_id=_ORG_ID)
    failed_attempt = _make_kb_upload(
        artifact_id=None,
        status_value="failed",
        source_ref="file:sha256:attempt",
        target_path="file:sha256:original",
    )
    db = _make_db(kb_upload=failed_attempt)

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
        ) as mock_ingest_delete,
        patch(
            "app.api.app_knowledge_bases.kb_uploads_repo.delete_rows_for_document",
            new=AsyncMock(return_value=0),
        ) as mock_cleanup,
    ):
        await delete_kb_upload(
            kb_slug=_KB_SLUG,
            upload_or_artifact_id=str(failed_attempt.id),
            perms=perms,
            db=db,
        )

    mock_cleanup.assert_not_awaited()
    mock_ingest_delete.assert_not_awaited()
    db.delete.assert_awaited_once_with(failed_attempt)
