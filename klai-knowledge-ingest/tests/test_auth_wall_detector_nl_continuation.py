"""Regression tests for SPEC-INGEST-LOGIN-WALL-DETECT-001 NL FP fix.

Production canary on 2026-05-06 (voys/support, 422 pages) found 4 false
positives at 2.6% FP-rate from the original NL canonical phrases:
  - "meld u aan"   matched "Meld u aan bij het Bubble-webportaal"
                   (tutorial step, not a wall)
  - "in te loggen" matched "om in te loggen of heb je geen toegang"
                   (password-recovery FAQ, not a wall)

The fix: tighten NL phrases to require a continuation token ("om verder",
"om door", "om dit") that distinguishes a wall from instructional text. This
test pins the production fixtures so the issue cannot regress silently.

Walls SHOULD still fire:
  - "u dient in te loggen om verder te gaan."
  - "log in om dit te lezen"
  - "meld u aan om verder te gaan"
  - "aanmelden om door te gaan"

Tutorial / instructional NL content MUST NOT fire:
  - "Meld u aan bij het Bubble-webportaal" (Bubble setup step)
  - "om in te loggen of heb je geen toegang" (password recovery FAQ)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_ingest.utils.auth_wall_detector import detect_anonymous_auth_wall

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN = FIXTURES / "clean_pages"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNlFalsePositiveRegression:
    """Production fixtures that previously fired must now stay quiet."""

    def test_redcactus_zoom_setup_does_not_fire(self) -> None:
        """https://wiki.redcactus.cloud/nl/phone-software/zoom — Bubble-webportaal
        setup tutorial. Contains "Meld u aan bij het Bubble-webportaal" multiple
        times as STEP 1 of the integration setup. NOT a wall."""
        text = _read(CLEAN / "redcactus_zoom_setup_nl.md")
        result = detect_anonymous_auth_wall(text)
        assert result is None, (
            f"redcactus_zoom_setup_nl flagged as {result!r} — "
            "this is instructional content, not a wall"
        )

    def test_voys_account_toegang_does_not_fire(self) -> None:
        """https://help.voys.nl/account-toegang — password-recovery / account-
        access FAQ. Contains "om in te loggen of heb je geen toegang"
        in the body. NOT a wall."""
        text = _read(CLEAN / "voys_account_toegang.md")
        result = detect_anonymous_auth_wall(text)
        assert result is None, (
            f"voys_account_toegang flagged as {result!r} — this is a recovery FAQ, not a wall"
        )


class TestNlContinuationTokensStillFire:
    """The tightened phrases must still catch real NL walls."""

    @pytest.mark.parametrize(
        "wall_text",
        [
            "U dient in te loggen om verder te gaan.",
            "Log in om dit artikel te lezen.",
            "Meld u aan om verder te lezen.",
            "Meld u aan om door te gaan.",
            "Aanmelden om verder te gaan.",
            "Aanmelden om door te lezen.",
        ],
    )
    def test_real_wall_phrases_fire(self, wall_text: str) -> None:
        result = detect_anonymous_auth_wall(wall_text)
        assert result is not None
        assert result.pattern == "canonical_phrase_nl"
        assert result.confidence >= 0.9


class TestPureInstructionalNlNeverFires:
    """Synthetic instructional content must not trip the detector."""

    @pytest.mark.parametrize(
        "instructional_text",
        [
            # Setup step
            "Meld u aan bij het Bubble-webportaal en navigeer naar instellingen.",
            # Recovery FAQ
            "Het e-mailadres dat u gebruikt om in te loggen kunt u wijzigen "
            "via uw accountinstellingen.",
            # Tutorial
            "Klik op 'Aanmelden' om uw account aan te maken.",
            # Generic mention
            "Vergeet niet uw wachtwoord op te slaan zodat u kunt in te loggen.",
        ],
    )
    def test_instructional_text_does_not_fire(self, instructional_text: str) -> None:
        result = detect_anonymous_auth_wall(instructional_text)
        assert result is None, f"instructional text {instructional_text!r} fired as {result!r}"
