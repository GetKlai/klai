"""Drift-detection: ``consent.css`` Klai tokens must match ``index.css``.

The OAuth consent page is server-rendered HTML in the backend and so cannot
@import the SPA's CSS. ``klai-portal/backend/app/static/oauth/consent.css``
duplicates the ``--color-rl-*`` token block from
``klai-portal/frontend/src/index.css``. This test fails if the two
diverge: a developer who rebrands the SPA must update both files
together (or this test will block CI until they do).

Tokens covered: every ``--color-rl-…`` declaration in index.css's
``@theme inline {}`` block. Other token namespaces (``--color-foreground``,
shadcn semantic aliases, sidebar variants) are NOT mirrored — consent.css
references them only via the ``--color-rl-*`` source-of-truth tokens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root is two levels up from this test file:
#   klai-portal/backend/tests/test_consent_css_token_drift.py
#   ── ../../../  → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_INDEX_CSS = _REPO_ROOT / "klai-portal" / "frontend" / "src" / "index.css"
_CONSENT_CSS = _REPO_ROOT / "klai-portal" / "backend" / "app" / "static" / "oauth" / "consent.css"

# Match lines like ``  --color-rl-accent: #fcaa2d;`` regardless of indentation
# or trailing comment. Capture (token-name, value).
_TOKEN_RE = re.compile(r"^\s*(--color-rl-[A-Za-z0-9-]+)\s*:\s*([^;]+?)\s*;", re.MULTILINE)


def _extract_tokens(css_path: Path) -> dict[str, str]:
    text = css_path.read_text(encoding="utf-8")
    return {name: value.strip() for name, value in _TOKEN_RE.findall(text)}


@pytest.fixture(scope="module")
def index_tokens() -> dict[str, str]:
    if not _INDEX_CSS.exists():
        pytest.skip(f"index.css not found at {_INDEX_CSS}")
    tokens = _extract_tokens(_INDEX_CSS)
    assert tokens, "Expected --color-rl-* tokens in index.css; refactor may have moved them"
    return tokens


@pytest.fixture(scope="module")
def consent_tokens() -> dict[str, str]:
    assert _CONSENT_CSS.exists(), f"consent.css not found at {_CONSENT_CSS}"
    tokens = _extract_tokens(_CONSENT_CSS)
    assert tokens, "Expected --color-rl-* tokens in consent.css"
    return tokens


def test_every_consent_token_matches_index(
    index_tokens: dict[str, str],
    consent_tokens: dict[str, str],
) -> None:
    """Every token in consent.css must have an identical value in index.css.

    Asymmetric on purpose: index.css MAY define more tokens than consent.css
    needs (the SPA uses more), but consent.css MUST NOT diverge on any
    token it duplicates. A diff here means the rebrand updated index.css
    and forgot to mirror it.
    """
    drift: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for name, consent_value in consent_tokens.items():
        index_value = index_tokens.get(name)
        if index_value is None:
            missing.append(name)
            continue
        if index_value != consent_value:
            drift.append((name, index_value, consent_value))

    if missing:
        pytest.fail(
            "consent.css declares Klai tokens that index.css no longer has — "
            "either rename them in both files or drop them from consent.css:\n  - " + "\n  - ".join(missing)
        )
    if drift:
        rows = [f"{n}: index='{i}', consent='{c}'" for n, i, c in drift]
        pytest.fail(
            "Klai brand-token drift between index.css and consent.css "
            "(SPA rebrand probably forgot to mirror to consent.css):\n  - " + "\n  - ".join(rows)
        )


def test_consent_css_uses_only_rl_namespace_for_brand(consent_tokens: dict[str, str]) -> None:
    """Sanity guard: consent.css mirrors ONLY the ``--color-rl-*`` namespace.

    Other token namespaces (shadcn semantic ``--color-foreground`` etc.,
    sidebar variants) are derived from the rl-* tokens inside the SPA.
    Re-declaring those in consent.css would re-introduce drift in a
    different namespace. Block accidental copies.
    """
    suspect = [name for name in consent_tokens if not name.startswith("--color-rl-")]
    assert not suspect, (
        "consent.css should only mirror --color-rl-* tokens from index.css; "
        f"found extraneous brand-token declarations: {suspect}. "
        "Reference these via the rl-* tokens or shadcn aliases in CSS instead."
    )
