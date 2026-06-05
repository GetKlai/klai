from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.auth_password_policy import password_policy
from app.core.password_policy import PasswordPolicy, get_password_policy
from app.services.password_policy_guard import (
    PasswordPolicyGuardError,
    ZitadelPasswordComplexityPolicy,
    assert_zitadel_password_policy_compatible,
    compare_password_policies,
)


def _remote(**overrides: object) -> ZitadelPasswordComplexityPolicy:
    values = {
        "min_length": 8,
        "has_uppercase": True,
        "has_lowercase": True,
        "has_number": True,
        "has_symbol": True,
    }
    values.update(overrides)
    return ZitadelPasswordComplexityPolicy(**values)


def test_local_policy_may_be_stricter_than_zitadel() -> None:
    assert compare_password_policies(get_password_policy(), _remote(min_length=8)) == []


def test_local_policy_must_not_be_weaker_than_zitadel_min_length() -> None:
    weaker = PasswordPolicy(
        min_length=8,
        min_score=3,
        require_uppercase=True,
        require_lowercase=True,
        require_number=True,
        require_symbol=True,
    )

    assert compare_password_policies(weaker, _remote(min_length=12)) == ["local min_length 8 < Zitadel minLength 12"]


def test_local_policy_must_include_zitadel_composition_requirements() -> None:
    weaker = PasswordPolicy(
        min_length=12,
        min_score=3,
        require_uppercase=True,
        require_lowercase=True,
        require_number=True,
        require_symbol=False,
    )

    assert compare_password_policies(weaker, _remote(has_symbol=True)) == [
        "Zitadel requires symbol but local policy does not"
    ]


def test_zitadel_policy_response_parsing() -> None:
    parsed = ZitadelPasswordComplexityPolicy.from_api_response(
        {
            "policy": {
                "minLength": "8",
                "hasUppercase": True,
                "hasLowercase": True,
                "hasNumber": True,
                "hasSymbol": True,
            }
        }
    )

    assert parsed == _remote(min_length=8)


@pytest.mark.parametrize(
    "payload",
    [
        {"policy": {"minLength": "8", "hasUppercase": True, "hasLowercase": True, "hasNumber": True}},
        {
            "policy": {
                "minLength": True,
                "hasUppercase": True,
                "hasLowercase": True,
                "hasNumber": True,
                "hasSymbol": True,
            }
        },
        {
            "policy": {
                "minLength": "8",
                "hasUppercase": "true",
                "hasLowercase": True,
                "hasNumber": True,
                "hasSymbol": True,
            }
        },
    ],
)
def test_zitadel_policy_response_rejects_missing_or_invalid_fields(payload: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        ZitadelPasswordComplexityPolicy.from_api_response(payload)


@pytest.mark.asyncio
async def test_guard_raises_on_policy_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_zitadel = SimpleNamespace(
        get_password_complexity_policy=AsyncMock(
            return_value={
                "policy": {
                    "minLength": "16",
                    "hasUppercase": True,
                    "hasLowercase": True,
                    "hasNumber": True,
                    "hasSymbol": True,
                }
            }
        )
    )
    monkeypatch.setattr("app.services.password_policy_guard.zitadel", fake_zitadel)

    with pytest.raises(PasswordPolicyGuardError):
        await assert_zitadel_password_policy_compatible()


@pytest.mark.asyncio
async def test_password_policy_endpoint_returns_backend_policy() -> None:
    response = await password_policy()

    assert response.model_dump() == get_password_policy().public_dict()


def test_startup_wires_password_policy_guard_after_pat_validation() -> None:
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/main.py").read_text()
    pat_idx = source.index("Zitadel PAT validated successfully")
    guard_idx = source.index("await assert_zitadel_password_policy_compatible()")
    background_idx = source.index("poller_task = asyncio.create_task")

    assert pat_idx < guard_idx < background_idx
