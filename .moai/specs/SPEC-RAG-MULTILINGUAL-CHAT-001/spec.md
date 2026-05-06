---
id: SPEC-RAG-MULTILINGUAL-CHAT-001
version: "1.0"
status: draft
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: medium
issue_number: TBD
related_specs:
  - SPEC-RAG-CONTEXTUAL-001 (uses contextual.detect_language for documents — pre-existing, unchanged)
  - SPEC-RAG-EVAL-001 (eval-suite — extends with cross-lingual coverage)
  - SPEC-API-001 (partner_chat in portal-api — second prompt location to update)
related_issues:
  - "#452 (follow-up: multilingual coverage for default chat templates + taxonomy category names)"
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-06 | Mark Vletter | Initial draft after web-research validation. Klai is expanding from NL-only to a multi-country team (DE, ES, UK, ZA) and the existing chat synthesis prompts hardcode an NL/EN switch that excludes other languages. Retrieval (bge-m3) is already cross-lingual; only the chat-answer layer needs change. |

---

# SPEC-RAG-MULTILINGUAL-CHAT-001: Language-agnostic chat answer layer

## Context

### Goal

Make the Klai chat answer in the language of the user's question,
regardless of the language of the source documents in the knowledge
base. A Spanish question against a Dutch corpus should produce a Spanish
answer with citations [n] pointing to the Dutch source URL. Same for
German, English, Afrikaans.

The retrieval layer (bge-m3 + Qdrant + FalkorDB) is already polyglot —
this SPEC does not touch it. Only the chat-answer layer (system prompt
that steers LLM generation) needs to evolve.

### Two prompt locations, identical wording

`search-broadly-when-changing` audit found two services with the same
hardcoded NL/EN switch:

1. `klai-retrieval-api/retrieval_api/services/synthesis.py:16-33` —
   `_SYSTEM_PROMPT` used by the `/synthesize` streaming endpoint.
2. `klai-portal/backend/app/services/partner_chat.py:44-61` —
   `_GROUNDED_SYSTEM_PROMPT` used by partner-chat completions.

Both prompts contain:

```python
"[CRITICAL] Respond in the language of the user's question. "
"Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
"If the user writes English, respond in English. Never switch mid-conversation."
```

REQ-1 updates both files in lockstep.

### Industry-standard pattern

Web research (see `research.md` for sources and citations) confirms
production multilingual chatbots use **per-message language detection
with three guards**:

1. Minimum message length (short replies inherit prior language)
2. Single-foreign-word tolerance (no flip on isolated foreign terms)
3. Substantive switch (full-sentence question in a different language
   triggers and persists)

This is what ChatGPT and Claude do today. Confirmation prompts on
switch ("I see you switched to French — continue in French?") are
optional and rejected for Klai (internal-team tool, friction is not
worth the safety net).

### Architectural decision: detection in the system prompt

REQ-1 expresses the three guards as natural-language instructions in
the system prompt itself, not as a separate detection layer. Rationale
in `research.md` § "Architectural decision".

### Why retrieval is already cross-lingual

bge-m3 (`BAAI/bge-m3`) embeds queries and documents from 100+ languages
into a shared vector space. M3-Embedding paper reports 75.5% Recall@100
on the MKQA cross-lingual benchmark, beating OpenAI's text-embedding-large.
A Spanish query already retrieves semantically related Dutch chunks.
Source: `research.md` § Finding 1.

---

## Scope

### In scope

1. Rewrite `_SYSTEM_PROMPT` in
   `klai-retrieval-api/retrieval_api/services/synthesis.py` to detect
   the language of the user's most recent substantive message and
   respond in that language, applying the three industry-standard
   guards (minimum length, single-foreign-word tolerance, substantive
   switch).
2. Rewrite `_GROUNDED_SYSTEM_PROMPT` in
   `klai-portal/backend/app/services/partner_chat.py` with the same
   replacement. The replacement string MUST be defined ONCE in a new
   shared library `klai-libs/chat-prompts` and imported by both
   `klai-retrieval-api` and `klai-portal`. Duplicating the string
   inline in both services is NOT allowed in V1; this matches the
   existing klai-libs pattern (connector-credentials, identity-assert,
   service-auth, etc.) and is the industry-standard approach for a
   small number of cross-service prompts (prompt-management platforms
   like Langfuse are deferred until prompt count or A/B testing
   demands them).
3. Extend the eval-suite (`klai-retrieval-api/retrieval_api/eval/`)
   to cover queries in NL, EN, DE, FR, PT, and ES. Add a new
   `language_correctness` metric defined as the percentage of responses
   where the response language matches the query language.
4. Update `judge_client.py` to support multilingual judge prompts
   (currently NL-only, see `eval/judge_client.py:187`).
5. Update `scripts/generate_gate_reference.py` to generate cross-lingual
   reference data covering all six target languages, not just 50/50
   NL/EN.

### Out of scope (What NOT to Build)

- No changes to ingest pipeline, embedding model, taxonomy, document
  storage, or knowledge-graph.
- No changes to the existing per-document enrichment summary templates
  (NL + EN + EN-fallback in `contextual.py`). Adding DE/ES enrichment
  templates is a separate SPEC if a future eval shows
  enrichment-quality regression.
- No changes to portal frontend i18n. Paraglide/Inlang already manages
  UI translations and DE is documented as planned in `product.md`.
- No changes to `default_templates.py` (default chat templates) — the
  hardcoded Dutch prompt content there is user-editable product content
  owned by the product team, with an explicit `@MX:NOTE` warning
  against autonomous edits.
- No changes to mailer templates.
- No query-translation layer at retrieval time (bge-m3 makes this
  redundant and counter-productive per
  `research.md` § Finding 1).
- No persistent per-tenant or per-user explicit language preference for
  chat. Auto-detect from the message is the contract. The existing
  `portal_users.language` field (from `/api/me/language`) drives UI
  locale only and is **not** read by chat.
- No per-tenant model-tier override (klai-fast vs klai-smart selection
  per org). All tenants use the platform-default synthesis model.
  Per-tenant model selection is a possible future SPEC if usage
  patterns or eval results demand it; explicitly deferred here.
- No new authentication paths, no new SOPS secrets, no new external
  dependencies.
- No mid-conversation confirmation prompt ("I see you switched to
  French — continue in French?"). Confirmation friction is not added.
- No support for non-Latin script languages (CJK, Arabic, Cyrillic,
  Hebrew). The current target is the six Western-European languages
  where Klai has team members today: NL (covers Nederlands + Vlaams),
  EN (covers UK + ZA where colleagues use English at work), DE, FR
  (covers Waals + future French-speaking tenants), PT, ES. Afrikaans
  is explicitly NOT a target — ZA team members work in English.

---

## Requirements (EARS)

### REQ-RAG-MULTILINGUAL-CHAT-001-01 — Synthesis system prompt rewrite

When `klai-retrieval-api` builds the synthesis request to the LLM, the
system MUST use a system prompt that:

1. Instructs the model to respond in the language of the user's most
   recent substantive message.
2. Treats messages with fewer than 5 words as inheriting the language
   of the most recent prior message of 5+ words. The first user
   message in a conversation is always treated as substantive
   regardless of length.
3. Treats single foreign-language words inside an otherwise
   consistent-language message as non-switches.
4. Treats a full-sentence question in a different language as a
   substantive switch that persists until another substantive switch.
5. Instructs the model to translate cited content from the source
   language into the user's language naturally, without translator
   disclaimers, apologies, or commentary on language mismatch.
6. Preserves citations `[n]` linking to the original source URL
   regardless of source language.
7. Removes the explicit Dutch sentence "Als de gebruiker Nederlands
   schrijft, antwoord je in het Nederlands" — the new prompt MUST be
   language-list-free (no explicit allow-list of supported languages).

The replacement applies to `_SYSTEM_PROMPT` in
`klai-retrieval-api/retrieval_api/services/synthesis.py`.

### REQ-RAG-MULTILINGUAL-CHAT-001-02 — Shared prompt library

A new sub-library MUST be added at `klai-libs/chat-prompts/` with its
own `pyproject.toml`, exposing the grounded chat system prompt as a
public constant (e.g. `klai_chat_prompts.GROUNDED_CHAT_SYSTEM_PROMPT`).

Both `klai-retrieval-api` and `klai-portal` MUST add this library as a
dependency and import the prompt from it. The previous in-file
constants `_SYSTEM_PROMPT` (synthesis.py) and
`_GROUNDED_SYSTEM_PROMPT` (partner_chat.py) MUST be removed and
replaced with the import from the shared library.

The library is the single source of truth. A change to chat behaviour
is a change to one constant in one library, picked up by both
services on next deploy.

A CI check MUST verify that no other `klai-*` service contains a
hardcoded copy of the prompt string (regex scan for the prompt's
opening line). This catches accidental re-introduction of duplication
in a future service.

### REQ-RAG-MULTILINGUAL-CHAT-001-03 — Multilingual eval-suite

The system MUST extend `klai-retrieval-api/retrieval_api/eval/` to
cover at minimum six query languages: Dutch (nl), English (en),
German (de), French (fr), Portuguese (pt), Spanish (es).

A new test set MUST contain at minimum 20 queries per language against
the existing Dutch reference corpus (`voys/support` or a snapshot
thereof, fixture-mounted for offline reproducibility).

The eval suite MUST report per-language scores for the existing
metrics (faithfulness, answer-relevance, citation-correctness) plus a
new `language_correctness` metric defined as the percentage of
responses where the response language matches the query language.

`scripts/generate_gate_reference.py` MUST be extended to produce
reference answers covering all six target languages, replacing the
current 50/50 NL/EN split with an even per-language distribution.

### REQ-RAG-MULTILINGUAL-CHAT-001-04 — Multilingual judge prompts

`klai-retrieval-api/retrieval_api/eval/judge_client.py` MUST construct
the LLM-as-judge prompt in a language-agnostic way. The current
hardcoded Dutch prompt (line 187) MUST be replaced with one of:

- A single English judge prompt that takes the query language as a
  parameter and instructs the judge to evaluate language-correctness
  given that parameter (preferred — single judge prompt covers all
  languages).
- One judge prompt per supported language, dispatched on
  query-language detection (acceptable but more code to maintain).

The judge MUST score `language_correctness` as a Boolean (1 = response
language matches query language, 0 = mismatch) per response, in
addition to the existing scalar metrics.

### REQ-RAG-MULTILINGUAL-CHAT-001-05 — Pre-merge eval gate

Before REQ-01 + REQ-02 land in main, the cross-lingual eval-suite from
REQ-03 MUST be merged first and a baseline measurement MUST be
recorded. After REQ-01 + REQ-02 land, the same eval MUST be re-run.

The eval-suite is **not** scheduled (no cron, no per-PR CI gate). It
is run **manually** at three moments only:
1. Once during Phase 1, to record the pre-change baseline scorecard.
2. Once during Phase 2, to verify the post-change gate passes.
3. Ad-hoc on later "large changes" — defined as any change to
   `synthesis.py`, `partner_chat.py`, the shared
   `klai-libs/chat-prompts` constant, the embedding model, or the
   reranker. Smaller diffs (typo fixes, refactors that don't touch
   prompt or model wiring) skip the eval.

Run cost (~10-15 min wall clock + LiteLLM token cost) is the trade-off
for skipping per-PR gating.

The post-change eval MUST satisfy:

| Metric | Baseline (NL/EN only) | Post-change requirement |
|---|---|---|
| `language_correctness` per language (NL/EN/DE/FR/PT/ES) | n/a | ≥ 95% per language |
| Faithfulness on existing NL/EN test set | record at REQ-03 merge | ≥ baseline − 0.02 |
| Answer-relevance on existing NL/EN test set | record at REQ-03 merge | ≥ baseline − 0.02 |
| Citation-correctness on existing NL/EN test set | record at REQ-03 merge | ≥ baseline − 0.02 |

If any post-change metric fails the requirement, REQ-01 + REQ-02 MUST
NOT merge as-is. The implementer MUST either revise the system prompt
(REQ-01) until the gate passes, or remove the failing language from
the target list (and document the removal in HISTORY). There is no
per-tenant or per-language model-escalation escape valve in this SPEC.

### REQ-RAG-MULTILINGUAL-CHAT-001-06 — Tenant isolation invariants

Both prompt rewrites (REQ-01, REQ-02) MUST honour existing tenant
isolation:

- The system prompt is global per service; no org-id leakage between
  tenants is possible because the prompt does not carry tenant context.
- No new Qdrant or Postgres queries are introduced that would require
  separate tenant-isolation review beyond the existing pattern.
- No new per-tenant configuration columns are added to `portal_orgs`
  or any other tenant-scoped table.

### REQ-RAG-MULTILINGUAL-CHAT-001-07 — Observability

The system MUST emit a structured log event from synthesis.py and
partner_chat.py for every chat completion containing:

- `event` = `"chat_synthesis_complete"`
- `query_language_detected` (the LLM's implicit detection, captured by
  asking the LLM to prefix its reasoning OR by a passive `lingua` check
  on the user's last message — implementer's choice; the second is
  cheaper and good enough for observability)
- `response_language_detected` (passive `lingua` check on the response)
- `language_correctness` (Boolean: detected query lang == detected
  response lang)
- Existing fields: `request_id`, `org_id`, `chunk_count`,
  `latency_ms`

The logs MUST flow into VictoriaLogs via the existing pipeline
(structlog → Alloy → VictoriaLogs). A new Grafana panel SHALL be added
to the existing chat-monitoring dashboard tracking weekly
`language_correctness` rate per detected query language.

The observability fields are observable-only — they MUST NOT change
behaviour. A `language_correctness=False` event does not retry, does
not switch models, does not surface to the user. It exists for trend
detection.

### REQ-RAG-MULTILINGUAL-CHAT-001-08 — No regression on NL/EN behaviour

The post-change behaviour for existing NL/EN users MUST be
indistinguishable from the pre-change behaviour to the user. This is
verified by REQ-05's metrics-baseline check (faithfulness,
answer-relevance, citation-correctness within ±0.02 of baseline) and
by an explicit acceptance scenario (see `acceptance.md` AC-NL-NORM and
AC-EN-NORM).

The exception is the explicit Dutch sentence in the system prompt,
which is removed — but its functional effect (NL questions get NL
answers) MUST be preserved by the new auto-detect prompt.

### REQ-RAG-MULTILINGUAL-CHAT-001-09 — Documentation

`docs/architecture/knowledge-ingest-flow.md` § retrieval / synthesis
section MUST be updated to describe the new auto-detect chat
behaviour. The documentation MUST include:

- The three guards (minimum length, single-foreign-word, substantive
  switch).
- Note that bge-m3 already handles cross-lingual retrieval — the chat
  layer is the only multilingual change.
- Reference to this SPEC ID.

The Klai rules file
`.claude/rules/klai/projects/knowledge.md` (or
`knowledge-ingest.md`) MUST be updated with a new pitfall entry
"system prompt language assumptions" warning future implementers
against re-introducing language allow-lists in chat prompts.

---

## Non-functional requirements

### Performance

- Prompt change: zero measurable latency impact (same number of input
  tokens, same model).
- Eval-suite expansion: per-language eval run completes in same wall
  time as current NL/EN run scaled linearly with query count
  (sequential model calls dominate; trivially parallelisable later).
- Per-tenant override (if implemented): adds one extra DB read per
  chat request from portal-api, with a small in-process cache
  (~30s TTL, same pattern as KB-scope cache) to keep amortised cost
  near zero.

### Security

- No new authentication paths.
- No new external network calls.
- No new SOPS secrets.
- Per `secret-fail-closed-on-empty`: the optional
  `synthesis_model_override` field MUST be a model-name string that is
  validated against an allowlist (`klai-fast`, `klai-smart`, future
  additions). Empty string and unknown values MUST be rejected with a
  422 at write time and treated as NULL (default model) at read time.
  No silent fallback to a privileged model.

### Reliability

- LLM cross-lingual generation can fail (return mixed-language text or
  English when asked for Spanish). The eval-suite is the detection
  mechanism. The user-visible mitigation is REQ-06 model escalation,
  triggered manually after eval reveals a failure.
- Reverting REQ-01 + REQ-02 is a one-PR rollback. Reverting REQ-06 is
  setting all `synthesis_model_override` rows to NULL and reverting
  the portal-api / retrieval-api wiring.

### Observability

- All synthesis outcomes log `language_correctness` per REQ-08.
- Existing `request_id` propagation continues unchanged.
- Existing chat-completion latency / token-count metrics continue
  unchanged.

---

## Success criteria

1. The cross-lingual eval-suite (REQ-03) is committed and a baseline
   measurement on the existing NL/EN test data is recorded.
2. REQ-01 + REQ-02 are merged with REQ-05's post-change eval reporting
   `language_correctness ≥ 95%` for all six target languages and
   faithfulness/answer-relevance/citation-correctness within ±0.02 of
   the NL/EN baseline.
3. A test query in DE asking about a Dutch-source topic returns a
   coherent German answer with `[n]` citations pointing to Dutch
   sources. Same for FR, PT, ES, EN.
4. A NL conversation with a stray "thanks!" message in EN does NOT
   cause the next answer to switch to English.
5. A NL conversation with an unambiguous full-sentence German question
   in the middle DOES cause the next answer to switch to German and
   subsequent substantive German messages stay in German.
6. The `language_correctness` Grafana panel shows ≥ 95% for all
   target languages over a rolling 7-day window after launch
   (sourced from per-completion observability logs from REQ-07,
   not from the eval-suite).
7. `docs/architecture/knowledge-ingest-flow.md` describes the new
   auto-detect behaviour and references this SPEC.
8. The pitfall entry in `.claude/rules/klai/projects/knowledge.md` (or
   `knowledge-ingest.md`) is in place and discoverable.
9. A follow-up GitHub issue is open tracking the deferred
   multilingual coverage of default chat templates
   (`default_templates.py`) and taxonomy category names
   (`clustering_tasks.py`). These are explicitly out of scope here
   and owned by the product team.
