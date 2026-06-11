"""
Tests for knowledge_ingest.eval.suite_loader.

RED phase: tests fail until suite_loader.py exists.

Coverage:
  - Load a valid YAML file and return a Suite dataclass with queries.
  - Validate required fields — missing 'query' raises SuiteValidationError.
  - Optional fields have safe defaults (expected_chunks defaults to []).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Test 1 — load valid YAML
# ---------------------------------------------------------------------------


def test_load_valid_yaml(tmp_path: Path) -> None:
    """Loading a well-formed suite YAML returns a Suite with the correct queries."""
    yaml_content = textwrap.dedent(
        """\
        suite: _sample
        description: Sample queries for TDD.
        queries:
          - id: sample-q-1
            org_zitadel_id: "111"
            user_zitadel_id: null
            query: "Hoe troubleshoot ik Bubble?"
            reference_answer: >-
              Herstart Bubble, controleer de browserplugin en volg de
              Bubble troubleshoot-stappen.
            expected_topics:
              - bubble
              - browser-plugin
            expected_chunks:
              - "Bubble troubleshoot"
          - id: sample-q-2
            org_zitadel_id: "111"
            user_zitadel_id: null
            query: "Wat is de procedure voor uitportering?"
            expected_topics:
              - uitportering
          - id: sample-q-3
            org_zitadel_id: "111"
            user_zitadel_id: null
            query: "Hoe stel ik een voicemail in?"
            expected_topics:
              - voicemail
        """
    )
    suite_file = tmp_path / "_sample.yaml"
    suite_file.write_text(yaml_content, encoding="utf-8")

    from knowledge_ingest.eval.suite_loader import load_suite

    suite = load_suite(suite_file)

    assert suite.name == "_sample"
    assert len(suite.queries) == 3
    assert suite.queries[0].id == "sample-q-1"
    assert suite.queries[0].query == "Hoe troubleshoot ik Bubble?"
    assert suite.queries[0].reference_answer == (
        "Herstart Bubble, controleer de browserplugin en volg de Bubble troubleshoot-stappen."
    )
    assert "bubble" in suite.queries[0].expected_topics
    assert suite.queries[0].expected_chunks == ["Bubble troubleshoot"]
    assert suite.queries[1].id == "sample-q-2"
    assert suite.queries[2].id == "sample-q-3"


# ---------------------------------------------------------------------------
# Test 2 — validates required fields
# ---------------------------------------------------------------------------


def test_load_validates_required_fields(tmp_path: Path) -> None:
    """A query missing the 'query' field raises SuiteValidationError with the id."""
    yaml_content = textwrap.dedent(
        """\
        suite: bad-suite
        description: Missing query field.
        queries:
          - id: bad-q-1
            org_zitadel_id: "111"
            expected_topics:
              - topic
        """
    )
    suite_file = tmp_path / "bad-suite.yaml"
    suite_file.write_text(yaml_content, encoding="utf-8")

    from knowledge_ingest.eval.suite_loader import SuiteValidationError, load_suite

    with pytest.raises(SuiteValidationError) as exc_info:
        load_suite(suite_file)

    assert "bad-q-1" in str(exc_info.value) or "query" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test 3 — optional fields have safe defaults
# ---------------------------------------------------------------------------


def test_load_handles_optional_fields(tmp_path: Path) -> None:
    """A query without 'expected_chunks' gets an empty list, not None."""
    yaml_content = textwrap.dedent(
        """\
        suite: optional-fields
        description: Test optional field defaults.
        queries:
          - id: opt-q-1
            org_zitadel_id: "222"
            user_zitadel_id: null
            query: "Test query without optional fields"
            expected_topics:
              - test
        """
    )
    suite_file = tmp_path / "optional-fields.yaml"
    suite_file.write_text(yaml_content, encoding="utf-8")

    from knowledge_ingest.eval.suite_loader import load_suite

    suite = load_suite(suite_file)

    assert len(suite.queries) == 1
    q = suite.queries[0]
    # expected_chunks must default to [] not None
    assert q.expected_chunks == []
    assert q.reference_answer is None
    # user_zitadel_id can be None
    assert q.user_zitadel_id is None
    # org_zitadel_id is a required field
    assert q.org_zitadel_id == "222"


def test_load_strict_requires_reference_answer(tmp_path: Path) -> None:
    """Strict scored-suite validation rejects missing reference answers."""
    yaml_content = textwrap.dedent(
        """\
        suite: strict-fields
        description: Test strict reference validation.
        queries:
          - id: strict-q-1
            org_zitadel_id: "222"
            query: "Test query without a reference answer"
            expected_topics:
              - test
        """
    )
    suite_file = tmp_path / "strict-fields.yaml"
    suite_file.write_text(yaml_content, encoding="utf-8")

    from knowledge_ingest.eval.suite_loader import SuiteValidationError, load_suite

    with pytest.raises(SuiteValidationError, match="reference_answer"):
        load_suite(suite_file, require_reference_answer=True)
