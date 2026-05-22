"""Unit tests for the kb_upload_poller (SPEC-KB-FILE-UPLOAD-001).

The poller's responsibility is the per-row state machine
(``processing`` → ``ingesting`` → ``done`` / ``failed``). These tests
mock the docling client, knowledge-ingest client, and DB session
helpers so the state-machine logic is exercised in isolation.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import kb_upload_poller
from app.services.docling_client import (
    DoclingError,
    DoclingIngestResult,
    DoclingPollResult,
    DoclingResultNotFoundError,
    DoclingTaskStatus,
    DoclingTimeoutError,
)
from app.services.kb_uploads_repo import (
    STATUS_FAILED,
    STATUS_INGESTING,
    STATUS_PROCESSING,
    KBUploadView,
)


def _view(
    *,
    status: str = STATUS_PROCESSING,
    docling_task_id: str | None = "task-1",
    org_id: int = 1,
    kb_id: int = 42,
) -> KBUploadView:
    return KBUploadView(
        id=uuid.uuid4(),
        kb_id=kb_id,
        org_id=org_id,
        created_by="user-abc",
        filename="chemie.pdf",
        extension=".pdf",
        mime="application/pdf",
        bytes=1024,
        source_ref="file:sha256:abc",
        status=status,
        failure_reason=None,
        docling_task_id=docling_task_id,
        artifact_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@asynccontextmanager
async def _fake_session(*_args, **_kwargs):
    db = AsyncMock()
    db.add = MagicMock()
    yield db


class _PollerPatches:
    """Bundle the docling + repo + session patches for poller tests."""

    def __init__(
        self,
        *,
        poll_result: DoclingPollResult | None = None,
        poll_side_effect: Exception | None = None,
        markdown: str | None = "# converted markdown",
        chunks: tuple[str, ...] | None = None,
        result_side_effect: Exception | None = None,
        ingest_artifact: str = "art-pdf-1",
        ingest_side_effect: Exception | None = None,
        kb: object | None = None,
        org: object | None = None,
    ) -> None:
        self.poll_result = poll_result
        self.poll_side_effect = poll_side_effect
        self.markdown = markdown
        self.chunks = chunks
        self.result_side_effect = result_side_effect
        self.ingest_artifact = ingest_artifact
        self.ingest_side_effect = ingest_side_effect
        self.kb = kb
        self.org = org
        self.stack = ExitStack()
        self.mock_poll: AsyncMock | None = None
        self.mock_result: AsyncMock | None = None
        self.mock_ingest: AsyncMock | None = None
        self.mock_mark_done: AsyncMock | None = None
        self.mock_mark_failed: AsyncMock | None = None
        self.mock_mark_ingesting: AsyncMock | None = None

    def __enter__(self) -> _PollerPatches:
        self.mock_poll = AsyncMock(return_value=self.poll_result, side_effect=self.poll_side_effect)
        self.stack.enter_context(patch("app.services.kb_upload_poller.docling_client.poll_status", self.mock_poll))

        docling_result = DoclingIngestResult(
            content=self.markdown or "",
            chunks=self.chunks,
            chunk_count=len(self.chunks or ()),
        )
        self.mock_result = AsyncMock(return_value=docling_result, side_effect=self.result_side_effect)
        self.stack.enter_context(
            patch(
                "app.services.kb_upload_poller.docling_client.get_result_document",
                self.mock_result,
            )
        )

        self.mock_ingest = AsyncMock(return_value=self.ingest_artifact, side_effect=self.ingest_side_effect)
        self.stack.enter_context(
            patch(
                "app.services.kb_upload_poller.knowledge_ingest_client.ingest_document",
                self.mock_ingest,
            )
        )

        self.mock_mark_done = AsyncMock(return_value=None)
        self.mock_mark_failed = AsyncMock(return_value=None)
        self.mock_mark_ingesting = AsyncMock(return_value=None)
        self.stack.enter_context(patch("app.services.kb_upload_poller.kb_uploads_repo.mark_done", self.mock_mark_done))
        self.stack.enter_context(
            patch("app.services.kb_upload_poller.kb_uploads_repo.mark_failed", self.mock_mark_failed)
        )
        self.stack.enter_context(
            patch(
                "app.services.kb_upload_poller.kb_uploads_repo.mark_ingesting",
                self.mock_mark_ingesting,
            )
        )

        # Both session helpers yield a no-op AsyncSession mock — the
        # tests assert against repo / docling calls, not raw SQL.
        self.stack.enter_context(patch("app.services.kb_upload_poller.tenant_scoped_session", _fake_session))

        # Default KB / Org for _ingest_and_finish; tests override via
        # patches.set_kb_org if they want to exercise a missing KB.
        kb = self.kb or self._default_kb()
        org = self.org or self._default_org()

        async def _execute_stub(stmt, *args, **kwargs):
            text_repr = str(stmt).lower()
            result = MagicMock()
            if "portal_knowledge_bases" in text_repr:
                result.scalar_one_or_none = MagicMock(return_value=kb)
            elif "portal_orgs" in text_repr:
                result.scalar_one_or_none = MagicMock(return_value=org)
            else:
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        # tenant_scoped_session yields a fake db; patch its execute
        # method via the asynccontextmanager produced by `_fake_session`
        # — easier to inject via a wrapper.
        @asynccontextmanager
        async def _scoped(_org_id: int):
            db = AsyncMock()
            db.add = MagicMock()
            db.execute.side_effect = _execute_stub
            yield db

        self.stack.enter_context(patch("app.services.kb_upload_poller.tenant_scoped_session", _scoped))
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stack.close()

    @staticmethod
    def _default_kb() -> MagicMock:
        kb = MagicMock()
        kb.id = 42
        kb.slug = "personal"
        kb.name = "Personal"
        return kb

    @staticmethod
    def _default_org() -> MagicMock:
        org = MagicMock()
        org.id = 1
        org.zitadel_org_id = "zitadel-org-1"
        return org


class _SessionBoundValue:
    """Object that raises when accessed after the fake DB session closes."""

    def __init__(self, value: str, closed: dict[str, bool]) -> None:
        self.value = value
        self.closed = closed

    def get(self) -> str:
        if self.closed["value"]:
            raise RuntimeError("attribute accessed after session closed")
        return self.value


class _SessionBoundKB:
    id = 42

    def __init__(self, closed: dict[str, bool]) -> None:
        self._slug = _SessionBoundValue("chemie", closed)
        self._name = _SessionBoundValue("Chemie", closed)
        self._owner_type = _SessionBoundValue("user", closed)

    @property
    def slug(self) -> str:
        return self._slug.get()

    @property
    def name(self) -> str:
        return self._name.get()

    @property
    def owner_type(self) -> str:
        return self._owner_type.get()


class _SessionBoundOrg:
    id = 1

    def __init__(self, closed: dict[str, bool]) -> None:
        self._zitadel_org_id = _SessionBoundValue("zitadel-org-1", closed)

    @property
    def zitadel_org_id(self) -> str:
        return self._zitadel_org_id.get()


# ---- Processing-state transitions -----------------------------------------


class TestProcessingState:
    @pytest.mark.asyncio
    async def test_still_pending_no_terminal_action(self) -> None:
        view = _view(status=STATUS_PROCESSING)
        with _PollerPatches(
            poll_result=DoclingPollResult(
                task_id="task-1",
                status=DoclingTaskStatus.IN_PROGRESS,
                terminal=False,
                error_message=None,
                queue_position=2,
            ),
        ) as patches:
            await kb_upload_poller._process_processing_row(view)

        assert patches.mock_mark_done is not None
        patches.mock_mark_done.assert_not_called()
        patches.mock_mark_failed.assert_not_called()  # type: ignore[union-attr]
        patches.mock_mark_ingesting.assert_not_called()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_terminal_failure_marks_failed(self) -> None:
        view = _view(status=STATUS_PROCESSING)
        with _PollerPatches(
            poll_result=DoclingPollResult(
                task_id="task-1",
                status=DoclingTaskStatus.FAILURE,
                terminal=True,
                error_message="bad pdf",
                queue_position=None,
            ),
        ) as patches:
            await kb_upload_poller._process_processing_row(view)

        assert patches.mock_mark_failed is not None
        patches.mock_mark_failed.assert_called_once()
        kwargs = patches.mock_mark_failed.call_args.kwargs
        assert kwargs["upload_id"] == view.id
        assert kwargs["failure_reason"] == "extraction_failed"

    @pytest.mark.asyncio
    async def test_success_advances_through_ingest_to_done(self) -> None:
        view = _view(status=STATUS_PROCESSING)
        with _PollerPatches(
            poll_result=DoclingPollResult(
                task_id="task-1",
                status=DoclingTaskStatus.SUCCESS,
                terminal=True,
                error_message=None,
                queue_position=None,
            ),
            markdown="# converted",
            ingest_artifact="art-99",
        ) as patches:
            await kb_upload_poller._process_processing_row(view)

        # Step 1: marked ingesting
        patches.mock_mark_ingesting.assert_called_once()  # type: ignore[union-attr]
        # Step 2: ingest forwarded
        patches.mock_ingest.assert_called_once()  # type: ignore[union-attr]
        ingest_payload = patches.mock_ingest.call_args.args[0]  # type: ignore[union-attr]
        assert ingest_payload["content"] == "# converted"
        assert ingest_payload["content_hash"] == "abc"
        assert ingest_payload["source_type"] == "file"
        assert ingest_payload["content_type"] == "document"
        assert ingest_payload["extra"]["pipeline"] == "docling"
        # Step 3: marked done with artifact_id
        patches.mock_mark_done.assert_called_once()  # type: ignore[union-attr]
        kwargs = patches.mock_mark_done.call_args.kwargs  # type: ignore[union-attr]
        assert kwargs["upload_id"] == view.id
        assert kwargs["artifact_id"] == "art-99"

    @pytest.mark.asyncio
    async def test_success_forwards_docling_chunks_as_prechunked_ingest(self) -> None:
        view = _view(status=STATUS_PROCESSING)
        with _PollerPatches(
            poll_result=DoclingPollResult(
                task_id="task-1",
                status=DoclingTaskStatus.SUCCESS,
                terminal=True,
                error_message=None,
                queue_position=None,
            ),
            markdown="Preview",
            chunks=("Chunk one", "Chunk two"),
        ) as patches:
            await kb_upload_poller._process_processing_row(view)

        patches.mock_ingest.assert_called_once()  # type: ignore[union-attr]
        ingest_payload = patches.mock_ingest.call_args.args[0]  # type: ignore[union-attr]
        assert ingest_payload["content"] == "Preview"
        assert ingest_payload["skip_chunking"] is True
        assert ingest_payload["chunks"] == ["Chunk one", "Chunk two"]
        assert ingest_payload["content_hash"] == "abc"
        assert ingest_payload["extra"]["docling_chunk_count"] == 2
        assert ingest_payload["extra"]["document_text_truncated"] is True

    @pytest.mark.asyncio
    async def test_transient_timeout_leaves_row_pending(self) -> None:
        view = _view(status=STATUS_PROCESSING)
        with _PollerPatches(
            poll_side_effect=DoclingTimeoutError("timeout"),
        ) as patches:
            await kb_upload_poller._process_processing_row(view)

        # Transient — no terminal transition.
        patches.mock_mark_failed.assert_not_called()  # type: ignore[union-attr]
        patches.mock_mark_done.assert_not_called()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_docling_error_marks_failed(self) -> None:
        view = _view(status=STATUS_PROCESSING)
        with _PollerPatches(
            poll_side_effect=DoclingError("boom"),
        ) as patches:
            await kb_upload_poller._process_processing_row(view)

        patches.mock_mark_failed.assert_called_once()  # type: ignore[union-attr]
        kwargs = patches.mock_mark_failed.call_args.kwargs  # type: ignore[union-attr]
        assert kwargs["failure_reason"] == "docling_unreachable"

    @pytest.mark.asyncio
    async def test_missing_task_id_marks_failed(self) -> None:
        view = _view(status=STATUS_PROCESSING, docling_task_id=None)
        with _PollerPatches() as patches:
            await kb_upload_poller._process_processing_row(view)

        patches.mock_mark_failed.assert_called_once()  # type: ignore[union-attr]
        kwargs = patches.mock_mark_failed.call_args.kwargs  # type: ignore[union-attr]
        assert kwargs["failure_reason"] == "missing_docling_task"


# ---- Ingesting-state retry ------------------------------------------------


class TestIngestingState:
    @pytest.mark.asyncio
    async def test_retries_ingest_to_done(self) -> None:
        view = _view(status=STATUS_INGESTING)
        with _PollerPatches(
            markdown="# retry",
            ingest_artifact="art-retry",
        ) as patches:
            await kb_upload_poller._process_ingesting_row(view)

        patches.mock_ingest.assert_called_once()  # type: ignore[union-attr]
        patches.mock_mark_done.assert_called_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_result_fetch_failure_stays_ingesting(self) -> None:
        view = _view(status=STATUS_INGESTING)
        with _PollerPatches(
            result_side_effect=DoclingError("gone"),
        ) as patches:
            await kb_upload_poller._process_ingesting_row(view)

        # No terminal transition — row stays ingesting for next tick.
        patches.mock_mark_failed.assert_not_called()  # type: ignore[union-attr]
        patches.mock_mark_done.assert_not_called()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_missing_docling_result_marks_failed(self) -> None:
        view = _view(status=STATUS_INGESTING)
        with _PollerPatches(
            result_side_effect=DoclingResultNotFoundError("gone"),
        ) as patches:
            await kb_upload_poller._process_ingesting_row(view)

        patches.mock_mark_failed.assert_called_once()  # type: ignore[union-attr]
        kwargs = patches.mock_mark_failed.call_args.kwargs  # type: ignore[union-attr]
        assert kwargs["failure_reason"] == "docling_result_not_found"
        patches.mock_mark_done.assert_not_called()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_ingest_copies_kb_and_org_values_before_session_closes(self) -> None:
        """Regression for production DetachedInstanceError on org.zitadel_org_id."""

        view = _view(status=STATUS_INGESTING)
        closed = {"value": False}
        kb = _SessionBoundKB(closed)
        org = _SessionBoundOrg(closed)

        async def _execute_stub(stmt, *args, **kwargs):
            text_repr = str(stmt).lower()
            result = MagicMock()
            if "portal_knowledge_bases" in text_repr:
                result.scalar_one_or_none = MagicMock(return_value=kb)
            elif "portal_orgs" in text_repr:
                result.scalar_one_or_none = MagicMock(return_value=org)
            else:
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        @asynccontextmanager
        async def _scoped(_org_id: int):
            db = AsyncMock()
            db.execute.side_effect = _execute_stub
            yield db
            closed["value"] = True

        with _PollerPatches(markdown="# retry", ingest_artifact="art-retry") as patches:
            patches.stack.enter_context(patch("app.services.kb_upload_poller.tenant_scoped_session", _scoped))

            await kb_upload_poller._process_ingesting_row(view)

        patches.mock_ingest.assert_called_once()  # type: ignore[union-attr]
        payload = patches.mock_ingest.call_args.args[0]  # type: ignore[union-attr]
        assert payload["org_id"] == "zitadel-org-1"
        assert payload["kb_slug"] == "chemie"
        assert payload["kb_name"] == "Chemie"
        # Personal KB (owner_type="user") must forward the uploader's id, else
        # knowledge-ingest rejects with personal_kb_owner_mismatch (403) and the
        # upload hangs at `ingesting` forever (2026-05-22 PDF-upload incident).
        assert payload["user_id"] == "user-abc"
        patches.mock_mark_done.assert_called_once()  # type: ignore[union-attr]


# ---- Loop wrapper ---------------------------------------------------------


class TestPollerLoop:
    @pytest.mark.asyncio
    async def test_dispatch_uses_view_status(self) -> None:
        # Spy that ``_process_one_view`` routes to the right phase
        # handler. We patch the two leaf functions and assert each gets
        # called with the matching status.
        processing_view = _view(status=STATUS_PROCESSING)
        ingesting_view = _view(status=STATUS_INGESTING)
        failed_view = _view(status=STATUS_FAILED)

        with ExitStack() as stack:
            mock_processing = AsyncMock()
            mock_ingesting = AsyncMock()
            stack.enter_context(
                patch(
                    "app.services.kb_upload_poller._process_processing_row",
                    mock_processing,
                )
            )
            stack.enter_context(
                patch(
                    "app.services.kb_upload_poller._process_ingesting_row",
                    mock_ingesting,
                )
            )

            await kb_upload_poller._process_one_view(processing_view)
            await kb_upload_poller._process_one_view(ingesting_view)
            await kb_upload_poller._process_one_view(failed_view)

        mock_processing.assert_called_once_with(processing_view)
        mock_ingesting.assert_called_once_with(ingesting_view)
        # Terminal status is a no-op.
