"""Integration tests for POST /sources/file (SPEC-KB-FILE-UPLOAD-001).

Covers the three pipelines exposed by the route:

- **text** (``.md / .txt / .csv``): synchronous decode + forward to
  ``/ingest/v1/document``. Row created in ``done`` state.
- **docling** (``.pdf / .docx / .xlsx / .pptx / .json / .xml``): magic-
  byte validate + submit to docling-serve async queue. Row created in
  ``processing`` state with the ``task_id``.
- **archive** (``.zip / .tar``): safely extracted as an all-or-nothing
  batch; each member recurses through the text/docling paths.
- **phase_pending** (``.doc``): recorded as ``skipped`` with that reason.

The tests mock ``knowledge_ingest_client.ingest_document``,
``docling_client.submit_file_async`` and ``kb_uploads_repo`` helpers
so the route logic is exercised in isolation. Real DB / docling /
knowledge-ingest integration is covered by the live e2e smoke.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

from app.services.docling_client import DoclingError, DoclingSubmitResult
from app.services.kb_uploads_repo import (
    STATUS_DONE,
    STATUS_PROCESSING,
    KBUploadView,
)
from tests.conftest import make_perms

# A minimal valid PDF that ``filetype.guess`` recognises as
# ``application/pdf``.
_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


# --- Fixtures --------------------------------------------------------------


def _make_db_mock(kb: MagicMock | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # SQLAlchemy add() is sync
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


def _make_upload_view(
    *,
    upload_id: uuid.UUID | None = None,
    kb_id: int = 42,
    org_id: int = 1,
    filename: str = "f.md",
    extension: str = ".md",
    mime: str = "text/plain",
    bytes_count: int = 10,
    source_ref: str = "file:sha256:abc",
    status: str = STATUS_DONE,
    artifact_id: str | None = "art-1",
    docling_task_id: str | None = None,
    failure_reason: str | None = None,
) -> KBUploadView:
    from datetime import UTC, datetime

    return KBUploadView(
        id=upload_id or uuid.uuid4(),
        kb_id=kb_id,
        org_id=org_id,
        created_by="user-abc",
        filename=filename,
        extension=extension,
        mime=mime,
        bytes=bytes_count,
        source_ref=source_ref,
        status=status,
        failure_reason=failure_reason,
        docling_task_id=docling_task_id,
        artifact_id=artifact_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _upload(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )


def _make_request(files: list[UploadFile]) -> MagicMock:
    """Build a mock Request whose form() returns the given UploadFiles."""
    from starlette.datastructures import FormData

    form_items: list[tuple[str, UploadFile]] = [("files", f) for f in files]
    request = MagicMock()
    request.form = AsyncMock(return_value=FormData(form_items))
    return request


def _make_multipart_request(filename: str, content: bytes, content_type: str) -> Request:
    """Build a real Starlette Request so request.form() creates UploadFile."""

    boundary = "----klai-test-boundary"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n"
            "\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    chunks = [body[i : i + 64 * 1024] for i in range(0, len(body), 64 * 1024)]

    async def receive() -> dict[str, object]:
        if chunks:
            return {
                "type": "http.request",
                "body": chunks.pop(0),
                "more_body": bool(chunks),
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        },
        receive,
    )


def _build_zip(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buffer.getvalue()


class _FilePatches:
    """Bundle the ingest + docling + repo patches for the file route."""

    def __init__(
        self,
        *,
        role: str = "owner",
        ingest_artifact_id: str = "art-md-1",
        docling_task_id: str = "task-pdf-1",
        docling_submit_side_effect: Exception | None = None,
    ) -> None:
        self.role = role
        self.ingest_artifact_id = ingest_artifact_id
        self.docling_task_id = docling_task_id
        self.docling_submit_side_effect = docling_submit_side_effect
        self.stack = ExitStack()
        self.mock_ingest: AsyncMock | None = None
        self.mock_docling: AsyncMock | None = None
        self.mock_create_upload: AsyncMock | None = None

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

        self.mock_ingest = AsyncMock(return_value=self.ingest_artifact_id)
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.knowledge_ingest_client.ingest_document",
                self.mock_ingest,
            )
        )

        if self.docling_submit_side_effect is not None:
            self.mock_docling = AsyncMock(side_effect=self.docling_submit_side_effect)
        else:
            self.mock_docling = AsyncMock(
                return_value=DoclingSubmitResult(
                    task_id=self.docling_task_id,
                    initial_status="pending",
                )
            )
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.docling_client.submit_file_async",
                self.mock_docling,
            )
        )

        # kb_uploads_repo.create_upload — return a fresh view per call
        # so each upload has its own UUID. Capture the kwargs so tests
        # can assert what was persisted.
        async def _create_upload_stub(
            db: object,
            **kwargs: object,
        ) -> KBUploadView:
            return _make_upload_view(
                kb_id=int(kwargs.get("kb_id", 42)),
                org_id=int(kwargs.get("org_id", 1)),
                filename=str(kwargs.get("filename", "f")),
                extension=str(kwargs.get("extension", ".md")),
                mime=str(kwargs.get("mime", "text/plain")),
                bytes_count=int(kwargs.get("bytes_count", 0)),
                source_ref=str(kwargs.get("source_ref", "file:sha256:x")),
                status=str(kwargs.get("status", STATUS_DONE)),
                artifact_id=kwargs.get("artifact_id"),  # type: ignore[arg-type]
                docling_task_id=kwargs.get("docling_task_id"),  # type: ignore[arg-type]
            )

        self.mock_create_upload = AsyncMock(side_effect=_create_upload_stub)
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.kb_uploads_repo.create_upload",
                self.mock_create_upload,
            )
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stack.close()


# --- Text pipeline ---------------------------------------------------------


class TestTextPipeline:
    @pytest.mark.asyncio
    async def test_md_upload_returns_done_with_artifact(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("notes.md", b"# Hello\n\nworld", "text/markdown")]

        with _FilePatches(ingest_artifact_id="art-md-99") as patches:
            resp = await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert len(resp.uploads) == 1
        assert resp.uploads[0].status == "done"
        assert resp.uploads[0].artifact_id == "art-md-99"
        assert resp.uploads[0].filename == "notes.md"
        assert resp.uploads[0].source_type == "file"
        assert resp.uploads[0].source_ref.startswith("file:sha256:")
        assert resp.skipped == []

        # ingest forwarded once with the right shape
        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["source_type"] == "file"
        assert payload["content_type"] == "plain_text"
        assert payload["title"] == "notes"
        assert payload["content"] == "# Hello\n\nworld"
        assert payload["user_id"] == "user-abc"
        assert payload["extra"]["pipeline"] == "text"

        # kb_uploads row created in 'done' state
        assert patches.mock_create_upload is not None
        kwargs = patches.mock_create_upload.call_args.kwargs
        assert kwargs["status"] == STATUS_DONE
        assert kwargs["artifact_id"] == "art-md-99"

    @pytest.mark.asyncio
    async def test_csv_with_bom_strips_bom(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("rows.csv", b"\xef\xbb\xbfa,b\n1,2\n", "text/csv")]

        with _FilePatches() as patches:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["content"] == "a,b\n1,2\n"


# --- Docling pipeline ------------------------------------------------------


class TestDoclingPipeline:
    @pytest.mark.asyncio
    async def test_pdf_submits_to_docling_returns_processing(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("chemie.pdf", _TINY_PDF, "application/pdf")]

        with _FilePatches(docling_task_id="docling-task-xyz") as patches:
            resp = await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert len(resp.uploads) == 1
        assert resp.uploads[0].status == "processing"
        assert resp.uploads[0].artifact_id is None
        assert resp.uploads[0].filename == "chemie.pdf"
        assert resp.skipped == []

        # docling submitted once
        assert patches.mock_docling is not None
        kwargs = patches.mock_docling.call_args.kwargs
        assert kwargs["filename"] == "chemie.pdf"
        assert kwargs["content"] == _TINY_PDF
        assert kwargs["content_type"] == "application/pdf"

        # ingest NOT called yet — that happens in the poller
        assert patches.mock_ingest is not None
        patches.mock_ingest.assert_not_called()

        # kb_uploads row created in 'processing' with task_id
        assert patches.mock_create_upload is not None
        kwargs = patches.mock_create_upload.call_args.kwargs
        assert kwargs["status"] == STATUS_PROCESSING
        assert kwargs["docling_task_id"] == "docling-task-xyz"

    @pytest.mark.asyncio
    async def test_real_starlette_multipart_pdf_is_accepted(self) -> None:
        """Regression: request.form() returns Starlette UploadFile instances."""

        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        content = _TINY_PDF + (b"0" * (2 * 1024 * 1024))
        request = _make_multipart_request("chemie.pdf", content, "application/pdf")

        with _FilePatches(docling_task_id="docling-task-real-form") as patches:
            resp = await add_file_source(kb_slug="personal", request=request, perms=_make_perms(), db=db)

        assert len(resp.uploads) == 1
        assert resp.uploads[0].status == "processing"
        assert resp.uploads[0].filename == "chemie.pdf"
        assert patches.mock_docling is not None
        kwargs = patches.mock_docling.call_args.kwargs
        assert kwargs["filename"] == "chemie.pdf"
        assert kwargs["content"] == content
        assert kwargs["content_type"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_mime_mismatch_skipped_no_docling_call(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        # GIF magic bytes uploaded as .pdf — magic-byte check rejects it.
        files = [_upload("fake.pdf", b"GIF89a\x00\x00\x00\x00", "application/pdf")]

        with _FilePatches() as patches, pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "mime_mismatch"
        # Critical: no docling submission was made for spoofed content.
        assert patches.mock_docling is not None
        patches.mock_docling.assert_not_called()

    @pytest.mark.asyncio
    async def test_docling_submit_failure_skipped(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("ok.pdf", _TINY_PDF, "application/pdf")]

        with (
            _FilePatches(
                docling_submit_side_effect=DoclingError("docling unreachable"),
            ),
            pytest.raises(HTTPException) as excinfo,
        ):
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "extraction_failed"


# --- Phase-pending pipeline -----------------------------------------------


class TestPhasePending:
    @pytest.mark.asyncio
    async def test_doc_returns_phase_pending(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("legacy.doc", b"\xd0\xcf\x11\xe0", "application/msword")]

        with _FilePatches() as patches, pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "phase_pending"
        assert patches.mock_docling is not None
        patches.mock_docling.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_zip_skipped(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        # Just the zip header magic — not a real archive.
        files = [_upload("nope.zip", b"PK\x03\x04", "application/zip")]

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "archive_malformed"


# --- Archive pipeline ------------------------------------------------------


class TestArchivePipeline:
    @pytest.mark.asyncio
    async def test_zip_with_valid_markdown_members_ingests_each_member(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        zip_bytes = _build_zip([("one.md", b"# One"), ("two.md", b"# Two")])
        files = [_upload("bundle.zip", zip_bytes, "application/zip")]

        with _FilePatches() as patches:
            resp = await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert len(resp.uploads) == 2
        assert {u.filename for u in resp.uploads} == {"one.md", "two.md"}
        assert {u.status for u in resp.uploads} == {"done"}
        assert resp.skipped == []
        assert patches.mock_ingest is not None
        assert patches.mock_ingest.call_count == 2
        assert patches.mock_docling is not None
        patches.mock_docling.assert_not_called()
        assert patches.mock_create_upload is not None
        assert patches.mock_create_upload.call_count == 2

    @pytest.mark.asyncio
    async def test_zip_with_unsupported_member_rejects_whole_archive(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        zip_bytes = _build_zip([("one.md", b"# One"), ("script.exe", b"MZ\x90")])
        files = [_upload("bundle.zip", zip_bytes, "application/zip")]

        with _FilePatches() as patches, pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"
        assert patches.mock_ingest is not None
        patches.mock_ingest.assert_not_called()
        assert patches.mock_docling is not None
        patches.mock_docling.assert_not_called()
        assert patches.mock_create_upload is not None
        patches.mock_create_upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_zip_with_mime_mismatch_rejects_before_any_member_ingest(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        zip_bytes = _build_zip([("one.md", b"# One"), ("fake.pdf", b"GIF89a\x00\x00")])
        files = [_upload("bundle.zip", zip_bytes, "application/zip")]

        with _FilePatches() as patches, pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "mime_mismatch"
        assert patches.mock_ingest is not None
        patches.mock_ingest.assert_not_called()
        assert patches.mock_docling is not None
        patches.mock_docling.assert_not_called()
        assert patches.mock_create_upload is not None
        patches.mock_create_upload.assert_not_called()


# --- Mixed multi-file ------------------------------------------------------


class TestMixedRequest:
    @pytest.mark.asyncio
    async def test_md_plus_pdf_partial_success(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [
            _upload("notes.md", b"# md content", "text/markdown"),
            _upload("chemie.pdf", _TINY_PDF, "application/pdf"),
        ]

        with _FilePatches() as patches:
            resp = await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        # Both accepted; one done (md), one processing (pdf).
        assert len(resp.uploads) == 2
        statuses = {u.status for u in resp.uploads}
        assert statuses == {"done", "processing"}
        assert resp.skipped == []

        # Each pipeline was hit exactly once.
        assert patches.mock_ingest is not None
        patches.mock_ingest.assert_called_once()
        assert patches.mock_docling is not None
        patches.mock_docling.assert_called_once()

    @pytest.mark.asyncio
    async def test_md_plus_malformed_zip_md_done_zip_skipped(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [
            _upload("notes.md", b"# md", "text/markdown"),
            _upload("nope.zip", b"PK\x03\x04", "application/zip"),
        ]

        with _FilePatches():
            resp = await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert len(resp.uploads) == 1
        assert resp.uploads[0].status == "done"
        assert len(resp.skipped) == 1
        assert resp.skipped[0].reason == "archive_malformed"


# --- Protocol-level rejections --------------------------------------------


class TestProtocolErrors:
    @pytest.mark.asyncio
    async def test_unsupported_extension_returns_400(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload("malware.exe", b"MZ\x90", "application/octet-stream")]

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"

    @pytest.mark.asyncio
    async def test_empty_files_list_returns_400(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request([]), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "no_files"

    @pytest.mark.asyncio
    async def test_too_many_files_returns_400(self) -> None:
        from app.api.app_knowledge_sources import add_file_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        files = [_upload(f"f{i}.md", b"x", "text/markdown") for i in range(11)]

        with _FilePatches(), pytest.raises(HTTPException) as excinfo:
            await add_file_source(kb_slug="personal", request=_make_request(files), perms=_make_perms(), db=db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "too_many_files"


# --- Status polling endpoint ----------------------------------------------


class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_returns_view_for_existing_upload(self) -> None:
        from app.api.app_knowledge_sources import get_file_source_status

        kb = _make_kb()
        db = _make_db_mock(kb)
        upload_id = uuid.uuid4()
        view = _make_upload_view(
            upload_id=upload_id,
            status=STATUS_PROCESSING,
            artifact_id=None,
            docling_task_id="task-xyz",
            filename="chemie.pdf",
            extension=".pdf",
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources._load_org_or_500",
                    AsyncMock(return_value=_make_org()),
                )
            )
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.get_user_role_for_kb",
                    AsyncMock(return_value="owner"),
                )
            )
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                    AsyncMock(return_value=None),
                )
            )
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.kb_uploads_repo.get_view",
                    AsyncMock(return_value=view),
                )
            )

            resp = await get_file_source_status(
                kb_slug="personal",
                upload_id=upload_id,
                perms=_make_perms(),
                db=db,
            )

        assert resp.id == upload_id
        assert resp.status == "processing"
        assert resp.filename == "chemie.pdf"
        assert resp.artifact_id is None

    @pytest.mark.asyncio
    async def test_404_when_upload_missing(self) -> None:
        from app.api.app_knowledge_sources import get_file_source_status

        kb = _make_kb()
        db = _make_db_mock(kb)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources._load_org_or_500",
                    AsyncMock(return_value=_make_org()),
                )
            )
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.get_user_role_for_kb",
                    AsyncMock(return_value="owner"),
                )
            )
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                    AsyncMock(return_value=None),
                )
            )
            stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.kb_uploads_repo.get_view",
                    AsyncMock(return_value=None),
                )
            )

            with pytest.raises(HTTPException) as excinfo:
                await get_file_source_status(
                    kb_slug="personal",
                    upload_id=uuid.uuid4(),
                    perms=_make_perms(),
                    db=db,
                )
            assert excinfo.value.status_code == 404
