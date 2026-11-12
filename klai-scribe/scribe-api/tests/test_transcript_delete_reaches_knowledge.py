"""Deleting a transcript also deletes the copy it put into a knowledge base.

The knowledge copy lives under ``scribe/<transcription id>`` in
knowledge-ingest. Before this, the transcript row and audio went and the
ingested text, its vectors and graph episodes stayed searchable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.core.auth import CallerIdentity


@pytest.mark.asyncio
async def test_knowledge_delete_targets_the_transcript_path_with_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import knowledge_adapter

    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        knowledge_adapter.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(_handler), **kwargs),
    )

    await knowledge_adapter.delete_scribe_transcript("org-1", "txn-1")

    (request,) = seen
    assert request.method == "DELETE"
    assert request.url.path == "/ingest/v1/document"
    assert dict(request.url.params) == {"org_id": "org-1", "path": "scribe/txn-1"}
    secret = knowledge_adapter.settings.knowledge_ingest_secret
    assert request.headers["X-Internal-Secret"] == secret
    assert request.headers["X-Caller-Service"] == "scribe"


def _db(record) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=record)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_delete_removes_the_knowledge_copy_for_the_callers_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import transcribe

    knowledge_delete = AsyncMock()
    monkeypatch.setattr(transcribe, "delete_scribe_transcript", knowledge_delete)
    monkeypatch.setattr(transcribe, "delete_audio", lambda _path: None)
    db = _db(SimpleNamespace(audio_path=None))

    await transcribe.delete_transcription(
        txn_id="txn-1", caller=CallerIdentity(user_id="user-1", org_id="org-1"), db=db
    )

    knowledge_delete.assert_awaited_once_with("org-1", "txn-1")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_transcript_is_kept_when_the_knowledge_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import transcribe

    request = httpx.Request("DELETE", "http://knowledge-ingest:8000/ingest/v1/document")
    failure = httpx.HTTPStatusError(
        "unavailable", request=request, response=httpx.Response(503, request=request)
    )
    monkeypatch.setattr(transcribe, "delete_scribe_transcript", AsyncMock(side_effect=failure))
    audio_deleted: list[str | None] = []
    monkeypatch.setattr(transcribe, "delete_audio", audio_deleted.append)
    db = _db(SimpleNamespace(audio_path="user-1/txn-1.wav"))

    with pytest.raises(HTTPException) as exc:
        await transcribe.delete_transcription(
            txn_id="txn-1", caller=CallerIdentity(user_id="user-1", org_id="org-1"), db=db
        )

    assert exc.value.status_code == 502
    assert audio_deleted == []
    assert db.execute.await_count == 1, "only the lookup ran; the row was not deleted"
    db.commit.assert_not_awaited()
