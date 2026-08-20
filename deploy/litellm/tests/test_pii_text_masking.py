"""Tests for klai_pii_text_masking.py (SPEC-PRIVACY-MISTRAL-PII-001 REQ-8)."""

from __future__ import annotations

from klai_pii_entities import NEVER_RESTORE_ENTITIES, RETURN_SET_ENTITIES
from klai_pii_text_masking import (
    TAIL_LEN,
    DetectedSpan,
    mask_text,
    restore_text,
    split_safe_tail,
)

ALL_ENTITIES = NEVER_RESTORE_ENTITIES | RETURN_SET_ENTITIES


# ---------------------------------------------------------------------------
# Basic masking + restore round trip
# ---------------------------------------------------------------------------
def test_single_entity_masked_and_restored():
    text = "Bel mij op 06-12345678 alstublieft."
    phone = "06-12345678"
    span = DetectedSpan(
        entity_type="PHONE_NUMBER",
        start=text.index(phone),
        end=text.index(phone) + len(phone),
        score=0.9,
    )
    result = mask_text(
        text,
        [span],
        enabled_entities=ALL_ENTITIES,
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_text == "Bel mij op <PHONE_NUMBER_1> alstublieft."
    assert result.restore_map == {"<PHONE_NUMBER_1>": "06-12345678"}
    restored = restore_text(
        result.masked_text, result.restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES
    )
    assert restored == text


def test_disabled_entity_type_is_left_untouched():
    text = "IBAN NL91ABNA0417164300 hier."
    span = DetectedSpan(entity_type="IBAN_CODE", start=5, end=23, score=1.0)
    result = mask_text(
        text,
        [span],
        enabled_entities=frozenset(),  # nothing enabled
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_text == text
    assert result.restore_map == {}
    assert result.masked_entity_types == ()


# ---------------------------------------------------------------------------
# Never-restore set: structurally impossible to restore
# ---------------------------------------------------------------------------
def test_secret_is_masked_but_never_enters_restore_map():
    text = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz gebruiken"
    span = DetectedSpan(entity_type="SECRET", start=0, end=40, score=0.95)
    result = mask_text(
        text,
        [span],
        enabled_entities=ALL_ENTITIES,
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert "<SECRET_1>" in result.masked_text
    assert result.restore_map == {}  # structurally nothing to restore


def test_bsn_is_masked_but_never_enters_restore_map():
    text = "mijn bsn is 111222333 graag verwerken"
    span = DetectedSpan(entity_type="NL_BSN", start=12, end=21, score=0.85)
    result = mask_text(
        text,
        [span],
        enabled_entities=ALL_ENTITIES,
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_text == "mijn bsn is <NL_BSN_1> graag verwerken"
    assert result.restore_map == {}


def test_restore_text_refuses_a_never_restore_placeholder_even_if_in_the_map():
    """Defense in depth: even a hand-built restore_map containing a
    never-restore placeholder (a bug elsewhere, a crafted fixture) must not
    be honoured by restore_text itself. Two independent guarantees, not one.
    """
    masked = "mijn bsn is <NL_BSN_1> graag verwerken"
    tampered_map = {"<NL_BSN_1>": "111222333"}  # should never happen, but...
    restored = restore_text(masked, tampered_map, never_restore_entities=NEVER_RESTORE_ENTITIES)
    assert "111222333" not in restored
    assert "<NL_BSN_1>" in restored


def test_mixed_masking_only_return_set_restores():
    text = "sk-ant-api03-zzzzzzzzzzzzzzzzzzzz en bel 06-12345678"
    spans = [
        DetectedSpan(entity_type="SECRET", start=0, end=32, score=0.95),
        DetectedSpan(entity_type="PHONE_NUMBER", start=41, end=53, score=0.9),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=ALL_ENTITIES,
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert list(result.restore_map.keys()) == ["<PHONE_NUMBER_1>"]
    restored = restore_text(
        result.masked_text, result.restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES
    )
    assert "06-12345678" in restored
    assert "sk-ant-api03" not in restored
    assert "<SECRET_1>" in restored


# ---------------------------------------------------------------------------
# REQ-8 numbering: two different people must not collapse into one token
# ---------------------------------------------------------------------------
def test_two_different_people_get_distinct_placeholders_and_restore_correctly():
    text = "Van Jan de Vries naar Marieke Bakker doorsturen."
    name_a, name_b = "Jan de Vries", "Marieke Bakker"
    spans = [
        DetectedSpan(
            entity_type="PERSON",
            start=text.index(name_a),
            end=text.index(name_a) + len(name_a),
            score=0.9,
        ),
        DetectedSpan(
            entity_type="PERSON",
            start=text.index(name_b),
            end=text.index(name_b) + len(name_b),
            score=0.9,
        ),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"PERSON"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_text == "Van <PERSON_1> naar <PERSON_2> doorsturen."
    assert result.restore_map == {
        "<PERSON_1>": "Jan de Vries",
        "<PERSON_2>": "Marieke Bakker",
    }
    restored = restore_text(
        result.masked_text, result.restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES
    )
    assert restored == text
    # Neither placeholder restores to the other's value.
    assert result.restore_map["<PERSON_1>"] != result.restore_map["<PERSON_2>"]


def test_instance_counters_shared_across_multiple_mask_text_calls():
    """Same counters dict passed across two "messages" in one request keeps
    numbering unique across the whole outbound payload, not just one text
    unit."""
    counters: dict[str, int] = {}
    text_a = "Stuur naar Jan de Vries."
    name_a = "Jan de Vries"
    text_b = "Ook Marieke Bakker toevoegen."
    name_b = "Marieke Bakker"
    first = mask_text(
        text_a,
        [
            DetectedSpan(
                entity_type="PERSON",
                start=text_a.index(name_a),
                end=text_a.index(name_a) + len(name_a),
                score=0.9,
            )
        ],
        enabled_entities=frozenset({"PERSON"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters=counters,
    )
    second = mask_text(
        text_b,
        [
            DetectedSpan(
                entity_type="PERSON",
                start=text_b.index(name_b),
                end=text_b.index(name_b) + len(name_b),
                score=0.9,
            )
        ],
        enabled_entities=frozenset({"PERSON"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters=counters,
    )
    assert "<PERSON_1>" in first.masked_text
    assert "<PERSON_2>" in second.masked_text
    combined = {**first.restore_map, **second.restore_map}
    assert combined == {"<PERSON_1>": "Jan de Vries", "<PERSON_2>": "Marieke Bakker"}


# ---------------------------------------------------------------------------
# Overlapping spans across entity types (REQ-8's own regression case):
# an IBAN span fully containing a lower-scoring PHONE_NUMBER span. Exact
# text and offsets from spec.md REQ-8's measured example:
#
#   Betaal op IBAN NL91 ABNA 0417 1643 00 graag.
#   IBAN_CODE    [15:37] score=1.00
#   PHONE_NUMBER [25:37] score=0.40   <- fully inside the IBAN span
# ---------------------------------------------------------------------------
def test_overlapping_spans_iban_contains_phone_number():
    text = "Betaal op IBAN NL91 ABNA 0417 1643 00 graag."
    assert text[15:37] == "NL91 ABNA 0417 1643 00"
    assert text[25:37] == "0417 1643 00"

    spans = [
        DetectedSpan(entity_type="IBAN_CODE", start=15, end=37, score=1.00),
        DetectedSpan(entity_type="PHONE_NUMBER", start=25, end=37, score=0.40),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"IBAN_CODE", "PHONE_NUMBER"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_text.count("<IBAN_CODE_1>") == 1
    assert "<PHONE_NUMBER_1>" not in result.masked_text
    assert "PHONE_NUMBER" not in "".join(result.masked_entity_types)
    # Surrounding text intact.
    assert result.masked_text.startswith("Betaal op IBAN ")
    assert result.masked_text.endswith(" graag.")
    # Restores cleanly to the original.
    restored = restore_text(
        result.masked_text, result.restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES
    )
    assert restored == text


def test_overlap_resolution_prefers_higher_score():
    text = "AAAAAAAAAA"
    spans = [
        DetectedSpan(entity_type="EMAIL_ADDRESS", start=0, end=10, score=0.5),
        DetectedSpan(entity_type="PHONE_NUMBER", start=2, end=8, score=0.9),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"EMAIL_ADDRESS", "PHONE_NUMBER"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_entity_types == ("PHONE_NUMBER",)


def test_overlap_resolution_prefers_longer_span_on_score_tie():
    text = "AAAAAAAAAA"
    spans = [
        DetectedSpan(entity_type="EMAIL_ADDRESS", start=0, end=10, score=0.8),
        DetectedSpan(entity_type="PHONE_NUMBER", start=2, end=8, score=0.8),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"EMAIL_ADDRESS", "PHONE_NUMBER"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_entity_types == ("EMAIL_ADDRESS",)  # the longer span (10 > 6)


def test_partial_overlap_not_just_full_containment_is_resolved():
    text = "0123456789"
    spans = [
        DetectedSpan(entity_type="EMAIL_ADDRESS", start=0, end=6, score=0.9),
        DetectedSpan(entity_type="PHONE_NUMBER", start=4, end=10, score=0.5),  # partial overlap
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"EMAIL_ADDRESS", "PHONE_NUMBER"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_entity_types == ("EMAIL_ADDRESS",)
    assert "<PHONE_NUMBER_1>" not in result.masked_text


def test_non_overlapping_spans_are_both_kept():
    text = "bel 06-12345678 of mail jan@example.nl"
    spans = [
        DetectedSpan(entity_type="PHONE_NUMBER", start=4, end=15, score=0.9),
        DetectedSpan(entity_type="EMAIL_ADDRESS", start=25, end=39, score=0.9),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"PHONE_NUMBER", "EMAIL_ADDRESS"}),
        never_restore_entities=NEVER_RESTORE_ENTITIES,
        instance_counters={},
    )
    assert result.masked_entity_types == ("PHONE_NUMBER", "EMAIL_ADDRESS")
    restored = restore_text(
        result.masked_text, result.restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES
    )
    assert restored == text


# ---------------------------------------------------------------------------
# REQ-8 chunk-boundary safety: a placeholder split across a streamed chunk
# ---------------------------------------------------------------------------
def test_placeholder_split_across_chunk_boundary_restored_correctly():
    restore_map = {"<PERSON_1>": "Jan de Vries"}
    never_restore = NEVER_RESTORE_ENTITIES

    # Chunk 1 ends mid-placeholder.
    buffer = ""
    safe1, buffer = split_safe_tail("Hallo <PERS", restore_map, never_restore)
    # No complete placeholder yet -- nothing unsafe should have leaked.
    assert "<PERS" not in safe1 or safe1 == ""  # never emit a bare partial fragment
    assert "PERS" not in safe1

    # Chunk 2 completes the placeholder.
    safe2, buffer = split_safe_tail(buffer + "ON_1> vriendelijke groet", restore_map, never_restore)

    combined_safe = safe1 + safe2
    # The complete restored value must appear once combined + buffer flushed.
    final = combined_safe + restore_text(buffer, restore_map, never_restore_entities=never_restore)
    assert final == "Hallo Jan de Vries vriendelijke groet"
    # No raw, unrestored placeholder fragment ever appears in the safe output.
    assert "<PERS" not in combined_safe
    assert "PERSON_1" not in combined_safe


def test_split_safe_tail_never_emits_a_partial_placeholder_across_many_boundaries():
    """Feed the placeholder one character at a time and assert the safe
    output never contains a partial '<...' fragment at any point."""
    restore_map = {"<PHONE_NUMBER_1>": "06-12345678"}
    never_restore = NEVER_RESTORE_ENTITIES
    full_text = "Bel op <PHONE_NUMBER_1> alstublieft."

    buffer = ""
    emitted = ""
    for ch in full_text:
        buffer += ch
        safe, buffer = split_safe_tail(buffer, restore_map, never_restore)
        emitted += safe
        # Never emit a lone "<" without its closing ">" already resolved.
        if "<" in emitted:
            assert ">" in emitted[emitted.index("<") :] or emitted.count("<") == emitted.count(">")
    # Flush whatever remains at "stream end".
    emitted += restore_text(buffer, restore_map, never_restore_entities=never_restore)
    assert emitted == "Bel op 06-12345678 alstublieft."


def test_split_safe_tail_holds_back_at_least_tail_len_when_no_placeholder_present():
    restore_map: dict[str, str] = {}
    text = "x" * (TAIL_LEN + 5)
    safe, remaining = split_safe_tail(text, restore_map, NEVER_RESTORE_ENTITIES)
    assert len(remaining) >= TAIL_LEN
    assert safe + remaining == text


def test_split_safe_tail_holds_everything_when_buffer_shorter_than_tail_len():
    restore_map: dict[str, str] = {}
    text = "short"
    safe, remaining = split_safe_tail(text, restore_map, NEVER_RESTORE_ENTITIES)
    assert safe == ""
    assert remaining == text


def test_split_safe_tail_restores_complete_placeholder_immediately_even_mid_buffer():
    restore_map = {"<PERSON_1>": "Jan de Vries"}
    text = "<PERSON_1>" + ("y" * (TAIL_LEN + 2))
    safe, remaining = split_safe_tail(text, restore_map, NEVER_RESTORE_ENTITIES)
    assert "Jan de Vries" in safe
    assert "<PERSON_1>" not in safe
    assert "<PERSON_1>" not in remaining


def test_secret_placeholder_split_across_boundary_still_never_restored():
    """Never-restore placeholders also need chunk-boundary safety even
    though they are never substituted back -- a naive implementation could
    otherwise emit a bare '<SECRET_1' fragment."""
    restore_map: dict[str, str] = {}  # SECRET never enters restore_map
    never_restore = NEVER_RESTORE_ENTITIES

    buffer = ""
    safe1, buffer = split_safe_tail("Sleutel <SECR", restore_map, never_restore)
    assert "SECR" not in safe1
    safe2, buffer = split_safe_tail(buffer + "ET_1> hier", restore_map, never_restore)
    combined = safe1 + safe2 + restore_text(buffer, restore_map, never_restore_entities=never_restore)
    assert combined == "Sleutel <SECRET_1> hier"


# ---------------------------------------------------------------------------
# DetectedSpan validation
# ---------------------------------------------------------------------------
def test_detected_span_rejects_invalid_bounds():
    import pytest

    with pytest.raises(ValueError):
        DetectedSpan(entity_type="PERSON", start=5, end=5, score=0.9)
    with pytest.raises(ValueError):
        DetectedSpan(entity_type="PERSON", start=-1, end=5, score=0.9)


# ---------------------------------------------------------------------------
# System-review H1/H2 (2026-08-20) — overlap is not only containment
# ---------------------------------------------------------------------------
def test_overlap_not_merely_containment_higher_score_inside_lower():
    """NL_BSN [20:29] score 1.00 sits INSIDE NL_BTW [18:32] score 0.70.

    A containment-only rule takes the BSN first (higher score), then finds
    the BTW span is not contained in it and takes that too — two overlapping
    substitutions, corrupted output. The selection must drop any OVERLAP.
    """
    from klai_pii_text_masking import DetectedSpan, mask_text

    text = "Ons BTW-nummer is NL123456782B01."
    spans = [
        DetectedSpan(entity_type="NL_BSN", start=20, end=29, score=1.00),
        DetectedSpan(entity_type="NL_BTW", start=18, end=32, score=0.70),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"NL_BSN", "NL_BTW"}),
        never_restore_entities=frozenset({"NL_BSN"}),
        instance_counters={},
    )
    assert result.masked_text.count("<") == 1, result.masked_text
    assert "<NL_BSN_1>" in result.masked_text
    assert "NL_BTW" not in result.masked_text
    assert result.masked_text.startswith("Ons BTW-nummer is ")


def test_nested_same_entity_higher_score_inside_lower():
    """A JWT span sits inside a Bearer span with a HIGHER score."""
    from klai_pii_text_masking import DetectedSpan, mask_text

    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CV"
    spans = [
        DetectedSpan(entity_type="SECRET", start=15, end=len(text), score=0.85),
        DetectedSpan(entity_type="SECRET", start=22, end=len(text), score=0.90),
    ]
    result = mask_text(
        text,
        spans,
        enabled_entities=frozenset({"SECRET"}),
        never_restore_entities=frozenset({"SECRET"}),
        instance_counters={},
    )
    assert result.masked_text.count("<SECRET_") == 1, result.masked_text
    assert "eyJhbGci" not in result.masked_text


def test_identical_span_and_score_never_restore_entity_wins():
    """An 8-digit KvK that also passes the padded elfproef yields NL_BSN and
    NL_KVK at the same span with the same score. Ties must not decide whether
    a value lands in the restore map, so the never-restore entity wins."""
    from klai_pii_text_masking import DetectedSpan, mask_text

    text = "Ons KvK-nummer is 10000008 en dat klopt."
    spans = [
        DetectedSpan(entity_type="NL_KVK", start=18, end=26, score=1.00),
        DetectedSpan(entity_type="NL_BSN", start=18, end=26, score=1.00),
    ]
    for ordering in (spans, list(reversed(spans))):
        result = mask_text(
            text,
            list(ordering),
            enabled_entities=frozenset({"NL_BSN", "NL_KVK"}),
            never_restore_entities=frozenset({"NL_BSN"}),
            instance_counters={},
        )
        assert "<NL_BSN_1>" in result.masked_text, result.masked_text
        assert result.restore_map == {}, "a never-restore value must not be restorable"
