from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, values: list[str] | None = None, rowcount: int | None = None) -> None:
        self.rowcount = rowcount
        self._values = values or []

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class _FakeSession:
    def __init__(self, audio_paths: list[str], rowcount: int) -> None:
        self.audio_paths = audio_paths
        self.rowcount = rowcount
        self.statements = []
        self.execute = AsyncMock(side_effect=self._execute)
        self.commit = AsyncMock()

    async def _execute(self, statement):
        self.statements.append(statement)
        sql = str(statement)
        if sql.startswith("SELECT"):
            return _Result(values=self.audio_paths)
        return _Result(rowcount=self.rowcount)


class _SessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def test_internal_secret_rejects_missing_or_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.internal import _require_internal_secret
    from app.core.config import settings

    monkeypatch.setattr(settings, "portal_internal_secret", "expected-secret")

    with pytest.raises(HTTPException) as missing:
        _require_internal_secret(None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        _require_internal_secret("wrong-secret")
    assert wrong.value.status_code == 401


def test_internal_secret_accepts_expected_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.internal import _require_internal_secret
    from app.core.config import settings

    monkeypatch.setattr(settings, "portal_internal_secret", "expected-secret")

    _require_internal_secret("expected-secret")


@pytest.mark.asyncio
async def test_wipe_org_state_deletes_audio_then_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import internal
    from app.core.config import settings

    expected_secret = "expected-secret"
    monkeypatch.setattr(settings, "portal_internal_secret", expected_secret)
    deleted_audio: list[str] = []
    monkeypatch.setattr(internal, "delete_audio", lambda path: deleted_audio.append(path))

    session = _FakeSession(audio_paths=["user-a/txn-1.wav", "user-b/txn-2.wav"], rowcount=2)
    monkeypatch.setattr(internal, "AsyncSessionLocal", _SessionFactory(session))

    response = await internal.wipe_org_state(
        "zitadel-org-abc",
        x_internal_secret=expected_secret,
    )

    assert response.rows_deleted == 2
    assert response.audio_files_deleted == 2
    assert response.status == "ok"
    assert deleted_audio == ["user-a/txn-1.wav", "user-b/txn-2.wav"]
    session.commit.assert_awaited_once()

    select_sql = str(session.statements[0])
    delete_sql = str(session.statements[1])
    assert "transcriptions.audio_path" in select_sql
    assert "transcriptions.org_id = :org_id_1" in select_sql
    assert "DELETE FROM scribe.transcriptions" in delete_sql
    assert "transcriptions.org_id = :org_id_1" in delete_sql
