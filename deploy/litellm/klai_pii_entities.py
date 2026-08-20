"""Entity classification for SPEC-PRIVACY-MISTRAL-PII-001 Phase 3 (REQ-7).

Pure, network-free, import-safe from anywhere in the PII-enforcement stack.
This is the single source of truth for "which entity types exist, and what
happens to each" — every other Phase 3 module (``klai_pii_text_masking.py``,
``klai_pii_org_policy.py``, ``klai_pii_enforce.py``) imports its
classification from here rather than repeating it, so there is exactly one
place a reviewer needs to check to answer "can X ever be restored".

REQ-7's two-tier policy, encoded structurally rather than by convention:

- ``NEVER_RESTORE_ENTITIES`` (``SECRET``, ``NL_BSN``) are masked for every
  org, unconditionally, and never restored. ``effective_enabled_entities()``
  always includes them regardless of org policy content.
- ``RETURN_SET_ENTITIES`` are per-org, default off, restored when enabled.
- ``PERSON`` is deliberately absent from both sets. REQ-0b's PERSON half is
  unmeasurable today (REQ-2 disables SpacyRecognizer, GLiNER/REQ-9 is not
  deployed), so REQ-9 forbids enabling it before that re-run exists.
  ``effective_enabled_entities()`` intersects any org policy against
  ``RETURN_SET_ENTITIES`` — a policy dict that somehow contained
  ``"PERSON": true`` (operator typo, future portal-api bug, anything) still
  cannot produce PERSON in the enabled set, because PERSON is not a member
  of the set being intersected against. This is what "structurally
  impossible" means in practice: the exclusion does not depend on every
  caller remembering to check a flag, it depends on PERSON not existing in
  the data this function reads from.
"""

from __future__ import annotations

# REQ-7: unconditional, every org, never restored. A credential in a draft
# is an incident; a BSN is not Klai's to hold without a statutory basis.
NEVER_RESTORE_ENTITIES: frozenset[str] = frozenset({"SECRET", "NL_BSN"})

# REQ-7: per-org, default off, restored when enabled. PERSON is NOT a member
# of this set — see module docstring. Do not add it here before REQ-9's
# gate (a GLiNER-era survival re-run showing PERSON >= 95%) is satisfied.
RETURN_SET_ENTITIES: frozenset[str] = frozenset(
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

# Every entity type this pack can ever mask, in either tier. Used by
# klai_pii_text_masking.py to size the streaming chunk-boundary tail
# holdback (REQ-8) — it must cover the longest placeholder from EITHER
# tier, because a never-restore placeholder can still be split across a
# chunk boundary even though it is never substituted back.
ALL_MASKABLE_ENTITIES: frozenset[str] = NEVER_RESTORE_ENTITIES | RETURN_SET_ENTITIES


def effective_enabled_entities(org_policy: frozenset[str] | set[str]) -> frozenset[str]:
    """The entity types that should be masked for one request.

    ``org_policy`` is the set of RETURN_SET entities this org has opted
    into (REQ-7's "per-org, default off"). The result always contains the
    two unconditional entities regardless of what ``org_policy`` says, and
    can never contain PERSON regardless of what ``org_policy`` says — both
    guarantees fall out of set intersection against ``RETURN_SET_ENTITIES``
    rather than needing a separate check.
    """
    return NEVER_RESTORE_ENTITIES | (RETURN_SET_ENTITIES & frozenset(org_policy))
