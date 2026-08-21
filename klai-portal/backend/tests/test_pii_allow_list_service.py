"""SPEC-PRIVACY-PII-POLICY-ADMIN-001 PR1 — ``pii_allow_list`` service unit tests.

Direct tests of ``app.services.pii_allow_list.validate_allow_list`` and
``sanitize_stored_entries``, independent of any HTTP/endpoint plumbing —
mirrors ``tests/test_telemetry_level_service.py``'s split between
service-layer tests and endpoint tests.

REQ-9 coverage: a catastrophic-backtracking allow-list regex fails OPEN
(silently lets real PII through), which the SPEC calls out as more
dangerous than a detection pattern failing closed. These tests pin that
``validate_allow_list`` rejects the textbook nested-quantifier shape
without ever executing the pattern against text (no timing-dependent
assertions — the rejection is structural, from the parse tree).
"""

from __future__ import annotations

import pytest

from app.services.pii_allow_list import (
    MAX_ALLOW_LIST_ENTRIES,
    MAX_ALLOW_LIST_VALUE_LENGTH,
    PiiAllowListError,
    sanitize_stored_entries,
    validate_allow_list,
)


class TestValidEntries:
    def test_single_exact_entry_persists(self) -> None:
        result = validate_allow_list([{"value": "Best Solutions", "match": "exact", "note": "our company name"}])
        assert result == [{"value": "Best Solutions", "match": "exact", "note": "our company name"}]

    def test_note_is_optional(self) -> None:
        result = validate_allow_list([{"value": "Best", "match": "exact"}])
        assert result == [{"value": "Best", "match": "exact", "note": None}]

    def test_valid_regex_entry_persists(self) -> None:
        result = validate_allow_list([{"value": r"^TCK-\d{4}$", "match": "regex", "note": "our ticket numbers"}])
        assert result == [{"value": r"^TCK-\d{4}$", "match": "regex", "note": "our ticket numbers"}]

    def test_multiple_entries_all_returned_in_order(self) -> None:
        entries = [
            {"value": "Best", "match": "exact", "note": None},
            {"value": "Ede", "match": "exact", "note": None},
        ]
        result = validate_allow_list(entries)
        assert [e["value"] for e in result] == ["Best", "Ede"]

    def test_empty_list_is_valid(self) -> None:
        assert validate_allow_list([]) == []


class TestEntryCountCap:
    def test_at_the_cap_is_accepted(self) -> None:
        entries = [{"value": f"v{i}", "match": "exact"} for i in range(MAX_ALLOW_LIST_ENTRIES)]
        result = validate_allow_list(entries)
        assert len(result) == MAX_ALLOW_LIST_ENTRIES

    def test_over_the_cap_is_rejected(self) -> None:
        entries = [{"value": f"v{i}", "match": "exact"} for i in range(MAX_ALLOW_LIST_ENTRIES + 1)]
        with pytest.raises(PiiAllowListError, match="too many allow-list entries"):
            validate_allow_list(entries)


class TestValueValidation:
    def test_empty_value_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="non-empty string"):
            validate_allow_list([{"value": "", "match": "exact"}])

    def test_whitespace_only_value_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="non-empty string"):
            validate_allow_list([{"value": "   ", "match": "exact"}])

    def test_non_string_value_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="non-empty string"):
            validate_allow_list([{"value": 123, "match": "exact"}])

    def test_value_at_length_cap_is_accepted(self) -> None:
        value = "a" * MAX_ALLOW_LIST_VALUE_LENGTH
        result = validate_allow_list([{"value": value, "match": "exact"}])
        assert result[0]["value"] == value

    def test_value_over_length_cap_is_rejected(self) -> None:
        value = "a" * (MAX_ALLOW_LIST_VALUE_LENGTH + 1)
        with pytest.raises(PiiAllowListError, match="exceeds"):
            validate_allow_list([{"value": value, "match": "exact"}])


class TestMatchKindValidation:
    def test_unknown_match_kind_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="must be one of"):
            validate_allow_list([{"value": "Best", "match": "fuzzy"}])

    def test_missing_match_kind_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="must be one of"):
            validate_allow_list([{"value": "Best"}])


class TestNoteValidation:
    def test_over_long_note_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="note"):
            validate_allow_list([{"value": "Best", "match": "exact", "note": "x" * 501}])

    def test_non_string_note_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="note"):
            validate_allow_list([{"value": "Best", "match": "exact", "note": 123}])


class TestRegexSafety:
    """REQ-9: allow-list regex is more dangerous than a detection pattern —
    a catastrophic pattern here fails OPEN, silently letting PII through."""

    def test_non_compiling_regex_rejected(self) -> None:
        with pytest.raises(PiiAllowListError, match="does not compile"):
            validate_allow_list([{"value": "(unclosed", "match": "regex"}])

    def test_exact_match_does_not_need_to_compile(self) -> None:
        """A literal value that happens to look like invalid regex syntax is
        fine under 'exact' — the value is never compiled for that mode."""
        result = validate_allow_list([{"value": "(unclosed", "match": "exact"}])
        assert result[0]["value"] == "(unclosed"

    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)+",
            r"(a*)*",
            r"(a+)*",
            r"(a*)+",
            r"(?:a+)+b",
            r"a(b(c+)+)+",
        ],
    )
    def test_nested_quantifier_rejected(self, pattern: str) -> None:
        with pytest.raises(PiiAllowListError, match="nested quantifier"):
            validate_allow_list([{"value": pattern, "match": "regex"}])

    @pytest.mark.parametrize(
        "pattern",
        [
            r"^TCK-\d{4}$",
            r"a+b+",
            r"[A-Z]{2}\d{7}",
            r"(a|b)+",
            r"ab*c",
            r"\d{4}\s?[A-Z]{2}",
        ],
    )
    def test_ordinary_patterns_are_not_flagged(self, pattern: str) -> None:
        """Sibling (non-nested) quantifiers and simple alternation are the
        common, safe shape and must not be rejected by the heuristic."""
        result = validate_allow_list([{"value": pattern, "match": "regex"}])
        assert result[0]["value"] == pattern

    def test_validation_never_executes_the_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The module docstring's central safety claim: validation is a
        parse-tree walk, not a runtime match. Wrap ``re.compile`` so the
        returned object blows up on search/match/fullmatch — anything in
        ``validate_allow_list`` that ran the pattern against text would
        trip this.

        ``re.Pattern`` itself is a C-level immutable type and cannot be
        monkeypatched directly (``TypeError: cannot set 'search' attribute
        of immutable type 're.Pattern'``), hence the wrapper instead of
        patching the class.
        """
        import re

        import app.services.pii_allow_list as pii_allow_list_module

        class _NoExecPattern:
            def __init__(self, real: re.Pattern[str]) -> None:
                self._real = real

            def __getattr__(self, name: str) -> object:
                if name in ("search", "match", "fullmatch", "findall", "finditer"):
                    raise AssertionError("validate_allow_list must not execute the pattern against text")
                return getattr(self._real, name)

        real_compile = re.compile
        monkeypatch.setattr(pii_allow_list_module._re, "compile", lambda pattern: _NoExecPattern(real_compile(pattern)))

        # Must not raise the AssertionError above — the accept path proves
        # validation never executes the regex against text.
        validate_allow_list([{"value": r"^TCK-\d{4}$", "match": "regex"}])


class TestSanitizeStoredEntries:
    def test_none_returns_empty_list(self) -> None:
        assert sanitize_stored_entries(None) == []

    def test_empty_returns_empty_list(self) -> None:
        assert sanitize_stored_entries([]) == []

    def test_well_formed_entries_pass_through(self) -> None:
        stored = [{"value": "Best", "match": "exact", "note": "product name"}]
        assert sanitize_stored_entries(stored) == stored

    def test_malformed_entry_is_dropped_not_raised(self) -> None:
        """Defense-in-depth for a hypothetical direct DB write bypassing
        Python validation — the read path must not 500 on corrupt data."""
        stored = [
            {"value": "Best", "match": "exact"},
            {"value": "bad", "match": "not-a-real-kind"},
            {"match": "exact"},  # missing value
            "not-even-a-dict",
        ]
        result = sanitize_stored_entries(stored)  # type: ignore[arg-type]
        assert result == [{"value": "Best", "match": "exact", "note": None}]

    def test_non_string_note_is_dropped_to_none(self) -> None:
        stored = [{"value": "Best", "match": "exact", "note": 123}]
        result = sanitize_stored_entries(stored)
        assert result == [{"value": "Best", "match": "exact", "note": None}]
