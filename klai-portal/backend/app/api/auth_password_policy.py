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


@router.get("/password-policy", response_model=PasswordPolicyResponse)
async def password_policy() -> PasswordPolicyResponse:
    policy = get_password_policy()
    return PasswordPolicyResponse(
        min_length=policy.min_length,
        min_score=policy.min_score,
    )
