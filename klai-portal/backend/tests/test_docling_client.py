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
