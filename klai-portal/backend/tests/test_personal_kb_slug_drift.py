"""Drift-test for the shared ``personal_kb_slug`` template.

SPEC-RAG-PERSONAL-SCOPE-001 REQ-1. Pins the slug template at the portal-api
re-export site so a future refactor of either the library or the inline
helper cannot silently change the format. The same template is consumed by:

  - Provisioning (``create_default_personal_kb`` here)
  - Magic-slug shortcut (``resolve_personal_kb`` here)
  - Retrieval-api's server-side scope filter
  - Knowledge-ingest chunk metadata stamping

If this test fails, several services drift together — refuse to merge the
change until every consumer is updated in the same PR.
"""

from __future__ import annotations

import pytest

from app.services.default_knowledge_bases import personal_kb_slug


@pytest.mark.parametrize(
    "user_id, expected",
    [
        ("300000000000000002", "personal-300000000000000002"),
        ("jantine-zitadel-sub", "personal-jantine-zitadel-sub"),
        ("u1", "personal-u1"),
    ],
)
def test_personal_kb_slug_re_export_matches_canonical(user_id: str, expected: str) -> None:
    assert personal_kb_slug(user_id) == expected


def test_personal_kb_slug_matches_legacy_inline_pattern() -> None:
    """Pin the byte-equal output across all call sites that historically
    constructed the slug inline:

      - ``app.services.default_knowledge_bases.personal_kb_slug`` (pre-SPEC inline)
      - ``deploy/litellm/klai_knowledge.py`` (PR #705 inline)
      - ``klai-knowledge-ingest`` chunk-stamping code (search the repo grep)

    A drift here breaks server-side narrowing silently. Loud failure here
    is the only thing keeping the contract honest.
    """
    samples = ["jantine-zitadel-sub", "u1", "300000000000000002", ""]
    for user_id in samples:
        legacy = f"personal-{user_id}"
        assert personal_kb_slug(user_id) == legacy, (
            f"Slug template drift for user_id={user_id!r}: "
            f"helper produced {personal_kb_slug(user_id)!r}, "
            f"legacy inline produced {legacy!r}"
        )


def test_personal_kb_slug_is_callable_with_expected_signature() -> None:
    """Existing callers spread across the codebase pass a single str arg.

    If the signature ever changes (added kwargs, return type drift), this
    test fails before a downstream caller hits a runtime TypeError.
    """
    assert callable(personal_kb_slug)
    result = personal_kb_slug("test")
    assert isinstance(result, str)
    assert result == "personal-test"
