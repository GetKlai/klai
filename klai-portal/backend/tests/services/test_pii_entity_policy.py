"""SPEC-PRIVACY-MISTRAL-PII-001 REQ-7/REQ-9 — per-org PII entity policy domain.

These pin the server-side validation contract, not documentation:
``validate_entity_selection`` is what every write path must call, and the DB
CHECK constraint ``chk_portal_orgs_pii_masked_entities`` (migration
5d8cef52b18c) makes the identical guarantee for a write that bypasses Python.
The two must agree — if a value is added to one it must be added to the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.pii_entity_policy import (
    PII_NEVER_RESTORE_ENTITIES,
    PII_RETURN_SET_ENTITIES,
    PiiEntityPolicyError,
    sanitize_stored_entities,
    validate_entity_selection,
)


class TestValidateEntitySelection:
    def test_empty_selection_is_valid(self):
        """REQ-7's default: opting into nothing is a legal state, not an error."""
        assert validate_entity_selection([]) == frozenset()

    def test_full_return_set_is_valid(self):
        assert validate_entity_selection(sorted(PII_RETURN_SET_ENTITIES)) == PII_RETURN_SET_ENTITIES

    @pytest.mark.parametrize("entity", sorted(PII_RETURN_SET_ENTITIES))
    def test_each_return_set_entity_is_settable(self, entity):
        assert validate_entity_selection([entity]) == frozenset({entity})

    def test_person_is_rejected(self):
        """REQ-9: no PERSON detector is deployed, so it cannot be opted into."""
        with pytest.raises(PiiEntityPolicyError) as exc:
            validate_entity_selection(["PERSON"])
        assert "PERSON" in str(exc.value)
        assert "REQ-9" in str(exc.value)

    def test_person_is_rejected_even_alongside_valid_entities(self):
        with pytest.raises(PiiEntityPolicyError):
            validate_entity_selection(["IBAN_CODE", "PERSON"])

    @pytest.mark.parametrize("entity", sorted(PII_NEVER_RESTORE_ENTITIES))
    def test_unconditional_entities_are_not_settable(self, entity):
        """REQ-7: SECRET / NL_BSN are masked for every org and are not per-org state."""
        with pytest.raises(PiiEntityPolicyError) as exc:
            validate_entity_selection([entity])
        assert entity in str(exc.value)
        assert "REQ-7" in str(exc.value)

    @pytest.mark.parametrize("entity", ["", "iban_code", "US_SSN", "NL_IBAN", "DROP TABLE"])
    def test_unknown_entity_types_are_rejected(self, entity):
        with pytest.raises(PiiEntityPolicyError) as exc:
            validate_entity_selection([entity])
        assert "unknown" in str(exc.value)

    def test_non_string_is_rejected(self):
        with pytest.raises(PiiEntityPolicyError):
            validate_entity_selection([None])  # type: ignore[list-item]

    def test_person_is_absent_from_both_sets(self):
        """Structural, not conditional — PERSON is not a member of anything settable."""
        assert "PERSON" not in PII_RETURN_SET_ENTITIES
        assert "PERSON" not in PII_NEVER_RESTORE_ENTITIES


class TestSanitizeStoredEntities:
    def test_empty_and_none_are_empty(self):
        assert sanitize_stored_entities([]) == []
        assert sanitize_stored_entities(None) == []

    def test_return_set_survives_and_is_sorted(self):
        assert sanitize_stored_entities(["NL_KVK", "IBAN_CODE"]) == ["IBAN_CODE", "NL_KVK"]

    @pytest.mark.parametrize("entity", ["PERSON", "SECRET", "NL_BSN", "US_SSN"])
    def test_non_return_set_values_never_reach_the_wire(self, entity):
        """One bad row must not become an instruction to the enforcement side."""
        assert sanitize_stored_entities([entity, "IBAN_CODE"]) == ["IBAN_CODE"]

    def test_duplicates_collapse(self):
        assert sanitize_stored_entities(["IBAN_CODE", "IBAN_CODE"]) == ["IBAN_CODE"]


class TestConstraintMatchesPython:
    """The DB CHECK and the Python validator must allow exactly the same set.

    A live-PostgreSQL assertion would need the ``postgres`` marker (excluded
    from the default run), so this reads the migration source instead. It is a
    weaker check than executing the DDL, but it is the one that fails in the
    normal test suite when someone adds an entity to one side only.
    """

    def test_migration_check_lists_exactly_the_return_set(self):
        migration = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "5d8cef52b18c_spec_privacy_mistral_pii_001_req_7_.py"
        )
        source = migration.read_text(encoding="utf-8")
        block = source.split("pii_masked_entities <@ ARRAY[", 1)[1].split("]::text[]", 1)[0]
        in_constraint = set(re.findall(r"'([A-Z_]+)'", block))
        assert in_constraint == set(PII_RETURN_SET_ENTITIES)
