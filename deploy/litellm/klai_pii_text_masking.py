"""Pure span-selection, masking, and restore logic (REQ-8).

Network-free. Consumes already-fetched presidio-analyzer ``/analyze``
results (``DetectedSpan``) and produces/consumes plain text. Kept separate
from ``klai_pii_enforce.py`` (the CustomLogger orchestrator, which owns the
HTTP calls) so the substitution algorithm — the part most worth testing
exhaustively — has no async/mocking overhead to get right first.

Overlap-safe substitution
--------------------------
presidio-analyzer runs each registered recognizer independently and returns
every match, including matches that overlap across different entity types —
e.g. a lower-scoring ``PHONE_NUMBER`` match fully inside a higher-scoring
``IBAN_CODE`` match (an IBAN's check-digit-plus-account-number tail can
parse as a phone-shaped digit run). Naive substitution corrupts the text
either way round: replacing the IBAN first invalidates the phone's stored
offsets, and replacing the phone first leaves the IBAN's own span pointing
at text that no longer matches what it was scored against.

``_select_non_overlapping`` resolves this before any substitution happens:
sort candidates by score (desc), then span length (desc), then start offset
(asc) so the strongest, most specific match wins each contested region;
accept a candidate only if it does not overlap any span already accepted.
This handles a fully-contained span (the case above) and a partial overlap
identically — both are "cannot both survive substitution", so both are
resolved by the same one rule rather than two.

Substitution itself then runs in two passes: placeholders are numbered in
left-to-right (start-ascending) order first — REQ-8's own requirement, and
what keeps "two different people in one email" from collapsing into one
token — and only then substituted back-to-front (highest start offset
first), so every earlier, not-yet-processed span's offset stays valid
against the shrinking-or-growing string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from klai_pii_entities import ALL_MASKABLE_ENTITIES

__all__ = [
    "DetectedSpan",
    "MaskResult",
    "mask_text",
    "restore_text",
    "TAIL_LEN",
    "split_safe_tail",
]


@dataclass(frozen=True)
class DetectedSpan:
    """One presidio-analyzer ``/analyze`` result, trimmed to what masking
    needs. ``score`` and ``end - start`` (span length) are the two
    tie-breakers ``_select_non_overlapping`` uses.
    """

    entity_type: str
    start: int
    end: int
    score: float = 0.0

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(
                f"invalid span for {self.entity_type!r}: start={self.start} end={self.end}"
            )


@dataclass(frozen=True)
class MaskResult:
    masked_text: str
    # placeholder -> original substring. RETURN-SET entities only — see
    # module docstring on klai_pii_entities.py: a never-restore entity's
    # placeholder is never written here, which is what makes restoring it
    # structurally impossible rather than merely unconfigured.
    restore_map: dict[str, str]
    # Every entity type actually masked, start-ascending order, including
    # never-restore types. Callers use this to decide whether the
    # verbatim-token system instruction is needed at all (REQ-0b: mandatory
    # whenever masking is active).
    masked_entity_types: tuple[str, ...]


def _select_non_overlapping(
    spans: list[DetectedSpan],
    never_restore_entities: frozenset[str] = frozenset(),
) -> list[DetectedSpan]:
    """Greedy interval selection: highest score wins each contested region.

    Drops any span that OVERLAPS one already accepted, not merely one
    contained in it. Containment alone is not enough: NL_BSN [20:29] score
    1.00 sits inside NL_BTW [18:32] score 0.70, and a JWT span sits inside
    a Bearer span with a HIGHER score, so a containment-only rule would
    accept both and back-to-front substitution would corrupt the text.

    Never-restore entities win exact ties. NL_BSN and NL_KVK can produce a
    byte-identical span with an identical score — an 8-digit KvK number
    that also passes the padded elfproef — and without this the winner
    depends on the analyzer's result ordering. Ties must not decide whether
    a value lands in the restore map, so the entity that is never restored
    takes precedence.

    Returns the accepted spans sorted by ``start`` ascending — the order
    both instance-numbering and substitution need.
    """
    ordered = sorted(
        spans,
        key=lambda s: (
            -s.score,
            -(s.end - s.start),
            s.entity_type not in never_restore_entities,
            s.start,
            s.entity_type,
        ),
    )
    accepted: list[DetectedSpan] = []
    for span in ordered:
        overlaps = any(span.start < a.end and a.start < span.end for a in accepted)
        if overlaps:
            continue
        accepted.append(span)
    return sorted(accepted, key=lambda s: s.start)


def mask_text(
    text: str,
    spans: list[DetectedSpan],
    *,
    enabled_entities: frozenset[str],
    never_restore_entities: frozenset[str],
    instance_counters: dict[str, int],
) -> MaskResult:
    """Mask every enabled, non-overlapping span in ``text``.

    ``instance_counters`` is a plain ``{entity_type: count}`` dict the
    caller owns and mutates in place across every text unit (message
    content, a multi-part content block, a tool-call argument string)
    processed within ONE request — so numbering is shared across the whole
    outbound payload, not reset per message. That is what REQ-8's "two
    different people in one email must not collapse into one token"
    actually depends on when a name appears in more than one message of the
    same call: each occurrence still gets its own instance number and its
    own entry in the restore map, keyed by that number, regardless of
    whether the two occurrences happen to be the same text.
    """
    candidates = [s for s in spans if s.entity_type in enabled_entities]
    selected = _select_non_overlapping(candidates, never_restore_entities)
    if not selected:
        return MaskResult(text, {}, ())

    # Pass 1 — assign placeholders left-to-right (REQ-8 numbering order).
    assignments: list[tuple[DetectedSpan, str, str]] = []
    for span in selected:
        instance_counters[span.entity_type] = instance_counters.get(span.entity_type, 0) + 1
        n = instance_counters[span.entity_type]
        placeholder = f"<{span.entity_type}_{n}>"
        original = text[span.start : span.end]
        assignments.append((span, placeholder, original))

    # Pass 2 — substitute back-to-front so earlier offsets stay valid.
    result = text
    restore_map: dict[str, str] = {}
    for span, placeholder, original in reversed(assignments):
        result = result[: span.start] + placeholder + result[span.end :]
        if span.entity_type not in never_restore_entities:
            restore_map[placeholder] = original

    masked_types = tuple(span.entity_type for span, _, _ in assignments)
    return MaskResult(result, restore_map, masked_types)


# ---------------------------------------------------------------------------
# Restore (REQ-8) — literal, case-sensitive placeholder substitution
# ---------------------------------------------------------------------------
# Entity type names never contain digits, so greedy backtracking on
# `[A-Z0-9_]*` correctly finds the LAST `_<digits>>` suffix as the instance
# number regardless of how many underscores the type name itself has
# (NL_BSN, IBAN_CODE, PHONE_NUMBER, ...).
_PLACEHOLDER_RE = re.compile(r"<([A-Z][A-Z0-9_]*)_(\d+)>")


def restore_text(
    text: str, restore_map: dict[str, str], *, never_restore_entities: frozenset[str]
) -> str:
    """Replace every placeholder in ``text`` with its original value.

    Defense in depth for REQ-8's "never-return set SHALL NOT be restored
    under any configuration": even though a never-restore entity's
    placeholder is never written into ``restore_map`` in the first place
    (see ``mask_text``), this function ALSO refuses to substitute any
    placeholder whose entity-type prefix names a never-restore entity, even
    if one somehow ended up in ``restore_map`` (a future bug in a caller,
    a hand-built test fixture, anything). Two independent reasons a
    credential or a BSN cannot come back, not one.
    """

    def _sub(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        entity_type = match.group(1)
        if entity_type in never_restore_entities:
            return placeholder
        return restore_map.get(placeholder, placeholder)

    return _PLACEHOLDER_RE.sub(_sub, text)


def find_restorable_placeholders(text: str, restore_map: dict[str, str]) -> set[str]:
    """Which placeholders in ``text`` this ``restore_map`` can put back.

    Telemetry only — no caller mutates anything based on this. Deliberately a
    separate function rather than a second return value on ``restore_text``:
    that one carries REQ-8's never-restore guarantee and has a dozen call
    sites pinning its exact shape, and a counter is not worth reopening it.

    Call it on the text BEFORE restoring. The result is then exactly the set
    of masked values the model returned verbatim, which is REQ-0b's survival
    rate measured continuously instead of once in a Phase 0 harness. A
    placeholder the model mangled (changed case, inserted a space, dropped
    the angle brackets) does not match and therefore did NOT survive — which
    is correct: it will not be restored either, and the user sees the damage.

    A **set**, not a count, because a model that mentions the same placeholder
    twice would otherwise push the caller's tally above the number of values
    that were masked, and "unrestored" would go negative. Survival is a
    property of a value, not of how often the answer names it.

    Placeholders absent from ``restore_map`` are not included. That excludes
    the never-restore set by construction — ``mask_text`` never writes those
    into the map — so this never reports a credential or a BSN as "restored".
    """
    return {
        match.group(0) for match in _PLACEHOLDER_RE.finditer(text) if match.group(0) in restore_map
    }


# Case-INSENSITIVE, and tolerant of the spacing a model adds when it retypes a
# token instead of copying it. Deliberately looser than ``_PLACEHOLDER_RE``:
# that one decides what gets substituted and must be exact, this one decides
# what gets counted as damage and should catch the near-misses.
_LEAKED_PLACEHOLDER_RE = re.compile(r"<\s*([A-Za-z][A-Za-z0-9_]*)_(\d+)\s*>")


def count_leaked_placeholders(text: str, *, never_restore_entities: frozenset[str]) -> int:
    """Placeholder-shaped tokens still visible in ``text`` AFTER restoring.

    Call it on the text the user will actually see. Anything it finds is
    damage: a value that was masked on the way out and did not come back, so
    the reader gets ``<IBAN_CODE_1>`` where a bank account belonged.

    ``never_restore_entities`` are excluded because ``<SECRET_1>`` and
    ``<NL_BSN_1>`` are SUPPOSED to remain — that is REQ-8's never-return set
    working, not a failure.

    This exists because the obvious metric is wrong. ``masked - survived``
    counts every masked value the model did not echo, and in ordinary chat a
    model answers about one thing and never mentions the other five — so that
    number is dominated by values whose absence harms nobody. REQ-0b's 95.8%
    came from a harness that explicitly asked the model to repeat every token;
    production has no such instruction, so the same subtraction does not mean
    the same thing here. What is unambiguous is a placeholder the user can
    see, and that is what this counts.

    Known false-positive class, accepted: prose or code containing a literal
    ``<word_123>`` is counted. It is rare in answer text, and the failure
    direction is a slightly over-reported damage counter rather than a silent
    one.
    """
    return sum(
        1
        for match in _LEAKED_PLACEHOLDER_RE.finditer(text)
        if match.group(1).upper() not in never_restore_entities
    )


# ---------------------------------------------------------------------------
# Streaming chunk-boundary safety (REQ-8's own addendum to REQ-8)
# ---------------------------------------------------------------------------
# "<" + longest possible entity type name + "_" + a generous instance-count
# digit allowance + ">". Computed from klai_pii_entities.ALL_MASKABLE_ENTITIES
# rather than hardcoded so a future entity addition cannot silently shrink
# the safety margin below what it needs to be.
_MAX_ENTITY_NAME_LEN = max(len(e) for e in ALL_MASKABLE_ENTITIES)
_MAX_INSTANCE_DIGITS = 4  # up to 9999 instances of one entity type per request
TAIL_LEN = 1 + _MAX_ENTITY_NAME_LEN + 1 + _MAX_INSTANCE_DIGITS + 1


def split_safe_tail(
    buffer: str, restore_map: dict[str, str], never_restore_entities: frozenset[str]
) -> tuple[str, str]:
    """Split a growing stream buffer into (safe-to-emit, still-held-back).

    Any COMPLETE placeholder anywhere in ``buffer`` is restored immediately
    (``restore_text`` is a single regex pass over the whole buffer, not
    just the tail). What remains held back is always at least ``TAIL_LEN``
    characters — REQ-8: "hold back a tail of at least the longest possible
    placeholder length when matching across streamed chunks" — so a
    placeholder split as ``<PERS`` + ``ON_1>`` across a chunk boundary is
    never emitted as a visible, unrestored fragment: the first chunk's
    ``<PERS`` never leaves the held-back tail, and once ``ON_1>`` arrives
    the combined buffer contains the complete placeholder, which the next
    ``restore_text`` call resolves before any of it is released.

    At true stream end, callers do NOT call this function — they call
    ``restore_text`` directly on the full remaining buffer and flush
    everything, because there is no more input that could complete a
    still-forming placeholder.
    """
    if not buffer:
        return "", buffer
    restored = restore_text(buffer, restore_map, never_restore_entities=never_restore_entities)
    if len(restored) <= TAIL_LEN:
        return "", restored
    cut = len(restored) - TAIL_LEN
    return restored[:cut], restored[cut:]
