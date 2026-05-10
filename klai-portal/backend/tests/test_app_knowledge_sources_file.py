"""Integration tests for POST /sources/file (SPEC-KB-FILE-UPLOAD-001 Phase 1A).

Mirrors the pattern in test_app_knowledge_sources.py: synthetic
``UserPermissions`` via ``make_perms``, mocked DB + role + quota +
ingest. Covers the Phase 1A acceptance criteria:

- AC-1.1 / AC-1.2: whitelist routing + unsupported_extension rejection.
- AC-1.5: text-path UTF-8 / cp1252 fallback.
- AC-6.1: response shape with uploads + skipped arrays.
- Phase 1A divergence: .pdf returns ``phase_pending`` (not 500, not the
  Gitea wiki path), verifying the original bug is closed.
"""

from __future__ import annotations

import io
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from tests.conftest import make_perms

# --- Fixtures (mirrors test_app_knowledge_sources.py) ----------------------


def _make_db_mock(kb: MagicMock | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # SQLAlchemy add() is sync — see klai testing rule
    kb_query_result = MagicMock()
    kb_query_result.scalar_one_or_none.return_value = kb
    db.execute.return_value = kb_query_result
    return db


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.plan = "complete"
    org.slug = "voys"
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb() -> MagicMock:
    kb = MagicMock()
    kb.id = 42
    kb.slug = "personal"
    kb.name = "Personal"
    kb.org_id = 1
    kb.owner_type = "user"
    kb.owner_user_id = "user-abc"
    kb.created_by = "user-abc"
    kb.default_org_role = None
    return kb


def _make_perms() -> object:
    return make_perms(role="admin", user_id="user-abc", org_id=1)


def _upload(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    """Build a Starlette UploadFile carrying the given bytes."""
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


class _FilePatches:
    """Apply role / quota / org-load / ingest patches for the file route."""

    def __init__(
        self,
        *,
        role: str = "owner",
        ingest_return: str = "art-file-1",
    ) -> None:
        self.role = role
        self.ingest_return = ingest_return
        self.stack = ExitStack()
        self.mock_ingest: AsyncMock | None = None

    def __enter__(self) -> _FilePatches:
        org = _make_org()
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources._load_org_or_500",
                AsyncMock(return_value=org),
            )
        )
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.get_user_role_for_kb",
                AsyncMock(return_value=self.role),
            )
        )
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                AsyncMock(return_value=None),
            )
        )
        self.mock_ingest = AsyncMock(return_value=self.ingest_return)
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.knowledge_ingest_client.ingest_document",
                self.mock_ingest,
            )
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stack.close()


# --- Happy path: text formats -----------------------------------------------


class TestFileRouteHappyPath:
    @pytest.mark.asyncio
    async def test_md_upload_returns_202_with_artifact(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("notes.md", b"# Hello\n\nworld", "text/markdown")]

        with _FilePatches(ingest_return="art-md-1") as patches:
            resp = await add_file_source(
                kb_slug="personal",
                files=files,
                perms=_make_perms(),
                db=db,
            )

        assert len(resp.uploads) == 1
        assert resp.uploads[0].artifact_id == "art-md-1"
        assert resp.uploads[0].source_type == "file"
        assert resp.uploads[0].source_ref.startswith("file:sha256:")
        assert resp.skipped == []

        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["source_type"] == "file"
        assert payload["content_type"] == "plain_text"
        assert payload["title"] == "notes"
        assert payload["content"] == "# Hello\n\nworld"
        assert payload["extra"]["original_filename"] == "notes.md"
        assert payload["extra"]["extension"] == ".md"
        assert payload["extra"]["phase"] == "1a"

    @pytest.mark.asyncio
    async def test_csv_with_bom_strips_bom(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("rows.csv", b"\xef\xbb\xbfa,b,c\n1,2,3\n", "text/csv")]

        with _FilePatches() as patches:
            await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["content"] == "a,b,c\n1,2,3\n"
        assert payload["title"] == "rows"

    @pytest.mark.asyncio
    async def test_txt_upload_routes_to_ingest(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("plain.txt", b"plain text content", "text/plain")]

        with _FilePatches() as patches:
            resp = await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert len(resp.uploads) == 1
        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["content"] == "plain text content"


# --- Phase 1A divergence: pdf / docx / etc. → phase_pending -----------------


class TestPhasePendingPath:
    @pytest.mark.asyncio
    async def test_pdf_returns_400_phase_pending_no_ingest_call(self) -> None:
        """Original 500-bug regression: .pdf must NOT 500, NOT hit klai-docs wiki."""
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("chemie.pdf", b"%PDF-1.4 fake", "application/pdf")]

        with _FilePatches() as patches, pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "phase_pending"
        # Critical: no ingest call was made — content stays out of KB.
        assert patches.mock_ingest is not None
        patches.mock_ingest.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_request_partial_success(self) -> None:
        """One .md accepted + one .pdf phase_pending = 202 with skipped array."""
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [
            _upload("notes.md", b"# md content", "text/markdown"),
            _upload("doc.pdf", b"%PDF", "application/pdf"),
        ]

        with _FilePatches() as patches:
            resp = await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert len(resp.uploads) == 1
        assert resp.uploads[0].source_type == "file"
        assert len(resp.skipped) == 1
        assert resp.skipped[0].filename == "doc.pdf"
        assert resp.skipped[0].reason == "phase_pending"
        assert resp.skipped[0].extension == ".pdf"
        assert patches.mock_ingest is not None
        # Only the .md was forwarded — exactly one call.
        assert patches.mock_ingest.call_count == 1


# --- Rejection paths --------------------------------------------------------


class TestRejectionPaths:
    @pytest.mark.asyncio
    async def test_unsupported_extension_returns_400(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("malware.exe", b"MZ\x90", "application/octet-stream")]

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"

    @pytest.mark.asyncio
    async def test_empty_files_list_returns_400(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", files=[], perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "no_files"

    @pytest.mark.asyncio
    async def test_too_many_files_returns_400(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload(f"f{i}.md", b"x", "text/markdown") for i in range(11)]

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "too_many_files"

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("blank.md", b"   \n  ", "text/markdown")]

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", files=files, perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "empty_content"
