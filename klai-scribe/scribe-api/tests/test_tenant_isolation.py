"""Tenant-isolation tests for scribe transcriptions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.auth import CallerIdentity


def _sql_text(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    result.scalar_one = MagicMock(return_value=value)
    scalars = MagicMock()
    scalars.all.return_value = value if isinstance(value, list) else []
    result.scalars.return_value = scalars
    return result


class _FakeUpload:
    filename = "meeting.wav"

    async def read(self, _limit: int) -> bytes:
        return b"RIFFfake"


class _FakeDB:
    def __init__(self, value=None) -> None:
        self.statements = []
        self.added = None
        self.execute = AsyncMock(side_effect=self._execute)
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self._value = value

    async def _execute(self, statement):
        self.statements.append(statement)
        return _result(self._value)

    def add(self, record) -> None:
        self.added = record


def test_single_record_scope_requires_user_and_org() -> None:
    from app.api.transcribe import _owned_transcription_filters

    caller = CallerIdentity(user_id="user-1", org_id="org-1")

    filters = _owned_transcription_filters("txn-1", caller)
    sql = " AND ".join(_sql_text(f) for f in filters)

    assert "transcriptions.id = 'txn-1'" in sql
    assert "transcriptions.user_id = 'user-1'" in sql
    assert "transcriptions.org_id = 'org-1'" in sql


def test_collection_scope_requires_user_and_org() -> None:
    from app.api.transcribe import _caller_transcription_filters

    caller = CallerIdentity(user_id="user-1", org_id="org-1")

    filters = _caller_transcription_filters(caller)
    sql = " AND ".join(_sql_text(f) for f in filters)

    assert "transcriptions.user_id = 'user-1'" in sql
    assert "transcriptions.org_id = 'org-1'" in sql


@pytest.mark.asyncio
async def test_transcribe_persists_portal_verified_org(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import transcribe as transcribe_mod

    caller = CallerIdentity(user_id="user-1", org_id="org-1")
    db = _FakeDB()

    monkeypatch.setattr(transcribe_mod, "normalize_audio", lambda raw, filename: b"WAV")
    monkeypatch.setattr(
        transcribe_mod,
        "save_audio",
        lambda user_id, txn_id, wav: f"{user_id}/{txn_id}.wav",
    )

    provider = MagicMock()
    provider.transcribe = AsyncMock(
        return_value=SimpleNamespace(
            text="hello",
            language="en",
            duration_seconds=1.0,
            inference_time_seconds=0.2,
            provider="test-provider",
            model="test-model",
        )
    )
    monkeypatch.setattr(transcribe_mod, "get_provider", lambda: provider)

    response = await transcribe_mod.transcribe(
        file=_FakeUpload(),
        language=None,
        caller=caller,
        db=db,
    )

    assert db.added is not None
    assert db.added.user_id == "user-1"
    assert db.added.org_id == "org-1"
    assert response.text == "hello"


@pytest.mark.asyncio
async def test_get_transcription_404s_when_org_scope_returns_no_row() -> None:
    from app.api.transcribe import get_transcription

    caller = CallerIdentity(user_id="user-1", org_id="org-1")
    db = _FakeDB(value=None)

    with pytest.raises(HTTPException) as exc_info:
        await get_transcription(txn_id="txn-1", caller=caller, db=db)

    assert exc_info.value.status_code == 404
    sql = _sql_text(db.statements[0])
    assert "transcriptions.user_id = 'user-1'" in sql
    assert "transcriptions.org_id = 'org-1'" in sql


@pytest.mark.asyncio
async def test_delete_statement_is_org_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.transcribe import delete_transcription

    caller = CallerIdentity(user_id="user-1", org_id="org-1")
    record = SimpleNamespace(audio_path=None, created_at=datetime.now(UTC))
    db = _FakeDB(value=record)
    monkeypatch.setattr("app.api.transcribe.delete_audio", lambda _path: None)

    await delete_transcription(txn_id="txn-1", caller=caller, db=db)

    delete_sql = _sql_text(db.statements[1])
    assert "DELETE FROM scribe.transcriptions" in delete_sql
    assert "transcriptions.user_id = 'user-1'" in delete_sql
    assert "transcriptions.org_id = 'org-1'" in delete_sql
