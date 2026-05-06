"""Tests for the anonymous-crawl login-wall detector.

SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-01, REQ-02.

The detector is a pure synchronous function that scans markdown for signs that
the page is a login-walled stub captured during an anonymous crawl. The most
critical case is REAL captured RedCactus content (see
tests/fixtures/auth_walls/redcactus_hubspot.md): the page has 3243 content
words but only 2 occurrences of the canonical phrase — the detector must still
flag it, because content-word count alone cannot distinguish boilerplate
template chrome from real article content.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from knowledge_ingest.utils.auth_wall_detector import (
    AuthWallSignal,
    detect_anonymous_auth_wall,
)

FIXTURES = Path(__file__).parent / "fixtures"
WALLS = FIXTURES / "auth_walls"
CLEAN = FIXTURES / "clean_pages"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# REQ-01 — Pure detector function
# ---------------------------------------------------------------------------


class TestDetectorPurity:
    """AC-01.1: importable, pure, deterministic, side-effect-free."""

    def test_returns_dataclass_or_none(self) -> None:
        result = detect_anonymous_auth_wall("plain text with no login phrases.")
        assert result is None or isinstance(result, AuthWallSignal)

    def test_deterministic(self) -> None:
        text = _read(WALLS / "redcactus_hubspot.md")
        first = detect_anonymous_auth_wall(text)
        second = detect_anonymous_auth_wall(text)
        third = detect_anonymous_auth_wall(text)
        assert first is not None
        assert second is not None
        assert third is not None
        # Same pattern + evidence each time.
        assert first.pattern == second.pattern == third.pattern
        assert first.evidence == second.evidence == third.evidence
        assert first.confidence == second.confidence == third.confidence

    def test_no_exception_on_empty_input(self) -> None:
        assert detect_anonymous_auth_wall("") is None
        assert detect_anonymous_auth_wall("   \n\n\t  ") is None

    def test_no_exception_on_huge_input(self) -> None:
        # 1 MB of garbage should not crash. Performance asserted separately.
        text = "lorem ipsum " * 100_000
        result = detect_anonymous_auth_wall(text)
        assert result is None


class TestDetectorPerformance:
    """AC-01.2: p99 latency < 1ms on 100KB input."""

    def test_p99_under_1ms_on_100kb(self) -> None:
        # Real walled fixture is ~31KB; pad with template-chrome-style copy
        # to get to ~100KB.
        base = _read(WALLS / "redcactus_hubspot.md")
        padded = base + ("\n\n## More boilerplate\n\nSome content paragraph. " * 1500)
        # Sanity: aim for >= 100KB.
        assert len(padded) >= 100_000

        timings_ms = []
        for _ in range(200):  # smaller N — keep test runtime reasonable in CI.
            t0 = time.perf_counter()
            detect_anonymous_auth_wall(padded)
            timings_ms.append((time.perf_counter() - t0) * 1000)

        timings_ms.sort()
        p50 = timings_ms[len(timings_ms) // 2]
        p99 = timings_ms[int(len(timings_ms) * 0.99)]
        assert p50 < 0.5, f"p50 {p50:.3f}ms exceeds 0.5ms budget"
        assert p99 < 1.0, f"p99 {p99:.3f}ms exceeds 1ms budget"


class TestFitMarkdownPrecedence:
    """AC-01.3: fit_markdown takes precedence over raw_markdown for FP-guard."""

    def test_clean_fit_markdown_wins_over_dirty_raw(self) -> None:
        # raw has 6 weak-signal redirect_to= matches in nav chrome.
        raw = "[Login](/login?redirect_to=/a) " * 6 + "Some short text."
        # fit is a clean tutorial (>= 500 non-login content words).
        fit = "Lorem ipsum dolor sit amet. " * 200
        assert detect_anonymous_auth_wall(raw, fit_markdown=fit) is None

    def test_dirty_fit_markdown_overrides_clean_raw(self) -> None:
        raw = "Plain product description with no login terminology."
        fit = "you will have to log in with your example account to continue"
        result = detect_anonymous_auth_wall(raw, fit_markdown=fit)
        assert result is not None
        assert result.pattern == "canonical_phrase_en"


# ---------------------------------------------------------------------------
# REQ-02 — Detection patterns
# ---------------------------------------------------------------------------


class TestCanonicalPhraseEN:
    """AC-02.1, AC-02.7: English canonical phrases are STRONG signals."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "Please log in to read the rest of this article.",
            "You will need to sign in to continue.",
            "you will have to log in to view this content",
            "Please log in with your account to access these docs.",
            "This article requires authentication to view.",
            "Please sign in to view the full article.",
        ],
    )
    def test_english_canonical_phrases_fire(self, phrase: str) -> None:
        result = detect_anonymous_auth_wall(phrase)
        assert result is not None
        assert result.pattern == "canonical_phrase_en"
        assert result.confidence >= 0.9
        assert any(
            kw in evidence.lower()
            for evidence in result.evidence
            for kw in ("log in", "sign in", "authentication")
        )

    def test_real_redcactus_hubspot_fixture_fires(self) -> None:
        """AC-02.7: real captured RedCactus HubSpot page must flag positive."""
        text = _read(WALLS / "redcactus_hubspot.md")
        result = detect_anonymous_auth_wall(text)
        assert result is not None
        assert result.pattern == "canonical_phrase_en"
        assert result.confidence >= 0.9

    def test_real_redcactus_hubspot_embedded_fixture_fires(self) -> None:
        text = _read(WALLS / "redcactus_hubspot_embedded.md")
        result = detect_anonymous_auth_wall(text)
        assert result is not None
        assert result.pattern == "canonical_phrase_en"


class TestCanonicalPhraseNL:
    """AC-02.2: Dutch canonical phrases are STRONG signals."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "U dient in te loggen om verder te gaan.",
            "Log in om dit te lezen.",
            "Meld u aan om verder te gaan met dit artikel.",
        ],
    )
    def test_dutch_canonical_phrases_fire(self, phrase: str) -> None:
        result = detect_anonymous_auth_wall(phrase)
        assert result is not None
        assert result.pattern == "canonical_phrase_nl"
        assert result.confidence >= 0.9


class TestGermanNotMatched:
    """AC-02.2b: DE-only login content is NOT flagged in v1 (documented gap)."""

    def test_de_only_does_not_fire(self) -> None:
        text = _read(CLEAN / "de_only_login.md")
        result = detect_anonymous_auth_wall(text)
        assert result is None, (
            "DE-only login pages without redirect_to= density should NOT "
            "fire in v1. If a DE tenant onboards, extend canonical_phrase_de "
            "in a follow-up SPEC and update this test."
        )


class TestRedirectDensity:
    """AC-02.3: weak signal — redirect_to= ≥ 5 matches."""

    def test_high_redirect_density_fires(self) -> None:
        # 6 redirect_to= occurrences, no canonical phrase, short content.
        raw = (
            "[a](/login?redirect_to=/page1) "
            "[b](/login?redirect_to=/page2) "
            "[c](/login?redirect_to=/page3) "
            "[d](/login?redirect_to=/page4) "
            "[e](/login?redirect_to=/page5) "
            "[f](/login?redirect_to=/page6) "
            "Members only."
        )
        result = detect_anonymous_auth_wall(raw)
        assert result is not None
        assert result.pattern == "redirect_density"
        assert "6" in " ".join(result.evidence)

    def test_low_redirect_density_does_not_fire(self) -> None:
        raw = "[a](/login?redirect_to=/page1) [b](/login?redirect_to=/page2)"
        result = detect_anonymous_auth_wall(raw)
        assert result is None


class TestLoginLinkRepetition:
    """AC-02.4: weak signal — same /login href repeated ≥ 5 times."""

    def test_repeated_login_anchor_fires(self) -> None:
        raw = ("[Sign in](https://example.com/login) " * 6) + "Members only."
        result = detect_anonymous_auth_wall(raw)
        assert result is not None
        assert result.pattern in ("login_link_repetition", "redirect_density")
        # Either pattern is acceptable here — the repeated href contains /login.


class TestContentLoginRatio:
    """AC-02.5: weak signal — < 100 content words AND >= 3 login anchors."""

    def test_short_page_with_many_login_links_fires(self) -> None:
        raw = "[Sign in](/login/a) [Sign in](/login/b) [Sign in](/login/c) Short page."
        result = detect_anonymous_auth_wall(raw)
        assert result is not None


class TestFalsePositiveGuard:
    """AC-02.6, AC-02.6b: FP-guard protects WEAK signals only."""

    def test_clean_redcactus_ifttt_does_not_fire(self) -> None:
        text = _read(CLEAN / "redcactus_ifttt.md")
        result = detect_anonymous_auth_wall(text)
        assert result is None

    def test_auth_documentation_tutorial_does_not_fire(self) -> None:
        """AC-02.6: WEAK signals (6x /login anchors) + 500+ content words -> None."""
        text = _read(CLEAN / "auth_documentation_tutorial.md")
        result = detect_anonymous_auth_wall(text)
        assert result is None, (
            "Tutorial pages discussing authentication should not fire — "
            "they contain login links in chrome but have no canonical "
            "wall phrase and abundant clean content."
        )

    def test_canonical_phrase_overrides_word_count(self) -> None:
        """AC-02.6b: Condition A is STRONG; word count cannot veto it."""
        # 3243-word RedCactus page must STILL fire.
        text = _read(WALLS / "redcactus_hubspot.md")
        result = detect_anonymous_auth_wall(text)
        assert result is not None
        assert result.pattern == "canonical_phrase_en"


class TestSyntheticFixtures:
    """Verify synthetic Confluence/WordPress/Notion fixtures all fire."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "confluence_login_required.md",
            "wordpress_login_redirect.md",
            "notion_private_page.md",
        ],
    )
    def test_synthetic_walls_fire(self, fixture_name: str) -> None:
        text = _read(WALLS / fixture_name)
        result = detect_anonymous_auth_wall(text)
        assert result is not None, f"{fixture_name} did not fire"
        assert result.confidence >= 0.7
