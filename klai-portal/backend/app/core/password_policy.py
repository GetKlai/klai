"""Shared onboarding password policy.

The invite activation flow must validate the password before consuming the
one-time invite code. Otherwise Zitadel can accept the invite code, reject the
password, and leave the user with a used-up link.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

PASSWORD_TOO_SHORT_MSG = "Wachtwoord moet minimaal {min_length} tekens bevatten"
PASSWORD_MISSING_UPPERCASE_MSG = "Wachtwoord moet minimaal één hoofdletter bevatten"
PASSWORD_MISSING_LOWERCASE_MSG = "Wachtwoord moet minimaal één kleine letter bevatten"
PASSWORD_MISSING_NUMBER_MSG = "Wachtwoord moet minimaal één cijfer bevatten"
PASSWORD_MISSING_SYMBOL_MSG = "Wachtwoord moet minimaal één symbool bevatten"
PASSWORD_TOO_WEAK_MSG = "Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."
ZITADEL_PASSWORD_POLICY_MSG = (
    "Wachtwoord voldoet niet aan het wachtwoordbeleid. Kies een langer of minder voorspelbaar wachtwoord."
)


class PasswordPolicyConfigurationError(RuntimeError):
    """Raised when the deployed password-policy runtime is incomplete."""


try:
    from zxcvbn import zxcvbn as _zxcvbn
except ImportError as exc:
    logger.exception("zxcvbn_unavailable")
    raise PasswordPolicyConfigurationError("zxcvbn is required for password validation") from exc


@dataclass(frozen=True)
class PasswordPolicy:
    """Single backend source of truth for Klai onboarding passwords."""

    min_length: int
    min_score: int
    require_uppercase: bool
    require_lowercase: bool
    require_number: bool
    require_symbol: bool

    def public_dict(self) -> dict[str, int | bool]:
        return asdict(self)


SIGNUP_PASSWORD_POLICY = PasswordPolicy(
    min_length=12,
    min_score=3,
    require_uppercase=True,
    require_lowercase=True,
    require_number=True,
    require_symbol=True,
)

# Backwards-compatible aliases for tests/imports. The policy object above is
# the source of truth used by validation and the public endpoint.
MIN_PASSWORD_LENGTH = SIGNUP_PASSWORD_POLICY.min_length
ZXCVBN_MIN_SCORE = SIGNUP_PASSWORD_POLICY.min_score


class PasswordPolicyError(ValueError):
    """Raised when a password does not meet Klai's onboarding policy."""


def get_password_policy() -> PasswordPolicy:
    return SIGNUP_PASSWORD_POLICY


def validate_password_strength(
    password: str,
    *,
    user_inputs: Iterable[str] = (),
    policy: PasswordPolicy = SIGNUP_PASSWORD_POLICY,
) -> None:
    """Raise when ``password`` does not satisfy Klai/Zitadel onboarding policy."""
    if len(password) < policy.min_length:
        raise PasswordPolicyError(PASSWORD_TOO_SHORT_MSG.format(min_length=policy.min_length))

    if policy.require_uppercase and not any(char.isupper() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_UPPERCASE_MSG)

    if policy.require_lowercase and not any(char.islower() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_LOWERCASE_MSG)

    if policy.require_number and not any(char.isdigit() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_NUMBER_MSG)

    if policy.require_symbol and not any(not char.isalnum() and not char.isspace() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_SYMBOL_MSG)

    if _zxcvbn is None:
        raise PasswordPolicyConfigurationError("zxcvbn is required for password validation")

    result = _zxcvbn(
        password,
        user_inputs=[value for value in user_inputs if value],
    )
    if int(result.get("score", 0)) < policy.min_score:
        raise PasswordPolicyError(PASSWORD_TOO_WEAK_MSG)


def is_zitadel_password_policy_error(exc: object) -> bool:
    """Return True for Zitadel 400 responses caused by password policy drift."""
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != 400:
        return False

    payload = ""
    try:
        payload = _json_dumps_lower(response.json())
    except Exception:
        payload = str(getattr(response, "text", "")).lower()

    if "password" not in payload:
        return False

    policy_markers = (
        "policy",
        "complex",
        "strength",
        "weak",
        "minimum",
        "minlength",
        "min length",
        "uppercase",
        "lowercase",
        "digit",
        "number",
        "symbol",
    )
    return any(marker in payload for marker in policy_markers)


def _json_dumps_lower(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
