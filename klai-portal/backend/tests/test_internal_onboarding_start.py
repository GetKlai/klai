"""Unit tests for POST /internal/onboarding/start.

Triggered by a Twenty CRM Workflow's manual button on a Person record.
portal-api proxies the call to klai-mailer's /internal/send with
template ``onboarding_invite``.

Covers:
- 200 + ``{sent: true}`` on mailer-accepted send.
- 502 when the mailer returns 4xx/5xx or is unreachable.
- Cal-URL defaults to the canonical event-type when not supplied.
- The downstream rate-limit (mailer-side) surfaces as 502 here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_onboarding_start_returns_200_when_mailer_accepts(monkeypatch):
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())

    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.notifications.send_onboarding_invite", send_mock)

    body = internal.OnboardingStartRequest(
        name="Alice",
        email="alice@example.com",
        cal_url="https://cal.getklai.com/klai/onboarding-intake",
    )
    req = AsyncMock()
    resp = await internal.start_onboarding_drip(req, body)

    assert resp.sent is True
    send_mock.assert_awaited_once_with(
        name="Alice",
        email="alice@example.com",
        cal_url="https://cal.getklai.com/klai/onboarding-intake",
    )


@pytest.mark.asyncio
async def test_onboarding_start_defaults_cal_url_when_omitted(monkeypatch):
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())

    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.notifications.send_onboarding_invite", send_mock)

    body = internal.OnboardingStartRequest(name="Bob", email="bob@example.com")
    req = AsyncMock()
    await internal.start_onboarding_drip(req, body)

    args = send_mock.call_args.kwargs
    assert args["cal_url"] == "https://cal.getklai.com/klai/onboarding-intake"


@pytest.mark.asyncio
async def test_onboarding_start_returns_502_when_mailer_rejects(monkeypatch):
    from fastapi import HTTPException

    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())

    send_mock = AsyncMock(return_value=False)
    monkeypatch.setattr("app.services.notifications.send_onboarding_invite", send_mock)

    body = internal.OnboardingStartRequest(name="Charlie", email="charlie@example.com")
    req = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await internal.start_onboarding_drip(req, body)

    assert exc_info.value.status_code == 502
    assert "mailer" in exc_info.value.detail.lower()
