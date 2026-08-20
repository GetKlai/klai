---
id: SPEC-RAG-ANSWER-EPISTEMICS-001
version: "0.1.1"
status: draft
created: 2026-08-19
updated: 2026-08-19
author: Mark Vletter
priority: high
related:
  - PR #1059 (shipped the pasted-correspondence detector and PASTED_CORRESPONDENCE_SCOPE this SPEC hardens)
  - SPEC-RAG-CORRESPONDENCE-DISTILL-001 (REQ-2a establishes the "ask the model AND verify in code" doctrine this SPEC applies to the answer layer)
  - SPEC-RAG-SOURCE-SELECTION-001 (sibling; independent fix, shares the Phase 0 observability precondition, and its Phase 3 narrows the assertive register this SPEC constrains)
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (the abstain gate that did not fire because the band read `high`)
  - SPEC-RAG-EVAL-001 (acceptance criteria use its eval harness)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author       | Change        |
|---------|------------|--------------|---------------|
| 0.1.1   | 2026-08-19 | Codex | Implementation clarification: at the product owner's direction, Phase A and Phase B ship as one review unit rather than two PRs. Phase C remains deliberately deferred: v0.1 measures contract violations but never refuses, regenerates, or rewrites an answer because of them. |
| 0.1.0   | 2026-08-19 | Mark Vletter | Initial draft. The 2026-08-19 Voys trunk answer presented the *customer's own* hypothesis (a fraud/security block on the trunk) as "de meest waarschijnlijke oorzaak", and added an exclusion ("niet op een configuratiefout aan jullie kant") that no retrieved chunk supports and that actively steered a support agent away from the correct diagnosis. The existing epistemic guard forbids exactly this and did not prevent it. This SPEC analyses why, and replaces prohibition with structure. |

---

# SPEC-RAG-ANSWER-EPISTEMICS-001: The correspondence is context about what the sender thinks, never an answer source

## Summary

`PASTED_CORRESPONDENCE_SCOPE` (`deploy/litellm/klai_pasted_correspondence.py:149-175`)
tells the model not to adopt the sender's conclusions, self-assessments, exclusions, or
hypotheses as its own findings. It is a well-written instruction and it failed in
production.

It failed because it is **prohibition without structure**: it lists what the model must
not do, provides no shape for what it must produce instead, and has zero code-side
verification — while this codebase's own doctrine
(SPEC-RAG-CORRESPONDENCE-DISTILL-001 REQ-2a) states that *"prompt instructions are
requests the model can partially ignore, not guarantees... ask the model AND verify
deterministically, never ask alone."*

This SPEC gives the answer a typed shape in which **no slot exists for a ranked cause**,
makes the shape machine-verifiable, and measures claim provenance. A prohibition is
negotiable for a language model. A missing slot is not.

## Motivation

### What the system produced

For `request_id=d83d2c14-c2bb-4557-91a1-5831fa9a5a78` the visible footer correctly stated
*"Geplakte correspondentie gedetecteerd: inhoud behandeld als claims van de afzender,
niet als geverifieerde feiten."* The detector fired. The guard was injected. The answer
then opened with:

> **TL;DR** — De 404 Not Found met Reason: Q.850;cause=1 komt van Voys' routeringssysteem
> ná een succesvolle sessie-opzet. Dit wijst op een probleem in de uitgaande
> nummerroutering of belrechten op accountniveau, **niet op een configuratiefout aan
> jullie kant**. De meest waarschijnlijke oorzaak is een **fraude- of
> beveiligingsblokkade** op trunk 451030015, mogelijk geactiveerd door het eerdere
> verdachte verkeer.

Three separate defects in one paragraph:

1. **A ranked cause.** "De meest waarschijnlijke oorzaak is…" is a probability claim over
   hypotheses. No retrieved chunk can support a probability ranking.
2. **An exclusion.** "niet op een configuratiefout aan jullie kant" asserts the negative.
   Retrieval cannot ground a negative: absence of evidence is not evidence of absence.
   This is the sentence that sent a support agent down the wrong path — the actual answer
   was a malformed or wrong number, which the knowledge base states plainly in
   `01_sip_response_codes.md` (*"404 | Not Found | Gebruiker/toestel bestaat niet, of
   extensie niet gevonden"*).
3. **Sender-derived content promoted to conclusion.** The fraud hypothesis originated in
   the customer's own email — the customer had searched the Voys help pages and formed
   that theory themselves. The evidence-pack tokens confirm the email carried
   `fraudedetectiesysteem`, `hypothese`, `vermoeden`, `verdacht`, `beveiligingsgerelateerde`,
   `geblokkeerd`. The knowledge base nowhere links SIP 404 to fraud blocking.

### Why the existing guard did not catch it

Four structural reasons, each of which must be addressed separately.

**1 — It is ask-alone.** The guard is pure prompt text. This codebase deterministically
verifies model output in two comparable places already: `strip_model_citation_artifacts`
for citation artefacts, and `_clean_distilled_query` for the distillation. The
correspondence guard has no equivalent. Per REQ-2a's own reasoning, an instruction the
model can partially ignore *will* be partially ignored — and in that same SPEC's finding 4,
the model demonstrably obeyed one clause of an instruction while ignoring two others in
the same output.

**2 — Laundering through a generic knowledge-base fact.** The model never quoted the
sender. It found a chunk establishing *that* a fraud-detection system exists, and
presented the causal conclusion as its own inference. Formally it never "adopted the
sender's conclusion" — it "independently concluded" what the sender already believed.
The guard draws no distinction between *"X exists"* and *"X caused this"*.

This is why token-level provenance checking alone is insufficient, and this SPEC says so
explicitly rather than overselling the mechanism: when the sender's hypothesis and a
generic supporting fact are *both* present in the corpus, a token-overlap test cannot
separate them.

**3 — The guard asks for separation but never says what may occupy the conclusion.** It
requires distinguishing "what the correspondence claims / what is independently supported
/ what to verify first". The answer *did* have sections. The ungrounded causal claim
simply sat in the most authoritative position, the opening. Structurally compliant,
substantively wrong.

**4 — Ranked speculation is not forbidden at all.** The guard addresses adopting the
*sender's* hypotheses. It says nothing about the model generating its own ranking, which
is what happened.

### The third contributing factor, owned elsewhere

`confidence_band` was `high` because it is the **maximum** post-rerank score
(`klai-retrieval-api/retrieval_api/api/ranking.py:20-44`) and one irrelevant chunk scored
0.8661 while six of seven scored ≤0.28. `high` suppresses the deterministic
low-confidence abstain and licenses an assertive register. That fix belongs to
SPEC-RAG-SOURCE-SELECTION-001 REQ-8/REQ-9 and is deliberately not duplicated here — but
the two compound, and neither alone is sufficient.

### The design principle

The product owner's own formulation is the requirement:

> *"had die naar mijn mening moeten meenemen als context wat de klant denkt en niet mij
> teruggeven als antwoord"*

That is an **information-flow rule**, not a tone rule: pasted correspondence is input to
the *question*, never input to the *answer*. The way to enforce an information-flow rule
is not another prohibition — it is to give the answer a shape in which the forbidden move
has nowhere to go.

Note also that this failure mode is **most likely exactly when retrieval is weakest**. If
the knowledge base lacks the answer, the sender's hypothesis is the only coherent story
in context. Improving retrieval (SPEC-RAG-SOURCE-SELECTION-001) reduces how often the
situation arises; it does not remove the mechanism. The two SPECs are complements, not
alternatives.

## Scope

### In scope

**`deploy/litellm/klai_pasted_correspondence.py`**

- Replace the prohibition-shaped `PASTED_CORRESPONDENCE_SCOPE` with a shape-shaped
  contract carrying machine-readable section markers.

**`deploy/litellm/klai_kb_citation_render.py` (or a new sibling module)**

- Deterministic verification of the section contract; marker stripping before the user
  sees the answer.
- Provenance telemetry, computed for all grounded answers, enforced nowhere in v0.1.

**`klai-libs/citations`**

- Reuse of `extract_salient_query_tokens`; no new public API unless a helper genuinely
  needs to be shared, in which case it lands in the canonical package with tests there.

**Eval**

- `pasted_correspondence` canaries in `chat.yaml` extended with an assertion on answer
  shape and on absence of a ranked cause.

### Out of scope

- **Sentence-level text surgery.** Do not attempt to detect and delete conclusion
  sentences from generated prose. Commit `31b409243` ("stop deleting numbered prose lines
  that contain inline bold") is the precedent: heuristic prose deletion has already caused
  a production regression in this exact codebase. Verification acts on structure and on
  whole-answer decisions, never on cutting sentences out of prose.
- **Phrase or keyword lists** for detecting causal claims, exclusions, or source
  references, in any language. Rejected for the same reason the source-selection SPEC
  rejects them and for the reason `projects/knowledge.md` already forbids hand-curated
  language lists.
- **The confidence band.** Owned by SPEC-RAG-SOURCE-SELECTION-001 REQ-8/REQ-9.
- **Changing the detector** `klai_pasted_correspondence.py:108-144`. This SPEC consumes
  its output unchanged.
- **Generalising the contract to all grounded answers.** v0.1 scopes the shape contract to
  the pasted-correspondence path, where the harm is proven. The provenance telemetry
  (REQ-1) *is* emitted for all grounded answers, precisely so the generalisation decision
  can be made on data rather than intuition.
- **Enforcement action on contract violation.** Deferred to v0.2 by REQ-6.

## Functional Requirements (EARS)

### Phase A — measure provenance (precondition for every later judgement)

#### REQ-1 — claim-provenance telemetry (ubiquitous)

**THE post-call path SHALL** compute and log, for every grounded answer, three token sets
derived via the existing `extract_salient_query_tokens`:

- `C` — salient tokens of the pasted correspondence in the latest user turn (empty when
  the detector did not fire)
- `E` — salient tokens of the served evidence chunks (titles, heading paths, text), using
  the same field list as `has_direct_evidence_for_query`
  (`deploy/litellm/klai_kb_confidence_policy.py:184-190`)
- `A` — salient tokens of the answer

and emit:

- `sender_only_tokens_in_answer: int` — `|A ∩ (C \ E)|`
- `answer_tokens_unsupported_by_evidence: int` — `|A \ (E ∪ user_turn_tokens)|`
- `correspondence_detected: bool`

**THE telemetry SHALL** be emitted at a level that reaches VictoriaLogs
(SPEC-RAG-SOURCE-SELECTION-001 REQ-1 is a hard dependency) and **SHALL NOT** log token
*values* unless `telemetry_level == "full"`, per SPEC-PRIVACY-QUERY-SHADOW-001.

**THE telemetry SHALL NOT** alter the answer in v0.1. It is a measurement, and its known
limitation — that it cannot separate a laundered sender hypothesis from a genuinely
KB-grounded one when both vocabularies are present — is documented here so no later
reader mistakes it for a guarantee.

### Phase B — replace prohibition with shape

#### REQ-2 — typed answer contract for pasted correspondence (event-driven)

**WHEN** the pasted-correspondence detector fires, **THE injected contract SHALL** require
the answer to consist of exactly these sections, in this order, each opened by a stable
machine-readable marker:

1. **What the sender states** — claims attributed to their author, never asserted.
2. **What the knowledge base says about this** — only statements supported by a retrieved
   chunk, each carrying its citation.
3. **What this explicitly does not settle** — what remains open given the retrieved
   evidence.
4. **What to verify first** — concrete checks the reader can perform themselves.

**THE contract SHALL NOT** define a section for a conclusion, a diagnosis, a most-likely
cause, or a recommendation of a single explanation. The absence of the slot is the
mechanism; do not compensate for it with an additional prohibition sentence.

Section 3 is load-bearing and deliberately inverts the pressure that produced the
exclusion defect: a model that has just enumerated what remains open is structurally
disinclined to also assert what has been ruled out. This replaces "ban exclusions" —
which would require semantic negation detection and therefore a phrase list — with a
requirement that competes with it.

#### REQ-3 — the markers are machine-readable and stripped before display (ubiquitous)

**THE contract SHALL** require each section to be opened by a stable marker token that is
language-independent and not natural prose.

**THE render path SHALL** strip every marker before the answer reaches the user, reusing
the established mechanism for internal labels — the evidence-label stream guard and
`strip_model_citation_artifacts` in `klai_kb_citation_render.py` already inject, guard,
and strip internal tokens across both streaming and non-streaming paths.

**THE stripping SHALL** be covered by tests on both paths, including the split-across-
stream-deltas case that the evidence-label guard already handles.

#### REQ-4 — the shape is verified in code, not assumed (ubiquitous)

**THE post-call path SHALL** deterministically verify, when the detector fired:

- all four markers are present;
- they appear in the specified order;
- section 2 contains at least one citation marker when the evidence pack is non-empty.

Result recorded as `answer_contract: {satisfied: bool, missing_sections: [...], order_violation: bool, section2_uncited: bool}`.

This is the REQ-2a half of "ask AND verify". A contract that is only requested is a
contract that is only sometimes honoured.

#### REQ-5 — no behaviour change on the non-correspondence path (ubiquitous)

**WHEN** the detector does not fire, **THE injected prompt SHALL** be byte-identical to
its current form and no marker stripping or contract verification SHALL run. This is the
primary regression guard.

### Phase C — enforcement (deferred)

#### REQ-6 — enforcement is a separate decision on measured data (ubiquitous)

**THE v0.1 implementation SHALL NOT** refuse, regenerate, or rewrite an answer that fails
REQ-4. It records the verdict only.

Enforcement — one bounded regeneration, a deterministic fallback message, or a visible
degradation notice — is specified in v0.2 of this SPEC, gated on:

- the REQ-4 violation rate over a real traffic window;
- the REQ-1 provenance distribution;
- any future confidence-policy change that has a real downstream behavioral effect.

The 2026-08-20 shadow audit removed the earlier corroborated-band dependency because its
`high` → `medium` change affected no current policy and did not measure independent sources.

## Non-Functional Requirements

- **Latency**: no additional LLM round-trip in v0.1. REQ-2 adds prompt tokens to an
  existing injection; REQ-1 and REQ-4 are pure Python over already-materialised strings.
  Post-call hook overhead MUST stay under 20 ms p95.
- **Streaming**: marker stripping MUST work on the streaming path without leaking a
  partial marker to the user, including markers split across deltas. The existing
  `_EVIDENCE_STREAM_GUARD_RE` holdback pattern is the reference implementation.
- **Fail-open**: any failure in REQ-1 or REQ-4 MUST be swallowed and MUST NOT block the
  answer, matching the existing `except Exception: pass` discipline around hook telemetry
  (`klai_knowledge.py:839-841`). A measurement that can break a user's answer is worse
  than no measurement.
- **Privacy**: token values are customer text. Counts always; values only at
  `telemetry_level == "full"`.
- **Multi-tenant**: applies uniformly. No Voys-specific behaviour.

## Acceptance Criteria

| AC ID | Test | Expected outcome |
|-------|------|-------------------|
| AC-1 | Unit: `C`/`E`/`A` computation on a synthetic fixture where the correspondence contains a term absent from all chunks and the answer repeats it | `sender_only_tokens_in_answer > 0` |
| AC-2 | Unit: same fixture with the answer not repeating the term | `sender_only_tokens_in_answer == 0` |
| AC-3 | Unit: detector did not fire | `C` empty, no contract verification runs, `answer_contract` absent |
| AC-4 | Unit: prompt text with detector `False` | Byte-identical to current `_PASTED_CORRESPONDENCE_SCOPE` injection — proves REQ-5 |
| AC-5 | Unit: well-formed four-section answer | `answer_contract.satisfied == True`; all markers absent from the rendered output |
| AC-6 | Unit: answer with sections 1, 2, 4 only | `satisfied == False`, `missing_sections == [3]`; answer still returned unmodified (REQ-6) |
| AC-7 | Unit: answer with sections out of order | `order_violation == True` |
| AC-8 | Streaming test: markers split across stream deltas | No partial marker visible in any yielded chunk; final text marker-free |
| AC-9 | Replay of the incident's pasted email through the hook against a fixed chunk set | Rendered answer contains no ranked-cause slot; the sender's fraud hypothesis appears only under section 1, attributed |
| AC-10 | `chat.yaml` `pasted_correspondence` mix, full run | All canaries produce `answer_contract.satisfied == True`; negative-class control shows zero prompt diff (AC-4 covers the mechanism, this covers the eval-level outcome) |
| AC-11 | Full `chat.yaml` suite before vs. after | Aggregate `context_precision` / `context_recall` MUST NOT regress by more than 0.02 |
| AC-12 | Post-deploy, 48h | REQ-4 violation rate and REQ-1 provenance distribution reported. These are the inputs to v0.2 and the deliverable of this SPEC's Phase C gate |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A four-section answer reads as bureaucratic for a short pasted ticket and users dislike it | medium | medium | Sections may be terse; the contract governs presence and order, not length. AC-10 includes the short-ticket canary. If it still reads badly, tune the section wording — not the section set, since removing the shape reinstates the defect |
| The model emits markers as visible prose despite the strip pass | medium | high | Exactly the failure `strip_model_citation_artifacts` and the evidence-stream guard exist to catch; reuse them rather than writing a third mechanism. AC-8 covers the streaming case that already regressed once |
| Token provenance produces a misleading signal and someone later enforces on it | medium | high | The limitation is stated in REQ-1 itself, in the Motivation, and again here. REQ-6 forbids enforcement in v0.1 and requires a data-backed decision |
| The model relocates the ranked cause into section 2, citing a chunk that supports only the existence of the mechanism | medium | high | The laundering path, unsolved by structure alone. REQ-4's `section2_uncited` catches the uncited variant only. Honest residual risk; AC-12's data determines whether v0.2 needs an LLM-judge verification pass |
| Marker stripping breaks a non-correspondence answer | low | high | REQ-5 makes stripping conditional on the detector; AC-4 and AC-3 guard it |
| Prompt grows and crowds the branch-foundation prefixes stacked above it | low | medium | The shape contract replaces the current prohibition block rather than adding to it; net token delta should be near zero. Report the actual delta in the PR |

## Implementation handoff

Implementation is delegated. Phase A and Phase B are ordered slices in one review unit;
the product owner explicitly requested one complete handoff for review. Phase C is not an
implementation slice in v0.1: REQ-6 defers enforcement until production measurements are
available.

| Slice | Phase | Files | Gate before review |
|-------|-------|-------|--------------------|
| 1 | A | `deploy/litellm/klai_kb_citation_render.py` (or new sibling), `klai_knowledge.py`, tests | AC-1, AC-2, AC-3 |
| 2 | B | `deploy/litellm/klai_pasted_correspondence.py`, render path, `chat.yaml`, tests | AC-4 … AC-11 |

Hard dependency: **SPEC-RAG-SOURCE-SELECTION-001 PR 1 (Phase 0 observability) must be
merged and deployed first.** Without it, REQ-1's telemetry is written to a log level that
does not reach VictoriaLogs, and AC-12 — the entire justification for Phase C — cannot be
collected. Do not start this SPEC before that is confirmed live.

Rules for the implementer:

- Write the failing test first. AC-9 must be RED against current `main`, with the actual
  failure output quoted in the PR body.
- `minimal-changes` and `clean over clever, no parallel old+new` apply: the shape contract
  **replaces** `PASTED_CORRESPONDENCE_SCOPE`'s prohibition block. Do not leave both in the
  prompt.
- The shared-helper stopgate in AGENTS.md applies: the render path is a multi-path helper.
  Before patching, report direct callers, indirect paths, which paths are tested, and which
  are not. `klai_kb_citation_render` is reached from the non-streaming hook
  (`klai_knowledge.py:1578`) and two streaming call sites (`:1607`, `:1617`).
- Note the three-locations rule in `projects/knowledge.md`: chat system-prompt content
  lives in three canonical files. This SPEC touches **path A only** (the litellm hook).
  Confirm explicitly in the PR that paths B (`partner_chat.py`) and C (`synthesis.py`) are
  unaffected, and that `scripts/lint-no-duplicate-chat-prompt.sh` still passes.
- Report `git diff --stat` and actual test command output in the PR body.

## Sources

Production evidence (2026-08-19, VictoriaLogs, org `368884765035593759`):

- `request_id=d83d2c14-c2bb-4557-91a1-5831fa9a5a78` — the answer analysed above;
  `kb_citations_rendered_structured` carries the correspondence vocabulary
  (`fraudedetectiesysteem`, `hypothese`, `vermoeden`, `verdacht`,
  `beveiligingsgerelateerde`, `geblokkeerd`) in `salient_query_tokens`, establishing that
  the fraud hypothesis was present in the sender's own text.
- `retrieval_confidence_band: high` on a pack of `[0.8661, 0.2806, 0.2082]`.

Source references:

- `deploy/litellm/klai_pasted_correspondence.py:108-144` (detector), `:149-175` (guard text), `:178-188` (footer line)
- `deploy/litellm/klai_knowledge.py:428-433,584-590,1572-1620`
- `deploy/litellm/klai_kb_citation_render.py:731-770` (structured render logging), `:800` (`_EVIDENCE_STREAM_GUARD_RE`, the stream holdback pattern REQ-3 reuses)
- `deploy/litellm/klai_kb_confidence_policy.py:161-192` (`low_confidence_query_tokens`, `has_direct_evidence_for_query`)
- `klai-libs/citations/klai_citations/__init__.py:681` (`extract_salient_query_tokens`)
- `klai-retrieval-api/retrieval_api/api/ranking.py:20-44` (band on max — owned by the sibling SPEC)
- Commit `31b409243` — precedent for why heuristic prose deletion is out of scope
- SPEC-RAG-CORRESPONDENCE-DISTILL-001 HISTORY 0.3.0 (REQ-2a doctrine) and finding 4
  (model obeying one instruction clause while ignoring two others in the same output)
