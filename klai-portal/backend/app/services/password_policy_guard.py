"""Guard Klai's local password policy against Zitadel drift.

Local validation must be at least as strict as Zitadel before we consume
one-time invite/reset codes. Otherwise a user can lose a valid link because
Zitadel accepts the code and rejects the password afterwards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

import httpx

from app.core.password_policy import PasswordPolicy, get_password_policy
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

ZITADEL_COMPOSITION_REQUIREMENTS: dict[str, bool] = {
    "uppercase": False,
    "lowercase": False,
    "number": False,
    "symbol": False,
}


class PasswordPolicyGuardError(RuntimeError):
    """Raised when Klai cannot prove password policy compatibility."""


@dataclass(frozen=True)
class ZitadelPasswordComplexityPolicy:
    min_length: int
    has_uppercase: bool
    has_lowercase: bool
    has_number: bool
    has_symbol: bool

    @classmethod
    def from_api_response(cls, payload: dict[str, Any]) -> ZitadelPasswordComplexityPolicy:
        policy_payload = payload.get("policy")
        policy = cast(dict[str, Any], policy_payload) if isinstance(policy_payload, dict) else payload
        required = ("minLength", "hasUppercase", "hasLowercase", "hasNumber", "hasSymbol")
        missing = [key for key in required if key not in policy]
        if missing:
            raise ValueError(f"Zitadel password policy response missing keys: {missing}")
        return cls(
            min_length=_require_int(policy, "minLength"),
            has_uppercase=_require_bool(policy, "hasUppercase"),
            has_lowercase=_require_bool(policy, "hasLowercase"),
            has_number=_require_bool(policy, "hasNumber"),
            has_symbol=_require_bool(policy, "hasSymbol"),
        )


def compare_password_policies(
    local: PasswordPolicy,
    zitadel_policy: ZitadelPasswordComplexityPolicy,
) -> list[str]:
    """Return compatibility errors.

    Exact equality is not required: Klai may be stricter than Zitadel. What is
    unsafe is Klai being weaker, because then a password can pass locally and
    fail later inside Zitadel after a one-time code was consumed.
    """
    errors: list[str] = []
    if local.min_length < zitadel_policy.min_length:
        errors.append(f"local min_length {local.min_length} < Zitadel minLength {zitadel_policy.min_length}")
    if zitadel_policy.has_uppercase != ZITADEL_COMPOSITION_REQUIREMENTS["uppercase"]:
        errors.append("Zitadel must not require uppercase")
    if zitadel_policy.has_lowercase != ZITADEL_COMPOSITION_REQUIREMENTS["lowercase"]:
        errors.append("Zitadel must not require lowercase")
    if zitadel_policy.has_number != ZITADEL_COMPOSITION_REQUIREMENTS["number"]:
        errors.append("Zitadel must not require number")
    if zitadel_policy.has_symbol != ZITADEL_COMPOSITION_REQUIREMENTS["symbol"]:
        errors.append("Zitadel must not require symbol")
    return errors


async def assert_zitadel_password_policy_compatible() -> None:
    try:
        payload = await zitadel.get_password_complexity_policy()
    except httpx.HTTPError as exc:
        logger.exception("zitadel_password_policy_unavailable")
        raise PasswordPolicyGuardError("Zitadel password policy is unavailable") from exc

    try:
        remote_policy = ZitadelPasswordComplexityPolicy.from_api_response(payload)
        errors = compare_password_policies(get_password_policy(), remote_policy)
    except (TypeError, ValueError, AttributeError) as exc:
        logger.exception("zitadel_password_policy_response_invalid")
        raise PasswordPolicyGuardError("Zitadel password policy response is invalid") from exc
    if errors:
        logger.critical("policy_drift_detected error_count=%d", len(errors))
        raise PasswordPolicyGuardError("; ".join(errors))


def _require_bool(policy: dict[str, Any], key: str) -> bool:
    value = policy[key]
    if not isinstance(value, bool):
        raise TypeError(f"Zitadel password policy key {key} must be boolean")
    return value


def _require_int(policy: dict[str, Any], key: str) -> int:
    value = policy[key]
    if isinstance(value, bool):
        raise TypeError(f"Zitadel password policy key {key} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise ValueError(f"Zitadel password policy key {key} must be an integer")
