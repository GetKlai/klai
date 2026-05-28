"""Unit tests for :func:`klai_kb_slugs.personal_kb_slug`.

The function is trivial by design (a single f-string) — the value of
these tests is pinning the EXACT output shape. Any future refactor that
changes the template silently would break consumers (portal-api
provisioning, retrieval-api scope filter, knowledge-ingest chunk
metadata) and these tests catch it loudly.

See SPEC-RAG-PERSONAL-SCOPE-001 REQ-1.
"""

from __future__ import annotations

import pytest

from klai_kb_slugs import personal_kb_slug


@pytest.mark.parametrize(
    "user_id, expected",
    [
        ("300000000000000002", "personal-300000000000000002"),
        ("u1", "personal-u1"),
        ("abc-def-ghi", "personal-abc-def-ghi"),
        # Empty user_id is permitted at the lib level (validation lives in retrieve.py's
        # ``user_id required`` 400). The lib is a pure string helper.
        ("", "personal-"),
    ],
)
def test_personal_kb_slug_matches_template(user_id: str, expected: str) -> None:
    assert personal_kb_slug(user_id) == expected


def test_personal_kb_slug_matches_legacy_inline_pattern() -> None:
    """Guards against accidental rename of the slug prefix.

    Before SPEC-RAG-PERSONAL-SCOPE-001, the template was an inline f-string
    in ``klai-portal/backend/app/services/default_knowledge_bases.py``:

        def personal_kb_slug(user_id: str) -> str:
            return f"personal-{user_id}"

    This test pins the byte-equal output so that a future contributor
    cannot silently introduce a different template via this library.
    """
    samples = ["jantine-zitadel-sub", "u1", "300000000000000002", ""]
    for user_id in samples:
        legacy_inline = f"personal-{user_id}"
        assert personal_kb_slug(user_id) == legacy_inline, (
            f"Slug template drift for user_id={user_id!r}: "
            f"lib produced {personal_kb_slug(user_id)!r}, "
            f"legacy inline produced {legacy_inline!r}"
        )


def test_personal_kb_slug_is_in_dunder_all() -> None:
    """Public-API surface is intentionally narrow — exactly one export.

    Adding new exports requires updating ``__all__``; this test ensures
    re-exports stay deliberate.
    """
    import klai_kb_slugs

    assert klai_kb_slugs.__all__ == ["personal_kb_slug"]
