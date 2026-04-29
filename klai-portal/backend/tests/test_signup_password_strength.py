"""SPEC-SEC-HYGIENE-001 REQ-22 / AC-22: zxcvbn-backed password strength.

Pre-fix: SignupRequest accepted any password ≥12 chars. ``Password1234``
or ``aaaaaaaaaaaa`` slipped through. This adds a zxcvbn score-3 floor
plus a user_inputs context (email, first_name, last_name, company_name)
so passwords like ``Voys2026Klai`` for company "Voys" score low.

Tests at the Pydantic-validation level (no FastAPI app needed).
"""

from __future__ import annotations

from unittest.mock import patch

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
        SignupRequest(**_payload("Short1!"))  # 7 chars
    msg = str(exc_info.value)
    assert "minimaal 12 tekens" in msg or "minimaal 12" in msg


@pytest.mark.parametrize(
    "weak_password",
    [
        "Password1234",       # zxcvbn score 1
        "aaaaaaaaaaaa",       # all-same chars, score 0
        "1234567890ab",       # numeric run + suffix, score 0
    ],
)
def test_weak_password_rejected_by_zxcvbn(weak_password: str) -> None:
    """REQ-22.1: zxcvbn score < 3 → reject with the SPEC's Dutch message."""
    with pytest.raises(ValidationError) as exc_info:
        SignupRequest(**_payload(weak_password))
    msg = str(exc_info.value)
    assert "Wachtwoord is te zwak" in msg, (
        f"Expected the SPEC-mandated Dutch error for {weak_password!r}; got:\n{msg}"
    )


def test_user_input_context_lowers_score() -> None:
    """REQ-22.3: user_inputs (email/first_name/last_name/company_name) MUST
    be passed to zxcvbn so a password derived from the user's own PII
    scores below threshold even if it would otherwise look ok.

    "Mark.Vletter" scores 3 (passes) without context, but drops to 2
    once first_name/last_name are passed as user_inputs — the canonical
    proof that the wiring is in effect.
    """
    with pytest.raises(ValidationError) as exc_info:
        SignupRequest(
            **_payload(
                "Mark.Vletter",
                first_name="Mark",
                last_name="Vletter",
                email="mark@voys.nl",
                company_name="Voys",
            )
        )
    assert "Wachtwoord is te zwak" in str(exc_info.value)


def test_strong_passphrase_accepted() -> None:
    """REQ-22.1 positive: a high-entropy passphrase passes."""
    body = SignupRequest(**_payload("correct horse battery staple"))
    assert body.password == "correct horse battery staple"


def test_zxcvbn_unavailable_falls_back_to_length() -> None:
    """REQ-22.4: when zxcvbn import fails at module load, the validator
    falls back to length-only. We simulate the unavailability flag.
    """
    from app.api import signup as signup_module

    with patch.object(signup_module, "_ZXCVBN_AVAILABLE", False):
        # ``Password1234`` would be rejected by zxcvbn (score 1) but is OK
        # under length-only fallback.
        body = SignupRequest(**_payload("Password1234"))
    assert body.password == "Password1234"
