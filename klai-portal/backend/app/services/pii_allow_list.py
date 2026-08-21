"""Per-tenant PII allow-list validation — SPEC-PRIVACY-PII-POLICY-ADMIN-001 D1/REQ-9.

D1's subtractive model: a tenant does not compose a policy from scratch —
it starts from the platform default and excludes specific values, patterns
or keywords. Storage is ``portal_orgs.pii_allow_list`` (jsonb), a list of
``{"value": str, "match": "exact" | "regex", "note": str | None}``.

REQ-9 explains why an allow-list regex needs the *same* safety envelope as
a tenant-defined detection pattern, and is if anything more dangerous: a
catastrophic **detection** pattern fails closed (the analyzer times out,
the text stays masked); a catastrophic **allow-list** pattern fails open
(real PII silently reaches the model provider, because the exclusion
"matched" everything or hung the analyzer). ``validate_allow_list`` is the
write-time gate — every future write path (this PR's tenant endpoint, a
later platform endpoint, a fixture, operator tooling) MUST call it, the
same contract ``pii_entity_policy.validate_entity_selection`` documents for
the entity-type set.

**Scope boundary (explicit).** This module only stores and validates. It
does **not** wire ``allow_list`` into Presidio's
``AnalyzerEngine.analyze(allow_list=..., allow_list_match=...)`` —
that enforcement-side plumbing is out of scope for SPEC PR1 (this PR) and
lands with the platform-default/resolution work (REQ-3's resolution order,
step 4). A stored entry has no runtime effect yet.

**What "validate" means here, precisely — and its limit.** Every ``regex``
entry MUST compile (``re.compile``) and MUST NOT contain a nested-quantifier
shape, the single most common cause of catastrophic backtracking
(``(a+)+``, ``(a*)*``, ``(a+)*``, ...). Detection walks Python's own regex
parse tree (the ``re._parser`` private module) rather than running a
timed self-test: this function deliberately never executes a
user-supplied pattern against text during validation, so there is no
"did the timeout actually fire" race to get wrong — REQ-9's ban on
un-timed execution of untrusted regex is satisfied by not executing at
all. This is a static heuristic, not a ReDoS proof: it catches the
textbook nested-quantifier shapes, not every catastrophic pattern a
sufficiently adversarial author could construct (e.g. exponential
alternation across sibling groups without nesting). The enforcement-side
RE2/linear-time engine REQ-9 also requires is the real backstop once
wired — this module's job is to keep the obviously bad shapes out of
storage, not to certify safety.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

try:
    from re import _parser as _sre_parser  # type: ignore[attr-defined]  # private API — see module docstring
except ImportError:  # pragma: no cover - defensive; not expected on supported Pythons
    _sre_parser = None  # type: ignore[assignment]

import re as _re

MAX_ALLOW_LIST_ENTRIES = 50
MAX_ALLOW_LIST_VALUE_LENGTH = 200
MAX_ALLOW_LIST_NOTE_LENGTH = 500

_VALID_MATCH_KINDS = frozenset({"exact", "regex"})
_REPEAT_OPCODES = frozenset({"MAX_REPEAT", "MIN_REPEAT"})
_WRAPPED_SUBPATTERN_OPCODES = frozenset({"ASSERT", "ASSERT_NOT"})


class PiiAllowListError(ValueError):
    """A requested allow-list entry is not storable for a tenant."""


def _contains_repeat_node(subpattern: Any) -> bool:
    """True if a quantified node appears anywhere inside ``subpattern``."""
    for op, av in subpattern:
        opname = op.name
        if opname in _REPEAT_OPCODES:
            return True
        if opname == "SUBPATTERN":
            _, _, _, sub = av
            if _contains_repeat_node(sub):
                return True
        elif opname == "BRANCH":
            _, branches = av
            if any(_contains_repeat_node(branch) for branch in branches):
                return True
        elif opname in _WRAPPED_SUBPATTERN_OPCODES:
            _, sub = av
            if _contains_repeat_node(sub):
                return True
    return False


def _contains_branch_node(subpattern: Any) -> bool:
    """True if an alternation appears anywhere inside ``subpattern``.

    A quantifier over an alternation whose arms can match the same input --
    ``(a|aa)+``, ``(a|b|ab)*`` -- backtracks exponentially without ever
    nesting one quantifier inside another, so ``_has_nested_quantifier``
    alone reports it safe. Deciding whether the arms actually overlap is
    the hard part; we do not try. An allow-list entry is a customer term or
    identifier, where alternation-under-repetition has no legitimate use,
    so rejecting the shape outright costs nothing we want.
    """
    for op, av in subpattern:
        opname = op.name
        if opname == "BRANCH":
            return True
        if opname == "SUBPATTERN":
            _, _, _, sub = av
            if _contains_branch_node(sub):
                return True
        elif opname in _REPEAT_OPCODES:
            _, _, sub = av
            if _contains_branch_node(sub):
                return True
        elif opname in _WRAPPED_SUBPATTERN_OPCODES:
            _, sub = av
            if _contains_branch_node(sub):
                return True
    return False


def _has_nested_quantifier(subpattern: Any) -> bool:
    """True if any repeat node's body itself contains another repeat node.

    Walks the whole tree, not just the top level, so ``a(b(c+)+)+`` is
    caught even though the outermost node is not itself the offending one.
    """
    for op, av in subpattern:
        opname = op.name
        if opname in _REPEAT_OPCODES:
            _, _, sub = av
            if (
                _contains_repeat_node(sub)
                or _contains_branch_node(sub)
                or _has_nested_quantifier(sub)
            ):
                return True
        elif opname == "SUBPATTERN":
            _, _, _, sub = av
            if _has_nested_quantifier(sub):
                return True
        elif opname == "BRANCH":
            _, branches = av
            if any(_has_nested_quantifier(branch) for branch in branches):
                return True
        elif opname in _WRAPPED_SUBPATTERN_OPCODES:
            _, sub = av
            if _has_nested_quantifier(sub):
                return True
    return False


def _validate_regex_pattern(pattern: str) -> None:
    """Reject a non-compiling pattern or a nested-quantifier ReDoS shape.

    Never executes ``pattern`` against text — see module docstring.
    """
    try:
        _re.compile(pattern)
    except _re.error as exc:
        raise PiiAllowListError(f"pattern does not compile: {exc}") from exc

    if _sre_parser is None:  # pragma: no cover - defensive
        # Fail closed: REQ-9 treats an allow-list regex as the more
        # dangerous direction, so an unverifiable pattern is rejected
        # rather than trusted.
        raise PiiAllowListError("could not verify pattern safety: regex parser unavailable")

    try:
        parsed = _sre_parser.parse(pattern)
        unsafe = _has_nested_quantifier(parsed)
    except Exception as exc:
        raise PiiAllowListError("could not verify pattern safety") from exc

    if unsafe:
        raise PiiAllowListError(
            "pattern rejected: quantifier over a quantifier or over an alternation "
            "(e.g. '(a+)+', '(a|aa)+') risks catastrophic backtracking"
        )


def validate_allow_list(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the validated allow-list, or raise ``PiiAllowListError``.

    Every future write path MUST go through this function — mirrors the
    contract ``pii_entity_policy.validate_entity_selection`` documents for
    the entity-type set. Enforces, in order:

    - **Entry count cap** (``MAX_ALLOW_LIST_ENTRIES``) — checked before any
      per-entry work, so an oversized payload is rejected cheaply.
    - **``value``**: non-empty string, at most ``MAX_ALLOW_LIST_VALUE_LENGTH``
      characters. An empty value is rejected outright — an empty exact
      match is meaningless and an empty regex is the worst-case "matches
      everything" shape REQ-9 warns about.
    - **``match``**: must be ``"exact"`` or ``"regex"``.
    - **``note``**: optional string, at most ``MAX_ALLOW_LIST_NOTE_LENGTH``
      characters.
    - **``regex`` entries only**: must compile and must not contain a
      nested-quantifier shape (see ``_validate_regex_pattern``).

    Returns a list of plain dicts (JSON-serialisable, ready for the
    ``pii_allow_list`` JSONB column) rather than echoing the input objects,
    so callers get a normalised shape regardless of what container type
    (pydantic model, plain dict) they passed in.
    """
    materialized = list(entries)

    if len(materialized) > MAX_ALLOW_LIST_ENTRIES:
        raise PiiAllowListError(f"too many allow-list entries: {len(materialized)} > max {MAX_ALLOW_LIST_ENTRIES}")

    validated: list[dict[str, Any]] = []
    for raw in materialized:
        value = raw.get("value")
        match = raw.get("match")
        note = raw.get("note")

        if not isinstance(value, str) or not value.strip():
            raise PiiAllowListError("allow-list entry 'value' must be a non-empty string")
        if len(value) > MAX_ALLOW_LIST_VALUE_LENGTH:
            raise PiiAllowListError(
                f"allow-list entry 'value' exceeds {MAX_ALLOW_LIST_VALUE_LENGTH} characters: {value[:40]!r}..."
            )
        if match not in _VALID_MATCH_KINDS:
            raise PiiAllowListError(
                f"allow-list entry 'match' must be one of {sorted(_VALID_MATCH_KINDS)}, got {match!r}"
            )
        if note is not None and (not isinstance(note, str) or len(note) > MAX_ALLOW_LIST_NOTE_LENGTH):
            raise PiiAllowListError(
                f"allow-list entry 'note' must be a string of at most {MAX_ALLOW_LIST_NOTE_LENGTH} characters"
            )

        if match == "regex":
            _validate_regex_pattern(value)

        validated.append({"value": value, "match": match, "note": note})

    return validated


def sanitize_stored_entries(values: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Read path: the stored allow-list, narrowed to a shape the response
    model can render, dropping anything malformed rather than 500ing.

    Mirrors ``pii_entity_policy.sanitize_stored_entities``'s defensive
    posture: this write path already runs every entry through
    ``validate_allow_list`` before it reaches the column, so in practice
    this is close to an identity transform. It stays defensive because the
    column is JSONB (no per-element DB CHECK, deliberately — see the
    migration docstring), so a future direct write bypassing Python is the
    one scenario this guards against; a malformed element there should
    disappear from the read response, not crash it.
    """
    if not values:
        return []
    out: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        value = raw.get("value")
        match = raw.get("match")
        note = raw.get("note")
        if not isinstance(value, str) or match not in _VALID_MATCH_KINDS:
            continue
        if note is not None and not isinstance(note, str):
            note = None
        out.append({"value": value, "match": match, "note": note})
    return out
