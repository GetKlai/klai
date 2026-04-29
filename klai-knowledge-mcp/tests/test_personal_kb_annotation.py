"""HY-48 — Personal-KB slug annotation (docs-only).

SPEC-SEC-HYGIENE-001 REQ-48.1, REQ-48.2, REQ-48.3.

The personal-KB slug is derived deterministically from the verified user_id
(``f"personal-{verified.user_id}"``). An attacker who learns a victim's Zitadel
``sub`` can reconstruct the slug. The structural fix (membership-check between
user_id and KB) lives in SPEC-SEC-IDENTITY-ASSERT-001 — already shipped on
``main`` — which neutralises the attack chain even though the slug stays
guessable. This SPEC documents the intentional non-rotation of the slug
format so the hygiene angle stays visible to future readers.

Test is grep-shaped: REQ-48.2 forbids a code change; the deliverable is the
annotation only.
"""

from __future__ import annotations

import re
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"

# Match either the historical (`identity.user_id`) or post-IDENTITY-ASSERT-001
# (`verified.user_id`) form. The hygiene concern is the literal slug shape
# `f"personal-{...user_id}"` — what name the attribute carries upstream is
# orthogonal to this SPEC.
_SLUG_PATTERN = re.compile(
    r"""kb_slug\s*=\s*f["']personal-\{(?P<src>[a-z_]+)\.user_id\}["']""",
    flags=re.IGNORECASE,
)


def test_personal_slug_construction_site_carries_mx_note() -> None:
    """REQ-48.1 / REQ-48.3 — annotation immediately precedes the slug line."""
    text = MAIN_PY.read_text(encoding="utf-8")
    lines = text.splitlines()

    slug_line_idx: int | None = None
    for i, line in enumerate(lines):
        if _SLUG_PATTERN.search(line):
            slug_line_idx = i
            break
    assert slug_line_idx is not None, (
        'expected a kb_slug=f"personal-{...user_id}" line in main.py — '
        "is the personal-KB tool still wired? if it moved, this test "
        "needs to follow."
    )

    # Annotation block lives in the comment lines immediately above the
    # construction site. Look back at most 16 lines: the @MX:NOTE carries
    # WHY-context (companion SPEC + non-rotation rationale + future
    # migration ownership), which legitimately runs 10-12 lines.
    window = "\n".join(lines[max(0, slug_line_idx - 16) : slug_line_idx])
    assert "@MX:NOTE" in window, (
        f"expected @MX:NOTE on a comment line preceding line {slug_line_idx + 1}"
    )

    note_text = window.lower()
    assert "spec-sec-identity-assert-001" in note_text, (
        "MX:NOTE must reference SPEC-SEC-IDENTITY-ASSERT-001 (companion SPEC "
        "covering the structural membership-check fix)"
    )
    assert "spec-sec-hygiene-001" in note_text, "MX:NOTE must reference SPEC-SEC-HYGIENE-001"
    assert "req-48" in note_text, "MX:NOTE must reference REQ-48"


def test_personal_slug_format_unchanged() -> None:
    """REQ-48.2 — no code change to the derivation strategy.

    Rotating the slug derivation (e.g. switching to an opaque hash) would break
    every existing personal KB. That migration is IDENTITY-ASSERT-001's
    responsibility, not ours. Fail this test if the slug format ever silently
    diverges from f"personal-{...user_id}".
    """
    text = MAIN_PY.read_text(encoding="utf-8")
    matches = _SLUG_PATTERN.findall(text)
    assert matches, "personal-KB slug derivation pattern not found"
    # The literal "personal-" prefix is the part REQ-48.2 protects. Spot-check
    # by re-reading the line and confirming the f-string template is intact.
    assert any("personal-" in line for line in text.splitlines() if "kb_slug" in line)
