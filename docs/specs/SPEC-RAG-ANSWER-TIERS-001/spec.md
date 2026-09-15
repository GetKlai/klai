---
id: SPEC-RAG-ANSWER-TIERS-001
version: "0.4.0"
status: REQ-1 and REQ-3 built; REQ-2 measured and dropped; REQ-4 partly built
created: 2026-09-15
author: Fable (Opus 5), commissioned by Mark Vletter
priority: high
tenant_scope: platform-wide; the widget is the surface that hurts today
related:
  - SPEC-VOYS-HELPBOT-001 REQ-7 (consent-gated broad mode; this SPEC adds the
    class that REQ-7 does not cover and must not be confused with)
  - SPEC-RAG-SOURCE-SELECTION-001 (the selector whose rejection triggers today's
    refusal; its query_score is the signal REQ-2 gates on)
  - SPEC-RAG-MULTILINGUAL-CHAT-001 (the language contract; unaffected)
---

# HISTORY

| Version | Date | Change |
|---|---|---|
| 0.4.0 | 2026-09-15 | Review corrections. Two defects in the REQ-1 implementation: skipping the composer also skipped the only mechanical guard against a model-written URL or fake citation reaching the visitor (reproduced, now stripped), and `force_escalation` — which fires on frustration and shouting, not just an explicit request for a person — sent conversational turns back to the canned refusal. The offer now survives without the refusal text. Also corrected an overclaim: concurrency bounds the added latency, it does not zero it, and the two classifiers do not share a timeout. |
| 0.3.0 | 2026-09-15 | REQ-2 measured before building and dropped on the numbers: of the 73 refusing turns in seven days, 47 carried no salient query tokens at all and ~17 were escalation-shaped, leaving 2 that a broader retrieval attempt could have rescued. Building it would have cost a rewrite plus a retrieval round on every refusal to buy two turns a week. |
| 0.2.0 | 2026-09-15 | REQ-1 and REQ-3 built and merged. The classifier shares the gather that already carried the escalation classifier, so the worst-case latency window is unchanged rather than merely small — both sit behind the same 2 s timeout that was already accepted. REQ-2 deliberately left for its own change: it touches the retrieval loop and its acceptance evidence is the replay in §6, which is a measurement exercise rather than a patch. |
| 0.1.0 | 2026-09-15 | Initial. Written after a visitor asked the Voys widget "Can I also talk english?" and was told "I can't find this in our help articles", with a broad-mode consent block and an appointment button underneath. |

---

# 1. Why

A visitor opened the Voys help widget and asked, in English, whether they could
talk English. Production trace `request_id 469e4e48-94ad-4555-943a-a45b92bf46ae`:
the retriever searched the help articles for the tokens
`["also","can","english","talk"]`, found three candidates scoring 0.02 to 0.11,
rejected all three as `query_not_supported`, and the citation firewall then
replaced the model's answer with the canned refusal. The visitor was offered a
broader search and an appointment with a human.

Every component behaved as specified. The defect is that the widget has only
two outcomes for a turn, and this turn is neither of them.

**The pipeline conflates two different failures.** "We have no grounded answer
to a question about us" is one, and refusing there is correct and deliberate —
`Moffatt v. Air Canada` is why. "This was not a question about us at all" is the
other, and refusing there is a category error. Today both produce the same
output.

Measured over seven days on the Voys widget: **73 of 540 turns (13.5%) ended on
that refusal**, and in all 73 the retriever returned exactly three candidates
and the selector rejected all of them. Retrieval never comes back empty; it
comes back with three things that do not answer the question. So the refusal
population is not "the knowledge base is missing content" — it is a mix of
genuine coverage gaps and turns that were never knowledge questions.

Path A (LibreChat) does not hurt here, and the reason is instructive rather than
reusable. Its firewall is gated on the user's mode: strict replaces the answer,
open passes the model's answer through unchanged. The code says why, with an
incident attached (a tester on 2026-05-27 saw the canned refusal in every mode):
*"clobbering it with a refusal trains users to ignore the open/strict toggle."*
The widget has no toggle, so it sits permanently in the branch that incident
already established is wrong whenever the model had a usable answer.

Path A's meta-query detector is not the answer either. Measured on real
sentences: it matches "Wie ben je?" and "What can you do?", and misses "Can I
also talk english?", "Kan ik ook Engels praten?", "Spreek je Duits?" and
"dankjewel". A hand-curated sentence list is the same failure mode this repo
already documents for hand-curated language lists.

# 2. The model: three classes, not two

Every turn belongs to exactly one class, decided by what the ANSWER would
assert:

| Class | The answer asserts | Treatment |
|---|---|---|
| **About us** | something checkable about this organisation: prices, features, procedures, availability, outages | must be grounded in help articles, or refuse. Unchanged. |
| **About the world** | something checkable that is true regardless of provider | broad mode, with explicit consent. Unchanged — SPEC-VOYS-HELPBOT-001 REQ-7 owns this. |
| **About this conversation** | nothing checkable outside this chat window: what language we speak, that the visitor is welcome, that you are an AI | answer plainly. No retrieval refusal, no consent offer, no appointment. **New.** |

REQ-7 already carries a decidable test for the first two: *could this sentence
be written unchanged by any other phone provider?* This SPEC adds the test that
separates the third: **could this sentence be checked against anything outside
this chat window?** No → class three.

"Yes, I can answer in English" is about the conversation. "Voys supports
English-speaking customers" is about us and needs an article. The two sentences
look similar and are in different classes; that is the whole difficulty, and it
is why the boundary is stated as a test rather than a list.

# 3. Requirements

## REQ-1 [HARD] — Conversational turns leave the knowledge pipeline · built

A turn classified "about this conversation" is answered without the grounding
firewall, without the broad-mode consent offer, and without the appointment
offer. It carries no citations and no URLs — the existing bans stay.

Classification runs **before** retrieval is consumed and **concurrently with
it**, so it costs no added wall-clock on the 86.5% of turns that do not need it.
Prior art: Adaptive-RAG routes on a small classifier that adds under 100 ms and
saves seconds of unnecessary retrieval.

Not negotiable: the classifier decides the CLASS, never the CONTENT. A class-
three answer that nevertheless asserts something about the organisation is a
defect, and REQ-4 measures it.

## REQ-2 [HARD] — One broader attempt before refusing · measured, NOT built

When the turn is about us and the selector rejected every candidate, rewrite the
query once and retrieve again before falling back to the refusal. The retry is
**gated on the weak-retrieval signal, never unconditional**: production guidance
is to run normal retrieval first and rewrite only when the top result is below
threshold, and there is evidence that query expansion actively harms queries
that did not need it. Unconditional broadening would make the 86.5% worse to
help the 13.5%.

Budget: the retry may add at most one rewrite call plus one retrieval round. It
fires only on turns that would otherwise have refused, so the cost lands on
about one turn in seven.

`classify_gap` already produces the signal (`hard`, `soft`, or no gap) and
`SPEC-RAG-SOURCE-SELECTION-001` already computes a per-candidate `query_score`.
No new measurement is introduced.

**Measured 2026-09-15, and the measurement says do not build this.** §6 asked
for the replay before the patch. Grouping all 73 refusing turns of the last
seven days by their salient query tokens:

| what the retriever searched on | turns | what the turn actually was |
|---|---|---|
| *(no salient tokens at all)* | **47** | greetings, thanks, one-word turns — class three |
| "ben doorverbonden drie echt keer lost niemand zat" | 12 | a frustrated complaint — escalation |
| "zoek" | 3 | a fragment |
| "medewerker spreken wil" | 3 | asking for a person — escalation |
| "dus vandaag wel" | 2 | a follow-up fragment |
| "afspraak nerds wil" | 2 | asking for an appointment — escalation |
| "also can english talk" | 2 | the reported turn — class three |
| "assign call calling ... international number polish team" | 2 | **a real question retrieval missed** |

64% of the refusal population has no salient query tokens at all: the retriever
was searching the help articles on nothing. Another ~23% is escalation-shaped.
**Two turns in seven days — under 3% of the refusals — are a genuine question
that a broader retrieval attempt could have rescued.**

Rewriting and re-retrieving would therefore buy about two turns a week, at the
cost of a rewrite call plus a retrieval round on every refusing turn, against
published evidence that query expansion actively harms queries that did not need
it. REQ-1 already removes the 47, and escalation already owns the 17.

This requirement stays written down rather than deleted, because the reasoning
is the deliverable: it is correct in general and wrong at this corpus's numbers.
Revisit when the refusal population stops being dominated by turns that were
never knowledge questions — the same query-token grouping is the trigger.

## REQ-3 [HARD] — The grounding boundary does not move · built

Anything that asserts something about the organisation stays grounded or gets
refused. `Moffatt v. Air Canada` held a company liable for its chatbot's
invention and rejected the defence that the bot was a separate entity; the
tribunal's reasoning was that it makes no difference whether information comes
from a static page or a chatbot. REQ-1 and REQ-2 change WHICH turns enter the
firewall, never what the firewall does once a turn is inside it.

## REQ-4 [SHOULD] — The classification is observable and falsifiable · partly built

Every turn logs its class and, for class three, whether the rendered answer
contained an organisation-specific claim. Without this the boundary drifts
silently, which is the failure mode the widget already suffered for months.

# 4. Out of scope

- A user-visible strict/open toggle on the widget. REQ-7 settled that: a help-page
  visitor does not know what "strict" means and will not go looking for a setting.
- Multi-hop retrieval. Adaptive-RAG's third tier is real but there is no evidence
  of demand here; the measured failure is single-hop.
- Changing the selector's thresholds. This SPEC consumes its signal, it does not
  retune it.
- Path A. Its mode toggle already covers this; a second mechanism there would be
  the parallel-paths mistake this repo keeps paying for.

# 5. Acceptance criteria

1. "Can I also talk english?" on the Voys widget is answered in English, with no
   refusal, no consent block and no appointment button. Regression test names the
   reported turn.
2. A genuine Voys question that today refuses because of phrasing gets a grounded
   answer after the retry, proven on a case drawn from the 73.
3. A question about us that the articles genuinely do not cover still refuses,
   with the appointment offer intact. Proven by an existing test that must not
   change.
4. A class-three answer containing an organisation-specific claim is counted by
   REQ-4 and visible in the logs.
5. Wall-clock on a turn that needs neither REQ-1 nor REQ-2 does not regress.

# 6. Verification

Unit tests per requirement, plus a replay of the 73 refusing turns from the last
seven days through the new path, reporting how many change class and how many
change outcome. The replay is the acceptance evidence for criteria 2 and 3: a
claim that the boundary held is worth nothing without the count.

# 7. As built (REQ-1, REQ-3)

`app/services/turn_scope.py` classifies the visitor's turn concurrently with
retrieval, inside the `asyncio.gather` that already carried the escalation
classifier.

**Correction to v0.2.0, which overclaimed this.** `gather` waits for the slowest
member, and the two classifiers hold separate 2 s timeouts rather than one
shared budget. Concurrency therefore bounds the added latency by the classifier's
own duration, it does not make it zero: a turn whose retrieval finished in 200 ms
waits for this call. The honest statement of acceptance criterion 5 is that the
worst case is unchanged (still 2 s, already accepted for escalation) while the
typical case grows by however long the classifier actually takes — and that
number is not yet measured. Measuring p50/p95 on the running service is the
remaining evidence for criterion 5; until then it is argued, not proven.

`_compose_backend_managed_answer` gained one branch: a conversational turn with
text and no forced escalation returns the model's own answer with empty sources
and `turn_scope: conversational` on the decision, so the existing
`partner_chat_citation_selection_decision` event carries the class (REQ-4, first
half; the organisation-claim counter is the open half).

Three things the branch deliberately does NOT do, each with a test:
`force_escalation` still wins, because a visitor asking for a person must keep
the booking button; an empty model answer still falls back to the refusal rather
than sending silence; and a classifier that fails returns `None`, which
`is_conversational` reads as "not conversational" — a broken classifier restores
exactly today's behaviour and can never open the firewall.
