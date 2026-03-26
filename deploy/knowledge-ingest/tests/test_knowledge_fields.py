"""
Tests for _parse_knowledge_fields() and db._parse_dsn().

These are pure functions — no mocking needed.
"""
import pytest

from knowledge_ingest.routes.ingest import _parse_knowledge_fields
from knowledge_ingest.db import _parse_dsn

_SENTINEL = 253402300800


# ── _parse_knowledge_fields ──────────────────────────────────────────────────

class TestParseKnowledgeFieldsDefaults:
    def test_no_frontmatter_returns_defaults(self):
        result = _parse_knowledge_fields("# Plain document\n\nNo frontmatter here.", None)
        assert result["provenance_type"] == "observed"
        assert result["assertion_mode"] == "factual"
        assert result["synthesis_depth"] == 0
        assert result["confidence"] is None
        assert result["belief_time_end"] == _SENTINEL

    def test_source_type_docs_defaults_synthesis_depth_4(self):
        result = _parse_knowledge_fields("No frontmatter.", "docs")
        assert result["synthesis_depth"] == 4

    def test_source_type_connector_defaults_synthesis_depth_0(self):
        result = _parse_knowledge_fields("No frontmatter.", "connector")
        assert result["synthesis_depth"] == 0

    def test_empty_frontmatter_returns_defaults(self):
        content = "---\n---\n# Document"
        result = _parse_knowledge_fields(content, None)
        assert result["provenance_type"] == "observed"

    def test_invalid_yaml_returns_defaults(self):
        content = "---\n: bad: yaml:\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        assert result["provenance_type"] == "observed"


class TestParseKnowledgeFieldsFromFrontmatter:
    def test_valid_provenance_type(self):
        for pt in ("observed", "extracted", "synthesized", "revised"):
            content = f"---\nprovenance_type: {pt}\n---\n# Doc"
            assert _parse_knowledge_fields(content, None)["provenance_type"] == pt

    def test_invalid_provenance_type_falls_back_to_default(self):
        content = "---\nprovenance_type: invented\n---\n# Doc"
        assert _parse_knowledge_fields(content, None)["provenance_type"] == "observed"

    def test_valid_assertion_mode(self):
        for mode in ("factual", "procedural", "quoted", "belief", "hypothesis"):
            content = f"---\nassertion_mode: {mode}\n---\n# Doc"
            assert _parse_knowledge_fields(content, None)["assertion_mode"] == mode

    def test_invalid_assertion_mode_falls_back(self):
        content = "---\nassertion_mode: opinion\n---\n# Doc"
        assert _parse_knowledge_fields(content, None)["assertion_mode"] == "factual"

    def test_synthesis_depth_range(self):
        for depth in range(5):
            content = f"---\nsynthesis_depth: {depth}\n---\n# Doc"
            assert _parse_knowledge_fields(content, None)["synthesis_depth"] == depth

    def test_synthesis_depth_out_of_range_falls_back(self):
        for bad in (-1, 5, 99):
            content = f"---\nsynthesis_depth: {bad}\n---\n# Doc"
            result = _parse_knowledge_fields(content, "docs")
            assert result["synthesis_depth"] == 4  # source_type=docs default

    def test_valid_confidence_values(self):
        for conf in ("high", "medium", "low"):
            content = f"---\nconfidence: {conf}\n---\n# Doc"
            assert _parse_knowledge_fields(content, None)["confidence"] == conf

    def test_invalid_confidence_returns_none(self):
        content = "---\nconfidence: very_high\n---\n# Doc"
        assert _parse_knowledge_fields(content, None)["confidence"] is None

    def test_belief_time_start_parsed_from_iso_string(self):
        content = "---\nbelief_time_start: '2024-01-15'\n---\n# Doc"
        result = _parse_knowledge_fields(content, None)
        # 2024-01-15 00:00:00 UTC = 1705276800
        assert result["belief_time_start"] == 1705276800

    def test_belief_time_start_invalid_string_falls_back_to_now(self):
        import time
        content = "---\nbelief_time_start: 'not-a-date'\n---\n# Doc"
        before = int(time.time()) - 1
        result = _parse_knowledge_fields(content, None)
        assert result["belief_time_start"] >= before

    def test_explicit_frontmatter_overrides_source_type_default(self):
        content = "---\nsynthesis_depth: 1\n---\n# Doc"
        result = _parse_knowledge_fields(content, "docs")
        assert result["synthesis_depth"] == 1  # explicit wins over source_type default

    def test_belief_time_end_always_sentinel(self):
        content = "---\nprovenance_type: observed\n---\n# Doc"
        assert _parse_knowledge_fields(content, None)["belief_time_end"] == _SENTINEL


# ── db._parse_dsn ────────────────────────────────────────────────────────────

class TestParseDsn:
    def test_basic_dsn(self):
        dsn = "postgresql+asyncpg://user:pass@localhost:5432/mydb"
        result = _parse_dsn(dsn)
        assert result["host"] == "localhost"
        assert result["port"] == 5432
        assert result["user"] == "user"
        assert result["password"] == "pass"
        assert result["database"] == "mydb"

    def test_password_with_special_chars(self):
        """Passwords with +, /, = must be preserved exactly (Zitadel passwords)."""
        dsn = "postgresql+asyncpg://klai:XaaoT+PBq3Bf0k/hkYAUisTU1LAHIrEhEvsodDljMbw=@postgres:5432/klai"
        result = _parse_dsn(dsn)
        assert result["password"] == "XaaoT+PBq3Bf0k/hkYAUisTU1LAHIrEhEvsodDljMbw="
        assert result["host"] == "postgres"
        assert result["database"] == "klai"

    def test_default_port_when_absent(self):
        dsn = "postgresql+asyncpg://user:pass@host/db"
        result = _parse_dsn(dsn)
        assert result["port"] == 5432

    def test_zitadel_org_id_style_host(self):
        dsn = "postgresql+asyncpg://klai:secret@postgres:5432/klai"
        result = _parse_dsn(dsn)
        assert result["host"] == "postgres"
        assert result["user"] == "klai"
