# SPEC-TI-010A A-9: org_id tenant isolation on Transcription endpoints.
# Cross-org access -> 404 (never leak existence).
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.auth import CallerIdentity


def _make_db(results: list) -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    execute_returns = iter(results)

    async def _execute(*_a, **_kw):
        return next(execute_returns)

    db.execute = _execute
    return db


def _scalar_result(value) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    r.scalar_one = MagicMock(return_value=value)
    return r


def _make_transcription(user_id: str, org_id: str) -> MagicMock:
    t = MagicMock()
    t.id = "txn-001"
    t.user_id = user_id
    t.org_id = org_id
    t.status = "completed"
    t.name = "meeting"
    t.text = "hello"
    t.audio_path = "/data/" + user_id + "/txn-001.wav"
    t.duration_seconds = 60
    t.segments_json = None
    t.created_at = None
    t.updated_at = None
    return t


class TestTranscriptionOrgIsolation:
    async def test_get_returns_404_for_cross_org(self) -> None:
        from app.api.transcribe import get_transcription

        db = _make_db([_scalar_result(None)])
        caller = CallerIdentity(user_id="user-a", org_id="org-attacker")
        with pytest.raises(HTTPException) as exc_info:
            await get_transcription(txn_id="txn-victim", caller=caller, db=db)
        assert exc_info.value.status_code == 404

    async def test_patch_returns_404_for_cross_org(self) -> None:
        from app.api.transcribe import PatchTranscriptionRequest, patch_transcription

        db = _make_db([_scalar_result(None)])
        caller = CallerIdentity(user_id="user-a", org_id="org-attacker")
        with pytest.raises(HTTPException) as exc_info:
            await patch_transcription(
                txn_id="txn-victim",
                body=PatchTranscriptionRequest(name="pwned"),
                caller=caller,
                db=db,
            )
        assert exc_info.value.status_code == 404

    async def test_delete_returns_404_for_cross_org(self) -> None:
        from app.api.transcribe import delete_transcription

        db = _make_db([_scalar_result(None)])
        caller = CallerIdentity(user_id="user-a", org_id="org-attacker")
        with pytest.raises(HTTPException) as exc_info:
            await delete_transcription(txn_id="txn-victim", caller=caller, db=db)
        assert exc_info.value.status_code == 404

    async def test_list_excludes_cross_org_rows(self) -> None:
        from app.api.transcribe import list_transcriptions

        count_result = _scalar_result(0)
        rows_result = MagicMock()
        rows_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db = _make_db([count_result, rows_result])
        caller = CallerIdentity(user_id="user-a", org_id="org-attacker")
        response = await list_transcriptions(caller=caller, db=db)
        assert response.total == 0
        assert response.items == []

    async def test_same_org_access_succeeds(self) -> None:
        from app.api.transcribe import get_transcription

        txn = _make_transcription(user_id="user-a", org_id="org-a")
        db = _make_db([_scalar_result(txn)])
        caller = CallerIdentity(user_id="user-a", org_id="org-a")
        response = await get_transcription(txn_id="txn-001", caller=caller, db=db)
        assert response.id == "txn-001"

    async def test_different_user_same_org_returns_404(self) -> None:
        from app.api.transcribe import get_transcription

        db = _make_db([_scalar_result(None)])
        caller = CallerIdentity(user_id="user-b", org_id="org-a")
        with pytest.raises(HTTPException) as exc_info:
            await get_transcription(txn_id="txn-001", caller=caller, db=db)
        assert exc_info.value.status_code == 404


class TestTranscriptionOrgIdInsert:
    async def test_ingest_passes_caller_org_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api.transcribe import IngestToKBRequest, ingest_transcription_to_kb

        txn = _make_transcription(user_id="user-a", org_id="org-a")
        db = _make_db([_scalar_result(txn)])
        captured: dict = {}

        async def fake_ingest(*, org_id: str, kb_slug: str, transcription) -> str:
            captured["org_id"] = org_id
            return "art-001"

        monkeypatch.setattr("app.services.knowledge_adapter.ingest_scribe_transcript", fake_ingest)
        caller = CallerIdentity(user_id="user-a", org_id="org-a")
        await ingest_transcription_to_kb(
            txn_id="txn-001",
            body=IngestToKBRequest(kb_slug="team-notes"),
            caller=caller,
            db=db,
        )
        assert captured["org_id"] == "org-a", "ingest must forward caller.org_id (A-9)"

    async def test_ingest_cross_org_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api.transcribe import IngestToKBRequest, ingest_transcription_to_kb

        db = _make_db([_scalar_result(None)])
        caller = CallerIdentity(user_id="user-a", org_id="org-attacker")

        async def _should_not_run(**_kw) -> str:
            raise AssertionError("ingest must not run when lookup fails")

        monkeypatch.setattr(
            "app.services.knowledge_adapter.ingest_scribe_transcript", _should_not_run
        )
        with pytest.raises(HTTPException) as exc_info:
            await ingest_transcription_to_kb(
                txn_id="txn-victim",
                body=IngestToKBRequest(kb_slug="team-notes"),
                caller=caller,
                db=db,
            )
        assert exc_info.value.status_code == 404
