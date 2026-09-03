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
    PII_DEFAULT_MASKED_ENTITIES,
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


class TestDefaultOnIsSingleSourced:
    """SPEC-PRIVACY-PII-POLICY-ADMIN-001 D2 — default-on, stated three times.

    ``PII_DEFAULT_MASKED_ENTITIES`` is the source; migration ``d3a91c47f5b2``
    and ``PortalOrg.pii_masked_entities.server_default`` are SQL literals that
    cannot import it. Nothing stops the three from drifting except this class,
    and a drift is silent in exactly the worst direction: a tenant created
    after the drift gets a different default than a tenant backfilled before
    it, and neither the UI nor the enforcement path can tell.
    """

    def test_default_is_the_whole_return_set(self):
        assert set(PII_DEFAULT_MASKED_ENTITIES) == set(PII_RETURN_SET_ENTITIES)
        assert list(PII_DEFAULT_MASKED_ENTITIES) == sorted(PII_DEFAULT_MASKED_ENTITIES)

    def test_default_excludes_the_unconditional_and_forbidden_entities(self):
        """The column stores per-org state only — SECRET/NL_BSN/PERSON never enter it."""
        assert not set(PII_DEFAULT_MASKED_ENTITIES) & set(PII_NEVER_RESTORE_ENTITIES)
        assert "PERSON" not in PII_DEFAULT_MASKED_ENTITIES

    def test_migration_default_literal_matches_the_constant(self):
        migration = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "d3a91c47f5b2_pii_masked_entities_default_on.py"
        )
        source = migration.read_text(encoding="utf-8")
        block = source.split("_DEFAULT_ENTITIES_SQL = (", 1)[1].split(")", 1)[0]
        in_migration = re.findall(r"'([A-Z_]+)'", block)
        assert in_migration == list(PII_DEFAULT_MASKED_ENTITIES)

    def test_model_server_default_matches_the_constant(self):
        from app.models.portal import PortalOrg

        server_default = PortalOrg.__table__.c.pii_masked_entities.server_default
        assert server_default is not None
        in_server_default = re.findall(r"'([A-Z_]+)'", str(server_default.arg))
        assert in_server_default == list(PII_DEFAULT_MASKED_ENTITIES)

    def test_orm_default_gives_a_new_org_the_whole_set(self):
        """The three ``PortalOrg(...)`` call sites omit the field, so this is
        what a tenant created through signup or the platform console gets."""
        from app.models.portal import PortalOrg

        column_default = PortalOrg.__table__.c.pii_masked_entities.default
        assert column_default is not None and column_default.is_callable
        assert column_default.arg(None) == list(PII_DEFAULT_MASKED_ENTITIES)
