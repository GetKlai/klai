from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import internal_api, repo
from app.config import Settings, get_settings

SECRET = "test-internal-secret"


class FakeWebClient:
    revoked: ClassVar[list[str]] = []

    def __init__(self, token: str) -> None:
        self.token = token

    async def auth_revoke(self) -> dict[str, Any]:
        FakeWebClient.revoked.append(self.token)
        return {"ok": True}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, fake_db: object) -> dict[str, list[str]]:
    record: dict[str, list[str]] = {"purged": []}
    FakeWebClient.revoked = []

    async def get_bot_token(_s: object, team_id: str) -> str | None:
        return "xoxb-synthetic" if team_id == "T1" else None

    async def purge_team(_s: object, team_id: str) -> None:
        record["purged"].append(team_id)

    monkeypatch.setattr(repo, "get_bot_token", get_bot_token)
    monkeypatch.setattr(repo, "purge_team", purge_team)
    monkeypatch.setattr(internal_api, "AsyncWebClient", FakeWebClient)
    return record


def _client(internal_secret: str = SECRET) -> TestClient:
    app = FastAPI()
    app.include_router(internal_api.router)
    settings = Settings.model_construct(
        **{**get_settings().model_dump(), "internal_secret": internal_secret}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_unconfigured_secret_fails_closed(calls: dict[str, list[str]]) -> None:
    response = _client(internal_secret="").delete(
        "/internal/installations/T1", headers={"Authorization": "Bearer "}
    )
    assert response.status_code == 503
    assert calls["purged"] == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong"},
        {"Authorization": SECRET},
    ],
)
def test_missing_or_wrong_secret_is_401(
    calls: dict[str, list[str]], headers: dict[str, str]
) -> None:
    response = _client().delete("/internal/installations/T1", headers=headers)
    assert response.status_code == 401
    assert calls["purged"] == []


def test_right_secret_revokes_and_purges(calls: dict[str, list[str]]) -> None:
    response = _client().delete(
        "/internal/installations/T1", headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert response.status_code == 204
    assert FakeWebClient.revoked == ["xoxb-synthetic"]
    assert calls["purged"] == ["T1"]


def test_unknown_team_is_idempotent_204(calls: dict[str, list[str]]) -> None:
    response = _client().delete(
        "/internal/installations/T404", headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert response.status_code == 204
    assert FakeWebClient.revoked == []
    assert calls["purged"] == ["T404"]


def test_health_is_open() -> None:
    assert _client().get("/health").json() == {"status": "ok"}


def test_source_uses_constant_time_compare() -> None:
    import inspect

    source = inspect.getsource(internal_api.require_internal_secret)
    assert "verify_shared_secret" in source
    assert "==" not in source
