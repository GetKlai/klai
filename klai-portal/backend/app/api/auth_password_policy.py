"""Public auth password policy contract.

The signup and password-set screens use this endpoint instead of duplicating
password rules in the browser. The backend remains the source of truth.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.password_policy import get_password_policy

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasswordPolicyResponse(BaseModel):
    min_length: int
    min_score: int
    require_uppercase: bool
    require_lowercase: bool
    require_number: bool
    require_symbol: bool


@router.get("/password-policy", response_model=PasswordPolicyResponse)
async def password_policy() -> PasswordPolicyResponse:
    policy = get_password_policy()
    return PasswordPolicyResponse(
        min_length=policy.min_length,
        min_score=policy.min_score,
        require_uppercase=policy.require_uppercase,
        require_lowercase=policy.require_lowercase,
        require_number=policy.require_number,
        require_symbol=policy.require_symbol,
    )
