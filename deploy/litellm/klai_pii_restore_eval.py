"""Pure, network-free core of the Phase 0 PII-restore measurement harness
(SPEC-PRIVACY-MISTRAL-PII-001 REQ-0a/REQ-0b).

This module has NO network dependency. It only classifies already-fetched
response text and generates the Dutch drafting-prompt corpus. The live
invocation (real chat/completions calls through the local LiteLLM proxy,
with the ``presidio-pii-phase0`` guardrail from ``config.yaml`` enabled
per-request) lives in ``scripts/eval_pii_restore_live.py``, which imports
this module but is itself a manually-run, opt-in script — same constraint
as ``scripts/eval_pasted_correspondence_live.py`` / ``klai_correspondence_eval.py``.

REQ-0a — restore correctness
-----------------------------
``classify_restore_outcome`` distinguishes AC-0a/AC-0b's pass condition
(the original value comes back byte-identical) from the two failure shapes
named in `BerriAI/litellm#6247 <https://github.com/BerriAI/litellm/issues/6247>`_:

- ``empty_map`` — the literal, unrestored placeholder (``<PERSON_1>``,
  ``<PERSON>``, case-insensitive) is still visible in the response. The
  post-call map lookup found nothing.
- ``corrupted`` — neither the original value nor a literal placeholder
  survives, but the response is non-empty. Something WAS written in that
  slot and it matches neither shape above — the catch-all for #6247's
  ``{'<PERSON>': 'Mike. Wh'}``-style corrupted-map failure, which by
  definition produces unpredictable text and cannot be pattern-matched
  precisely.

REQ-0b — Dutch token survival
------------------------------
``output_parse_pii`` restores a placeholder by literal, CASE-SENSITIVE
string match (verified against the actual LiteLLM v1.96.2 source,
``_unmask_pii_text``: ``text.replace(token, original_text)`` on an exact
substring match — no case-folding, no whitespace tolerance). So if the
original value is present in the final (already-restored) response, that
is sound proof the model echoed the placeholder byte-for-byte — REQ-0b's
own definition of "survived" ("the placeholder appears in the model output
exactly as sent"). This lets ``classify_token_survival`` measure survival
from the client-visible response alone, without intercepting LiteLLM's
internal pre-restore text. Because the real mechanism is case-sensitive,
the "well-formed placeholder" check here is too (a case- or whitespace-
varied reproduction, e.g. ``<person_1>``, is exactly the ``altered``
bucket — LiteLLM's own ``str.replace`` would not have restored it either,
so the client genuinely sees the mangled token, not the original value).

Detection precondition (closed here, not just documented): REQ-2's Dutch
NLP engine is Phase 1 work, so Phase 0 necessarily runs the guardrail with
``presidio_language: "en"`` (see ``config.yaml``). A Dutch name or phone
number the English analyzer never detects is never masked into a
placeholder at all — a verbatim echo of the (unmasked) original value would
then look identical to a successful restore from the client-visible
response alone. Both ``classify_restore_outcome``-driven probes (via
``RestoreProbe.masked_by_analyzer``) and ``classify_token_survival`` accept
an explicit ``masked_by_analyzer`` flag for this reason: the live
orchestration script confirms detection with a direct call to the
analyzer's own ``/analyze`` endpoint before trusting any round-trip result
as restore evidence, and an entity the analyzer never detected is reported
in its own ``not_masked`` bucket rather than silently counted as either a
pass or a survival failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "RestoreOutcome",
    "RestoreProbe",
    "summarize_restore_probes",
    "DraftingPrompt",
    "dutch_drafting_prompts",
    "VERBATIM_TOKEN_SYSTEM_INSTRUCTION",
    "SurvivalOutcome",
    "SurvivalCondition",
    "classify_restore_outcome",
    "classify_token_survival",
    "SurvivalSample",
    "summarize_token_survival",
]

# ---------------------------------------------------------------------------
# REQ-0a — restore correctness classification
# ---------------------------------------------------------------------------

RestoreOutcome = Literal["exact_match", "empty_map", "corrupted", "not_masked"]


def _placeholder_pattern(entity_type: str) -> re.Pattern[str]:
    """An unrestored placeholder for ``entity_type``: ``<TYPE>`` or
    ``<TYPE_1>``, case-insensitive, tolerant of stray whitespace inside the
    brackets. Used for REQ-0a's ``empty_map`` detection, where the question
    is "is there clearly a stuck, unrestored placeholder in this response"
    — a coarser, more lenient check than REQ-0b's byte-exact survival test
    below, and deliberately so: any recognisable stuck placeholder is
    already proof the restore mechanism did not fire.
    """
    escaped = re.escape(entity_type)
    return re.compile(rf"<\s*{escaped}(?:_\d+)?\s*>", re.IGNORECASE)


def _exact_placeholder_pattern(entity_type: str) -> re.Pattern[str]:
    """The well-formed placeholder LiteLLM v1.96.2 actually generates:
    ``<ENTITY_TYPE_N>``, case-SENSITIVE, no internal whitespace — matching
    ``_finalize_presidio_anonymize_numbered_tokens`` in the vendored
    ``presidio.py`` guardrail hook exactly. REQ-0b defines survival as the
    placeholder appearing "exactly as sent", and LiteLLM's own restore
    (``_unmask_pii_text``) uses a plain, case-sensitive ``str.replace`` — so
    a case- or whitespace-varied reproduction would not have been restored
    by the real mechanism either. Used only by ``classify_token_survival``.
    """
    escaped = re.escape(entity_type)
    return re.compile(rf"<{escaped}(?:_\d+)?>")


def _loose_entity_token_pattern(entity_type: str) -> re.Pattern[str]:
    """The entity type name, with an optional numeric suffix, appearing
    ANYWHERE — with or without brackets, any case. Used only once a
    stricter pattern has already failed to match, to catch a mangled token
    (wrong case, missing bracket, stray punctuation) rather than a clean
    placeholder.
    """
    escaped = re.escape(entity_type)
    return re.compile(rf"{escaped}(?:_\d+)?", re.IGNORECASE)


def classify_restore_outcome(
    *, original_value: str, response_text: str, entity_type: str
) -> RestoreOutcome:
    """Classify one REQ-0a round-trip probe.

    Raises ``ValueError`` on an empty response — that is neither a restore
    success nor either named failure shape, and callers must not silently
    fold it into ``corrupted``.
    """
    if not response_text.strip():
        raise ValueError("cannot classify restore outcome from an empty response")
    if original_value and original_value in response_text:
        return "exact_match"
    if _placeholder_pattern(entity_type).search(response_text):
        return "empty_map"
    return "corrupted"


@dataclass(frozen=True)
class RestoreProbe:
    mode: Literal["streaming", "non_streaming"]
    entity_type: str
    original_value: str
    response_text: str
    # REQ-0a precondition: did the analyzer actually detect (and therefore
    # mask) this entity in the outbound prompt at all? Defaults to True so
    # existing call sites that never checked keep their prior behaviour;
    # the live orchestration script always sets this explicitly from a
    # direct call to the analyzer's own /analyze endpoint. When False, the
    # original value reaching the model unmasked would make a verbatim
    # echo look identical to a successful restore — this probe never
    # exercised the restore mechanism at all, so it cannot be scored as
    # exact_match/empty_map/corrupted (Sol delta-review finding).
    masked_by_analyzer: bool = True

    @property
    def outcome(self) -> RestoreOutcome:
        if not self.masked_by_analyzer:
            return "not_masked"
        return classify_restore_outcome(
            original_value=self.original_value,
            response_text=self.response_text,
            entity_type=self.entity_type,
        )


def summarize_restore_probes(probes: list[RestoreProbe]) -> dict:
    """AC-0a/AC-0b rollup: pass iff every probe's outcome is exact_match."""
    if not probes:
        raise ValueError("summarize_restore_probes requires at least one probe")

    rows = [
        {"mode": p.mode, "entity_type": p.entity_type, "outcome": p.outcome}
        for p in probes
    ]
    failures = [row for row in rows if row["outcome"] != "exact_match"]
    return {
        "probes": rows,
        "all_pass": not failures,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# REQ-0b — Dutch drafting-prompt corpus
# ---------------------------------------------------------------------------

_DUTCH_NAMES = [
    "Jan de Vries",
    "Marieke Bakker",
    "Pieter van Dijk",
    "Anna Jansen",
    "Willem de Boer",
]

_DUTCH_PHONES = [
    "06-12345678",
    "06-23456789",
    "020-1234567",
    "06-98765432",
    "010-7654321",
]

# (category, template) — REQ-0b names three drafting shapes explicitly:
# write an email, summarise a call, draft a reply. Two templates per shape
# x 5 name/phone pairs = 30, meeting the ">= 30" floor exactly.
#
# Each template mentions {name} and {phone} EXACTLY ONCE. Presidio's
# analyzer returns one detection PER SPAN, and output_parse_pii numbers
# tokens per span in left-to-right order (verified against
# _finalize_presidio_anonymize_numbered_tokens in the vendored LiteLLM
# v1.96.2 source) — so a name repeated twice in one prompt becomes TWO
# DISTINCT placeholders (<PERSON_1>, <PERSON_2>), not one. A survival check
# that only asks "is the value present anywhere in the response" would
# then pass even if just one of the two placeholders round-tripped,
# silently inflating the measured survival rate (Sol delta-review
# finding). Keeping each entity to a single mention removes the ambiguity
# structurally instead of requiring per-placeholder-instance tracking.
_TEMPLATES: list[tuple[str, str]] = [
    (
        "write_email",
        "Schrijf een e-mail aan {name} om een afspraak te bevestigen. Zet "
        "aan het einde het telefoonnummer {phone} erbij zodat de "
        "ontvanger kan terugbellen.",
    ),
    (
        "write_email",
        "Stel een e-mail op voor {name} ({phone}) over de voortgang van "
        "het project.",
    ),
    (
        "summarize_call",
        "Vat het volgende telefoongesprek samen: {name} belde vanaf "
        "{phone} met een vraag over een factuur.",
    ),
    (
        "summarize_call",
        "Maak een korte samenvatting van een gesprek met {name}, "
        "bereikbaar op {phone}, over een technisch probleem.",
    ),
    (
        "draft_reply",
        "Schrijf een antwoord op de vraag van {name} over de levertijd. "
        "Vermeld dat men kan terugbellen op {phone} bij onduidelijkheid.",
    ),
    (
        "draft_reply",
        "Concept-antwoord voor {name} ({phone}): bevestig de ontvangst "
        "van het verzoek.",
    ),
]


@dataclass(frozen=True)
class DraftingPrompt:
    id: str
    category: str
    text: str
    person: str
    phone: str


def dutch_drafting_prompts() -> list[DraftingPrompt]:
    """REQ-0b's corpus: >= 30 Dutch drafting prompts, each containing at
    least one person name and one phone number, spanning the three named
    drafting shapes (write an email, summarise a call, draft a reply).
    """
    prompts: list[DraftingPrompt] = []
    for template_idx, (category, template) in enumerate(_TEMPLATES):
        for pair_idx, (name, phone) in enumerate(zip(_DUTCH_NAMES, _DUTCH_PHONES)):
            prompts.append(
                DraftingPrompt(
                    id=f"{category}-{template_idx}-{pair_idx}",
                    category=category,
                    text=template.format(name=name, phone=phone),
                    person=name,
                    phone=phone,
                )
            )
    return prompts


VERBATIM_TOKEN_SYSTEM_INSTRUCTION = (
    "Als de tekst een token bevat met de vorm <PERSON_1>, <PHONE_NUMBER_1> "
    "of <EMAIL_ADDRESS_1> (hoofdletters, onderstrepingsteken, cijfer en "
    "punthaken erbij), neem dat token dan EXACT en ongewijzigd over in je "
    "antwoord. Verander geen hoofdletters, voeg geen leesteken toe of weg, "
    "en vervang het niet door een naam, telefoonnummer, e-mailadres of "
    "omschrijving."
)


# ---------------------------------------------------------------------------
# REQ-0b — token survival classification
# ---------------------------------------------------------------------------

SurvivalOutcome = Literal[
    "survived", "not_returned", "altered", "paraphrased", "not_masked"
]
SurvivalCondition = Literal["with_instruction", "without_instruction"]

# Necessarily incomplete — REQ-0b's "paraphrased" bucket is inherently
# open-ended (Motivation: "plain paraphrase to 'de genoemde persoon'"). This
# word list is the harness's best-effort signal, not a semantic classifier.
#
# Entity-scoped on purpose: a shared word list would let a PERSON-only
# paraphrase (e.g. "de genoemde persoon") also mark a PHONE_NUMBER check on
# the SAME response as "paraphrased" even when the number was simply
# dropped — every response is scored once per entity type, so a shared
# list systematically contaminates the not_returned/paraphrased split
# (Sol delta-review finding).
_PARAPHRASE_MARKERS_BY_ENTITY: dict[str, tuple[str, ...]] = {
    "PERSON": (
        "genoemde persoon",
        "de contactpersoon",
        "deze persoon",
        "de klant",
        "desbetreffende persoon",
    ),
    "PHONE_NUMBER": (
        "het genoemde nummer",
        "het telefoonnummer",
        "het nummer",
        "de telefoongegevens",
    ),
    "EMAIL_ADDRESS": (
        "het genoemde e-mailadres",
        "het e-mailadres",
        "de e-mailgegevens",
    ),
}


def classify_token_survival(
    *,
    response_text: str,
    entity_type: str,
    original_value: str,
    masked_by_analyzer: bool = True,
) -> SurvivalOutcome:
    """Classify one REQ-0b placeholder against the client-visible response.

    ``masked_by_analyzer`` is the same precondition ``RestoreProbe`` carries
    for REQ-0a (see module docstring): when False, the analyzer never
    detected this entity in the outbound prompt, so there was no
    placeholder to survive in the first place — reported as its own
    ``not_masked`` bucket rather than folded into ``not_returned``.

    Priority order otherwise (most specific signal first):

    1. ``survived`` — the original value is present (restore succeeded,
       which per LiteLLM's exact-match ``str.replace`` unmask logic is only
       possible if the model echoed the placeholder byte-for-byte), OR the
       exact, case-sensitive placeholder for this entity type is still
       visible (restore itself did not fire — e.g. #6247's empty-map shape
       — but the model DID reproduce the token exactly, which is REQ-0b's
       own definition of survival independent of whether restore worked).
    2. ``altered`` — a mangled/loose variant of the entity token is
       present (wrong case, missing bracket, stray punctuation) but not an
       exact match.
    3. ``paraphrased`` — no token-shaped remnant at all, but a generic
       Dutch referent phrase for THIS entity type appears (word list
       above).
    4. ``not_returned`` — default: the entity appears to have been dropped
       from the draft entirely.
    """
    if not masked_by_analyzer:
        return "not_masked"
    if not response_text.strip():
        return "not_returned"
    if original_value and original_value in response_text:
        return "survived"
    if _exact_placeholder_pattern(entity_type).search(response_text):
        return "survived"
    if _loose_entity_token_pattern(entity_type).search(response_text):
        return "altered"
    lowered = response_text.lower()
    markers = _PARAPHRASE_MARKERS_BY_ENTITY.get(entity_type, ())
    if any(marker in lowered for marker in markers):
        return "paraphrased"
    return "not_returned"


@dataclass(frozen=True)
class SurvivalSample:
    prompt_id: str
    condition: SurvivalCondition
    entity_type: str
    outcome: SurvivalOutcome


def summarize_token_survival(samples: list[SurvivalSample]) -> dict:
    """REQ-0b rollup: survival rate per entity type per condition
    (with/without the verbatim-token system instruction), with the three
    failure kinds reported separately as REQ-0b requires.

    Samples where the analyzer never masked the entity in the first place
    (``not_masked``) are excluded from ``survival_rate``'s denominator —
    they never exercised the placeholder-survival question at all, so
    counting them would understate survival for reasons that have nothing
    to do with the model's behaviour. They are still counted and reported
    (``not_masked``), mirroring how the existing correspondence-eval
    harness excludes "skipped" (raw-query-fallback) samples from its pass
    rate while still surfacing them as a warning
    (``eval_pasted_correspondence_live.py::_run_canary``).
    """
    if not samples:
        raise ValueError("summarize_token_survival requires at least one sample")

    keys = sorted({(s.entity_type, s.condition) for s in samples})
    report: dict[str, dict] = {}
    for entity_type, condition in keys:
        bucket = [
            s
            for s in samples
            if s.entity_type == entity_type and s.condition == condition
        ]
        total = len(bucket)
        counts = {
            outcome: sum(1 for s in bucket if s.outcome == outcome)
            for outcome in (
                "survived",
                "not_returned",
                "altered",
                "paraphrased",
                "not_masked",
            )
        }
        scored_total = total - counts["not_masked"]
        survival_rate = counts["survived"] / scored_total if scored_total else None
        report.setdefault(entity_type, {})[condition] = {
            "total": total,
            "scored_total": scored_total,
            "not_masked": counts["not_masked"],
            "survived": counts["survived"],
            "survival_rate": survival_rate,
            "not_returned": counts["not_returned"],
            "altered": counts["altered"],
            "paraphrased": counts["paraphrased"],
            # REQ-9's later gate, surfaced here for visibility only — this
            # harness does not enforce it, Phase 3 does. No scored samples
            # (survival_rate is None) never counts as passing the gate.
            "below_95_percent_gate": survival_rate is None or survival_rate < 0.95,
        }
    return report
