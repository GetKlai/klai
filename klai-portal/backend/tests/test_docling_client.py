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
        self.data: dict[str, object] | None = None

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def post(self, path: str, *, files: Any, data: dict[str, object]) -> _Response:
        self.data = data
        return _Response()


def test_extract_markdown_strips_embedded_data_images() -> None:
    payload = {
        "document": {"md_content": ("Intro\n\n![Image](data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==)\n\nOutro")}
    }

    markdown = docling_client._extract_markdown(payload, "task-123")

    assert "data:image" not in markdown
    assert markdown == "Intro\n\n<!-- image -->\n\nOutro"


@pytest.mark.asyncio
async def test_submit_file_async_requests_placeholder_images(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _AsyncClient()
    monkeypatch.setattr(docling_client, "_client", lambda _timeout_s: fake_client)

    await docling_client.submit_file_async(
        filename="chemie.pdf",
        content=b"%PDF",
        content_type="application/pdf",
    )

    assert fake_client.data is not None
    assert fake_client.data["to_formats"] == ["md"]
    assert fake_client.data["image_export_mode"] == "placeholder"
