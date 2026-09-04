"""Per-org PII entity policy — SPEC-PRIVACY-MISTRAL-PII-001 REQ-7.

Single source of truth, on the portal-api side, for *which* entity types a
tenant may opt into having masked on the Mistral call path, and for turning a
stored ``portal_orgs.pii_masked_entities`` value into the wire shape the
LiteLLM policy client parses.

REQ-7 splits the entity set in two:

- ``SECRET`` and ``NL_BSN`` are masked for **every** org, unconditionally, and
  are never restored. They are therefore **not settable per org** — a tenant
  cannot turn them on (they already are) and cannot turn them off (forwarding a
  credential to a model provider is an incident regardless of tenant
  preference; a BSN needs a statutory basis, which is a lawfulness question and
  not a checkbox). They are rejected by ``validate_entity_selection`` for that
  reason, not because they are unknown.
- ``PII_RETURN_SET_ENTITIES`` are per-org and restored in the response when
  enabled. ON by default since 2026-09-03 (SPEC-PRIVACY-PII-POLICY-ADMIN-001
  D2) — see ``PII_DEFAULT_MASKED_ENTITIES`` below.

``PERSON`` is deliberately absent from both sets. REQ-9 forbids enabling it
before a GLiNER-era survival re-run exists, and no PERSON detector is deployed
at all today (REQ-2 disables ``SpacyRecognizer``). It is rejected explicitly
rather than falling through the "unknown entity" branch so the error names the
real reason.

**Why this list is duplicated.** ``deploy/litellm/klai_pii_entities.py`` holds
the same two sets for the enforcement side. The two live in different
containers with no shared Python package between them, so the duplication is
structural, not an oversight — and it is deliberately defence in depth: the
LiteLLM client intersects whatever portal-api returns against its *own* copy of
``RETURN_SET_ENTITIES`` (``klai_pii_org_policy.py:145`), so a value that slips
past this module still cannot reach the masker. If REQ-7's set ever changes,
both files change together.
"""

from __future__ import annotations

from collections.abc import Iterable

# REQ-7: per-org, restored when enabled. Default-ON since D2 — the default
# itself is ``PII_DEFAULT_MASKED_ENTITIES`` below. Mirrors
# ``RETURN_SET_ENTITIES`` in ``deploy/litellm/klai_pii_entities.py``.
PII_RETURN_SET_ENTITIES: frozenset[str] = frozenset(
    {
        "IBAN_CODE",
        "CREDIT_CARD",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "NL_KVK",
        "NL_BTW",
        "NL_POSTCODE",
    }
)

# SPEC-PRIVACY-PII-POLICY-ADMIN-001 D2 — the value a tenant gets when nobody
# has chosen: the WHOLE return set, on. This inverts REQ-7's original
# "per-org, default off", deliberately and by owner decision (2026-09-03);
# migration ``d3a91c47f5b2`` sets it as the column DEFAULT and backfills every
# existing row. A tenant admin can still switch any individual type off — the
# default is a starting position, not a floor.
#
# Sorted so the tuple, the migration's array literal and the model's
# ``server_default`` are byte-comparable; ``TestDefaultOnIsSingleSourced``
# fails if any of the three drifts.
PII_DEFAULT_MASKED_ENTITIES: tuple[str, ...] = tuple(sorted(PII_RETURN_SET_ENTITIES))

# REQ-7: unconditional for every org, never restored, never per-org settable.
# Mirrors ``NEVER_RESTORE_ENTITIES`` in ``deploy/litellm/klai_pii_entities.py``.
PII_NEVER_RESTORE_ENTITIES: frozenset[str] = frozenset({"SECRET", "NL_BSN"})

# REQ-9 gate. Named separately so the rejection message can say *why*.
PII_FORBIDDEN_ENTITIES: frozenset[str] = frozenset({"PERSON"})


class PiiEntityPolicyError(ValueError):
    """A requested entity selection is not storable for a tenant."""


def validate_entity_selection(values: Iterable[str]) -> frozenset[str]:
    """Return the validated opt-in set, or raise ``PiiEntityPolicyError``.

    Every future write path (operator tooling, an eventual admin endpoint,
    a fixture) MUST go through this function; the DB CHECK constraint
    ``chk_portal_orgs_pii_masked_entities`` is the backstop that makes the
    same guarantee for a write that bypasses Python entirely.

    Rejects, with a reason naming the requirement:
    - ``PERSON`` — REQ-9, no detector deployed.
    - ``SECRET`` / ``NL_BSN`` — REQ-7, unconditional, not per-org settable.
    - anything not in ``PII_RETURN_SET_ENTITIES`` — unknown entity type.
    """
    requested: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise PiiEntityPolicyError(f"entity type must be a string, got {type(value).__name__}")
        requested.append(value)

    forbidden = sorted({v for v in requested if v in PII_FORBIDDEN_ENTITIES})
    if forbidden:
        raise PiiEntityPolicyError(f"{', '.join(forbidden)} cannot be enabled: no detector is deployed for it (REQ-9)")

    unconditional = sorted({v for v in requested if v in PII_NEVER_RESTORE_ENTITIES})
    if unconditional:
        raise PiiEntityPolicyError(
            f"{', '.join(unconditional)} is masked unconditionally for every org and is not per-org settable (REQ-7)"
        )

    unknown = sorted({v for v in requested if v not in PII_RETURN_SET_ENTITIES})
    if unknown:
        raise PiiEntityPolicyError(f"unknown PII entity type(s): {', '.join(unknown)}")

    return frozenset(requested)


def sanitize_stored_entities(values: Iterable[str] | None) -> list[str]:
    """Read path: the stored value, narrowed to REQ-7's return set, sorted.

    Defensive rather than trusting: the column is ``NOT NULL``, CHECK-constrained
    and defaults to the full return set, so in practice this is an identity
    transform. It still
    intersects, because the alternative is that one bad row — a constraint
    dropped during an incident, a superuser backfill, a future column reuse —
    puts ``PERSON`` or ``SECRET`` on the wire, and the enforcement side treats
    the response as an instruction. Sorted so the response is stable and
    diffable in logs.
    """
    if not values:
        return []
    return sorted({v for v in values if isinstance(v, str) and v in PII_RETURN_SET_ENTITIES})
