from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.conftest import TEST_ENV

SECRETS = [
    "SLACK_CLIENT_ID",
    "SLACK_CLIENT_SECRET",
    "SLACK_SIGNING_SECRET",
    "SLACK_ENCRYPTION_KEY",
    "DATABASE_URL",
    "PORTAL_INTERNAL_SECRET",
    "INTERNAL_SECRET",
]


def _apply(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for key, value in {**TEST_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_valid_env_loads(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.portal_api_url == "http://portal-api.test:8010"


def test_portal_api_url_default_is_the_compose_service(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply(monkeypatch, PORTAL_API_URL=None)
    assert Settings(_env_file=None).portal_api_url == "http://portal-api:8010"  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("name", SECRETS)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_empty_secret_fails_at_load(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str | None
) -> None:
    _apply(monkeypatch, **{name: value})
    with pytest.raises(ValidationError, match=name):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_encryption_key_must_be_base64(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, SLACK_ENCRYPTION_KEY="not base64 at all!")
    with pytest.raises(ValidationError, match="valid base64"):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("length", [16, 31, 33, 64])
def test_encryption_key_must_be_32_bytes(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, length: int
) -> None:
    _apply(monkeypatch, SLACK_ENCRYPTION_KEY=base64.b64encode(b"k" * length).decode())
    with pytest.raises(ValidationError, match=f"got {length}"):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
