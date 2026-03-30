"""Tests for SPEC-TAXONOMY-001: assertion_mode taxonomy alignment in knowledge-ingest.

RED phase: these tests define the expected behavior for the new 6-value taxonomy
and backward-compatible mapping from old values.
"""
import time

import pytest

from knowledge_ingest.routes.ingest import _parse_knowledge_fields

_SENTINEL = 253402300800


class TestAssertionModeType:
    """The AssertionMode Literal and VALID_ASSERTION_MODES must exist in models.py."""

    def test_valid_assertion_modes_has_six_values(self):
        from knowledge_ingest.models import VALID_ASSERTION_MODES

        assert VALID_ASSERTION_MODES == frozenset(
            {"fact", "claim", "speculation", "procedural", "quoted", "unknown"}
        )

    def test_assertion_mode_literal_exists(self):
        from knowledge_ingest.models import AssertionMode
        from typing import get_args

        args = set(get_args(AssertionMode))
        assert args == {"fact", "claim", "speculation", "procedural", "quoted", "unknown"}


class TestParseKnowledgeFieldsNewTaxonomy:
    """_parse_knowledge_fields must use the new taxonomy with backward-compatible mapping."""

    def test_no_frontmatter_defaults_to_unknown(self):
        """Default assertion_mode (no frontmatter) must be 'unknown', not 'factual'."""
        result = _parse_knowledge_fields("# Plain document\n\nNo frontmatter.", None)
        assert result["assertion_mode"] == "unknown"

    def test_new_values_accepted_directly(self):
        """All 6 new values must be accepted directly from frontmatter."""
        for mode in ("fact", "claim", "speculation", "procedural", "quoted", "unknown"):
            content = f"---\nassertion_mode: {mode}\n---\n# Doc"
            result = _parse_knowledge_fields(content, None)
            assert result["assertion_mode"] == mode, f"Failed for {mode}"

    def test_backward_compat_factual_maps_to_fact(self):
        content = "---\nassertion_mode: factual\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "fact"

    def test_backward_compat_belief_maps_to_claim(self):
        content = "---\nassertion_mode: belief\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "claim"

    def test_backward_compat_hypothesis_maps_to_speculation(self):
        content = "---\nassertion_mode: hypothesis\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "speculation"

    def test_backward_compat_note_maps_to_unknown(self):
        content = "---\nassertion_mode: note\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "unknown"

    def test_invalid_assertion_mode_defaults_to_unknown(self):
        content = "---\nassertion_mode: opinion\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "unknown"

    def test_procedural_preserved(self):
        """'procedural' exists in both old and new vocabularies — must stay 'procedural'."""
        content = "---\nassertion_mode: procedural\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "procedural"

    def test_quoted_preserved(self):
        """'quoted' exists in both old and new vocabularies — must stay 'quoted'."""
        content = "---\nassertion_mode: quoted\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "quoted"


class TestQdrantStoreAssertionModeAllowed:
    """assertion_mode must be in _ALLOWED_METADATA_FIELDS (already present, verify kept)."""

    def test_assertion_mode_in_allowed_fields(self):
        from knowledge_ingest.qdrant_store import _ALLOWED_METADATA_FIELDS

        assert "assertion_mode" in _ALLOWED_METADATA_FIELDS
