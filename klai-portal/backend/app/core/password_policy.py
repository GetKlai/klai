"""Shared onboarding password policy.

The invite activation flow must validate the password before consuming the
one-time invite code. Otherwise Zitadel can accept the invite code, reject the
password, and leave the user with a used-up link.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
ZXCVBN_MIN_SCORE = 3
PASSWORD_TOO_SHORT_MSG = "Wachtwoord moet minimaal 12 tekens bevatten"
PASSWORD_MISSING_UPPERCASE_MSG = "Wachtwoord moet minimaal één hoofdletter bevatten"
PASSWORD_MISSING_LOWERCASE_MSG = "Wachtwoord moet minimaal één kleine letter bevatten"
PASSWORD_MISSING_NUMBER_MSG = "Wachtwoord moet minimaal één cijfer bevatten"
PASSWORD_MISSING_SYMBOL_MSG = "Wachtwoord moet minimaal één symbool bevatten"
PASSWORD_TOO_WEAK_MSG = "Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."

try:
    from zxcvbn import zxcvbn as _zxcvbn

    _ZXCVBN_AVAILABLE = True
except ImportError:
    _zxcvbn = None  # type: ignore[assignment]
    _ZXCVBN_AVAILABLE = False
    logger.exception("zxcvbn_unavailable_falling_back_to_length_check")


class PasswordPolicyError(ValueError):
    """Raised when a password does not meet Klai's onboarding policy."""


def validate_password_strength(password: str, *, user_inputs: Iterable[str] = ()) -> None:
    """Raise when ``password`` does not satisfy Klai/Zitadel onboarding policy."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(PASSWORD_TOO_SHORT_MSG)

    if not any(char.isupper() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_UPPERCASE_MSG)

    if not any(char.islower() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_LOWERCASE_MSG)

    if not any(char.isdigit() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_NUMBER_MSG)

    if not any(not char.isalnum() and not char.isspace() for char in password):
        raise PasswordPolicyError(PASSWORD_MISSING_SYMBOL_MSG)

    if not _ZXCVBN_AVAILABLE or _zxcvbn is None:
        return

    result = _zxcvbn(
        password,
        user_inputs=[value for value in user_inputs if value],
    )
    if int(result.get("score", 0)) < ZXCVBN_MIN_SCORE:
        raise PasswordPolicyError(PASSWORD_TOO_WEAK_MSG)
