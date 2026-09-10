"""Unit tests for the docling-serve client."""

from __future__ import annotations

from typing import Any

import pytest

from app.services import docling_client


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"task_id": "task-123", "task_status": "pending"}


class _AsyncClient:
    def __init__(self) -> None:
        self.path: str | None = None
        self.data: dict[str, object] | None = None

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def post(self, path: str, *, files: Any, data: dict[str, object]) -> _Response:
        self.path = path
        self.data = data
        return _Response()


class _PollResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _PollClient:
    """Stands in for the httpx client used by ``poll_status``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.path: str | None = None

    async def __aenter__(self) -> _PollClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, path: str) -> _PollResponse:
        self.path = path
        return _PollResponse(self._payload)


# The ``TaskStatus`` enum of docling-jobkit 3.3.0 — the task state the poll
# endpoint can actually return — verified inside the running container
# klai-core-docling-serve-1 (docling-serve 1.30.0) on 2026-09-10:
# ``pending, started, success, failure``.
#
# Do NOT trust the openapi.json here: it types ``Task.task_status`` as
# ``ConversionStatus``, which also lists ``partial_success`` and ``skipped``.
# Those are DOCUMENT conversion statuses, not task statuses — jobkit's pydantic
# ``Task`` model rejects them, so they can never reach the poller. A previous
# brief read the OpenAPI literally and added them to the client enum; these
# tests exist so that mistake is not repeated.
#
# Limitation: portal-api has no docling dependency, so this test cannot import
# the real enum and compares two hand-maintained lists — the lists below must
# be re-verified against the container whenever docling-serve is bumped.
DOCLING_JOBKIT_3_3_0_TASK_STATUSES: dict[str, bool] = {
    "pending": False,
    "started": False,
    "success": True,
    "failure": True,
}


def test_extract_ingest_result_strips_embedded_data_images() -> None:
    payload = {
        "document": {"md_content": ("Intro\n\n![Image](data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==)\n\nOutro")}
    }

    result = docling_client._extract_ingest_result(payload, "task-123")

    assert "data:image" not in result.content
    assert result.content == "Intro\n\n<!-- image -->\n\nOutro"
    assert result.chunks is None


def test_extract_ingest_result_uses_docling_chunks() -> None:
    payload = {
        "chunks": [
            {"filename": "a.pdf", "chunk_index": 0, "text": "First", "doc_items": []},
            {"filename": "a.pdf", "chunk_index": 1, "text": "Second", "doc_items": []},
        ],
        "documents": [],
        "processing_time": 1.0,
    }

    result = docling_client._extract_ingest_result(payload, "task-123")

    assert result.content == "First\n\nSecond"
    assert result.chunks == ("First", "Second")
    assert result.chunk_count == 2


@pytest.mark.asyncio
async def test_submit_file_async_requests_hybrid_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _AsyncClient()
    monkeypatch.setattr(docling_client, "_client", lambda _timeout_s: fake_client)

    await docling_client.submit_file_async(
        filename="chemie.pdf",
        content=b"%PDF",
        content_type="application/pdf",
        input_format="pdf",
    )

    assert fake_client.path == "/v1/chunk/hybrid/file/async"
    assert fake_client.data is not None
    assert fake_client.data["include_converted_doc"] is False
    assert fake_client.data["convert_from_formats"] == ["pdf"]
    assert fake_client.data["convert_image_export_mode"] == "placeholder"


def test_docling_task_status_matches_jobkit_task_status() -> None:
    """The client enum must be exactly jobkit's ``TaskStatus`` vocabulary.

    A status the server never sends (the retired ``in_progress`` and
    ``revoked``) means the poller waits for nothing; a missing real status
    means a finished task is read as "still running".

    This is a pinned specification, not a live contract check: both this list
    and the enum are hand-maintained, so a docling-serve bump changes neither
    and this test stays green. What it does buy is that the values cannot
    drift back in silently, and that the verified source and date are written
    down. Catching a bump automatically needs an integration test that reads
    the enum out of the pinned container — that tier does not exist yet.
    """
    live = set(DOCLING_JOBKIT_3_3_0_TASK_STATUSES)
    client = {status.value for status in docling_client.DoclingTaskStatus}

    assert client == live, (
        "DoclingTaskStatus does not match docling-jobkit 3.3.0 TaskStatus: "
        f"missing={sorted(live - client)}, unknown-to-server={sorted(client - live)}"
    )


def test_terminal_statuses_are_success_and_failure() -> None:
    """Polling stops on exactly the two terminal task statuses."""
    assert set(docling_client._TERMINAL_STATUSES) == {"success", "failure"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_status", "expected_terminal"),
    sorted(DOCLING_JOBKIT_3_3_0_TASK_STATUSES.items()),
)
async def test_poll_status_terminal_flags_jobkit_task_statuses(
    monkeypatch: pytest.MonkeyPatch,
    task_status: str,
    expected_terminal: bool,
) -> None:
    fake_client = _PollClient({"task_status": task_status})
    monkeypatch.setattr(docling_client, "_client", lambda _timeout_s: fake_client)

    result = await docling_client.poll_status("task-123")

    assert result.status == task_status
    assert result.terminal is expected_terminal
