"""SPEC-SEC-HYGIENE-001 REQ-22 / AC-22: zxcvbn-backed password strength.

Pre-fix: SignupRequest accepted any password ≥12 chars. ``Password1234``
or ``aaaaaaaaaaaa`` slipped through. This adds a zxcvbn score-3 floor
plus a user_inputs context (email, first_name, last_name, company_name)
so passwords like ``Voys2026Klai`` for company "Voys" score low.

Tests at the Pydantic-validation level (no FastAPI app needed).
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from app.api.signup import SignupRequest


def _payload(password: str, **overrides: str) -> dict[str, str]:
    base = {
        "first_name": "Mark",
        "last_name": "Vletter",
        "email": "mark@voys.nl",
        "password": password,
        "company_name": "Voys",
        "preferred_language": "nl",
    }
    base.update(overrides)
    return base


def test_short_password_rejected_with_length_error() -> None:
    """REQ-22.2: minimum-length gate fires first; zxcvbn never sees it."""
    with pytest.raises(ValidationError) as exc_info:
        SignupRequest(**_payload("short phrase"))  # 12 chars
    msg = str(exc_info.value)
    assert "minimaal 15 tekens" in msg or "minimaal 15" in msg


@pytest.mark.parametrize(
    "weak_password",
    [
        "PasswordPassword2026",  # zxcvbn score 1
        "QwertyQwertyQwerty",  # keyboard walk, score 0
        "WelcomeWelcome2026",  # common word + numeric suffix, score 2
    ],
)
def test_weak_password_rejected_by_zxcvbn(weak_password: str) -> None:
    """REQ-22.1: zxcvbn score < 3 → reject with the SPEC's Dutch message."""
    with pytest.raises(ValidationError) as exc_info:
        SignupRequest(**_payload(weak_password))
    msg = str(exc_info.value)
    assert "Wachtwoord is te zwak" in msg, f"Expected the SPEC-mandated Dutch error for {weak_password!r}; got:\n{msg}"


def test_user_input_context_is_passed_to_zxcvbn() -> None:
    """REQ-22.3: user_inputs (email/first_name/last_name/company_name) MUST
    be passed to zxcvbn so it can score against the user's own context.
    """
    from app.core import password_policy

    zxcvbn = Mock(return_value={"score": 2})
    with patch.object(password_policy, "_zxcvbn", zxcvbn):
        with pytest.raises(ValidationError) as exc_info:
            SignupRequest(
                **_payload(
                    "Mark!Vletter2026",
                    first_name="Mark",
                    last_name="Vletter",
                    email="mark@voys.nl",
                    company_name="Voys",
                )
            )

    zxcvbn.assert_called_once()
    assert zxcvbn.call_args.kwargs["user_inputs"] == ["mark@voys.nl", "Mark", "Vletter", "Voys"]
    assert "Wachtwoord is te zwak" in str(exc_info.value)


def test_user_context_password_can_be_rejected_by_zxcvbn() -> None:
    """A password containing user PII can still be rejected by zxcvbn."""
    with pytest.raises(ValidationError) as exc_info:
        SignupRequest(
            **_payload(
                "QwertyQwertyQwerty",
                first_name="Mark",
                last_name="Vletter",
                email="mark@voys.nl",
                company_name="Voys",
            )
        )
    assert "Wachtwoord is te zwak" in str(exc_info.value)


def test_strong_passphrase_accepted() -> None:
    """REQ-22.1 positive: a high-entropy passphrase passes."""
    body = SignupRequest(**_payload("violet meadow lantern orbit"))
    assert body.password == "violet meadow lantern orbit"


def test_modern_policy_does_not_require_character_classes() -> None:
    """Modern policy is length + zxcvbn strength, not legacy composition."""
    body = SignupRequest(**_payload("violet meadow lantern orbit"))
    assert body.password == "violet meadow lantern orbit"


def test_zxcvbn_unavailable_fails_loud() -> None:
    """REQ-22.4: if zxcvbn is unavailable, backend validation must not silently weaken."""
    from app.core import password_policy
    from app.core.password_policy import PasswordPolicyConfigurationError, validate_password_strength

    with patch.object(password_policy, "_zxcvbn", None):
        with pytest.raises(PasswordPolicyConfigurationError):
            validate_password_strength("violet meadow lantern orbit")
