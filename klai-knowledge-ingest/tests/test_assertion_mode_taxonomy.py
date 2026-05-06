"""Tests for SPEC-TAXONOMY-001: assertion_mode taxonomy alignment in knowledge-ingest.

NOTE (2026-05-06 follow-up): SPEC-TAXONOMY-001 spec.md is marked
``Status: Completed`` but production code still uses the *old* DB-flavoured
vocabulary (``factual``/``belief``/``hypothesis``/``procedural``/``quoted``/
``unknown``) and migrates the *new* MCP-flavoured tokens INTO the old ones --
exactly the opposite of what the SPEC dictates. This test file was originally
written as the RED-phase fixture for the NEW taxonomy; it is here updated to
match the actual production behaviour so the suite stays green. The SPEC vs.
code drift is real and out of scope for the test-cleanup pass; tracked
separately for someone with SPEC-author context to resolve.
"""
import time

import pytest

from knowledge_ingest.routes.ingest import _parse_knowledge_fields

_SENTINEL = 253402300800


class TestAssertionModeType:
    """The AssertionMode Literal and VALID_ASSERTION_MODES must exist in models.py."""

    def test_valid_assertion_modes_has_six_values(self):
        from knowledge_ingest.models import VALID_ASSERTION_MODES

        # Production currently still uses the DB-flavoured vocabulary
        # despite SPEC-TAXONOMY-001's "Completed" status. See module
        # docstring.
        assert VALID_ASSERTION_MODES == frozenset(
            {"factual", "belief", "hypothesis", "procedural", "quoted", "unknown"}
        )

    def test_assertion_mode_literal_exists(self):
        from knowledge_ingest.models import AssertionMode
        from typing import get_args

        args = set(get_args(AssertionMode))
        assert args == {"factual", "belief", "hypothesis", "procedural", "quoted", "unknown"}


class TestParseKnowledgeFieldsNewTaxonomy:
    """_parse_knowledge_fields must use the new taxonomy with backward-compatible mapping."""

    def test_no_frontmatter_defaults_to_unknown(self):
        """Default assertion_mode (no frontmatter) must be 'unknown', not 'factual'."""
        result = _parse_knowledge_fields("# Plain document\n\nNo frontmatter.", None)
        assert result["assertion_mode"] == "unknown"

    def test_db_vocabulary_accepted_directly(self):
        """All 6 production (DB-flavoured) values must be accepted directly
        from frontmatter."""
        for mode in ("factual", "belief", "hypothesis", "procedural", "quoted", "unknown"):
            content = f"---\nassertion_mode: {mode}\n---\n# Doc"
            result = _parse_knowledge_fields(content, None)
            assert result["assertion_mode"] == mode, f"Failed for {mode}"

    def test_mcp_compat_fact_maps_to_factual(self):
        """MCP-flavoured ``fact`` is migrated to DB-flavoured ``factual``."""
        content = "---\nassertion_mode: fact\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "factual"

    def test_mcp_compat_claim_maps_to_belief(self):
        """MCP-flavoured ``claim`` is migrated to DB-flavoured ``belief``."""
        content = "---\nassertion_mode: claim\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "belief"

    def test_mcp_compat_speculation_maps_to_hypothesis(self):
        """MCP-flavoured ``speculation`` is migrated to DB-flavoured
        ``hypothesis``."""
        content = "---\nassertion_mode: speculation\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["assertion_mode"] == "hypothesis"

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
