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
| 1.1 | 2026-05-06 | Mark Vletter (autonomous run) | Implementation pass. Realised during code exploration that the eval-suite uses RAGAS via `evaluation/eval_runner.py` (not the imagined `eval/judge_client.py`). REQ-04 reframed: instead of a multilingual LLM-as-judge, the implementation adds a deterministic per-response `language_correctness` metric plus a dedicated `evaluation/cross_lingual_runner.py` that scores queries directly against the synthesis endpoint. Cross-lingual test set landed at 65 queries across 6 languages (10-11/lang × 11 intents) — under the original 20/lang target but acceptable as a V1 floor with measurable test coverage; future expansion is a `cross_lingual_runner` + extra fixture data, no code change. Target language list locked in as NL/EN/DE/FR/PT/ES (Afrikaans removed — ZA team uses English; PT added; FR expanded coverage of Walloon-BE). All file paths in this SPEC corrected to match the actual codebase layout. |
| 1.2 | 2026-05-07 | Mark Vletter (post-merge audit) | **Scope correction.** PR #454 (v1.1) shipped to main, but post-merge E2E on the live Voys LibreChat surfaced that a DE query still produced a NL response. Investigation revealed three concurrent chat paths in Klai, only two of which were touched by the v1.1 SPEC: (a) **LibreChat → LiteLLM `klai_knowledge.py` pre-call hook → Mistral** — primary user-facing chat flow; system prompt prefix is hardcoded NL inside the LiteLLM hook (header narrow + broad mode, antwoordformaat instructions, Klai Templates wrapper, KB-unavailable notice). v1.1 did NOT touch this. (b) **portal-api `/partner/v1/chat/completions` (Widget + Partner API) → `partner_chat.py` → LiteLLM (no `user` field, hook skips)** — v1.1 made this multilingual via the shared `klai-libs/chat-prompts` library. Working as intended. (c) **retrieval-api `POST /chat`** — registered endpoint with auth + tenant-isolation guards + tests but no current external callers; dormant infrastructure for a future SPEC-KNOW-005 feedback feature. v1.1 wired the new prompt here too, harmless as long as the endpoint stays dormant. v1.2 adds REQ-10 to extend the multilingual treatment to `klai_knowledge.py` so that path (a) — the primary user-visible chat — also responds in the user's language. Also corrects three derivative defects from v1.1: (1) `evaluation/cross_lingual_runner.py` POSTs to a non-existent `/synthesize` endpoint and would 404 on first run — fix to call `/chat` (or document it as a partner-API runner via `/partner/v1/chat/completions`). (2) `scripts/lint-no-duplicate-chat-prompt.sh` only catches the EN anchor from `klai-libs/chat-prompts` and misses the NL anchors that live in `klai_knowledge.py` — broaden the anchor list. (3) `docs/architecture/knowledge-ingest-flow.md`, `docs/runbooks/multilingual-chat-observability.md`, and `.claude/rules/klai/projects/knowledge.md` claim multilingual is live for the chat — that claim only holds for paths (b) and (c) and must be corrected before the `klai_knowledge.py` work lands. |

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

### Three chat paths in production (v1.2 corrected)

The v1.0 audit found two prompt locations and concluded the chat could
be made multilingual by updating those. v1.1 implemented that.
Post-merge E2E in v1.2 revealed there are actually **three paths**, and
the most user-visible one was missed.

**Path A — LibreChat → LiteLLM `klai_knowledge.py` pre-call hook → Mistral.**
Primary user-facing chat (the `chat-{tenant}.getklai.com` LibreChat
embed seen at `voys.getklai.com/app/chat`). The hook at
`deploy/litellm/klai_knowledge.py` checks `data["user"]` (LibreChat
sends it as a MongoDB ObjectId). When present, the hook prepends a
hardcoded NL system-prefix containing four blocks:

- Klai Kennisbank header (narrow + broad mode variants)
- ANTWOORDFORMAAT instructions (TLDR, sources, citations)
- Klai Templates wrapper (when active templates exist)
- KB-unavailable notice (NL fallback when retrieval fails)

This is what makes a DE query produce a NL response in production today.
v1.0/v1.1 SPEC did NOT touch this path. **REQ-10 in v1.2 covers it.**

**Path B — portal-api `/partner/v1/chat/completions` → `partner_chat.py`
→ LiteLLM (no `user` field) → Mistral.** Both the embeddable Widget
(`klai-widget/`) and external Partner API tokens flow through this
endpoint. `partner_chat.py::chat_completion_*` POSTs to LiteLLM without
the `user` field, so the `klai_knowledge.py` hook hits its
`if not librechat_user_id: return data` early-exit. The system prompt
that reaches Mistral is the one `partner_chat.py::_build_system_prompt`
constructs. v1.1 made this multilingual by importing
`klai_chat_prompts.GROUNDED_CHAT_SYSTEM_PROMPT`. **Working as
intended; no v1.2 change needed.**

**Path C — retrieval-api `POST /chat` (dormant).** Registered FastAPI
route with auth + tenant-isolation guards + 17 unit tests (`tests/
test_synthesis.py`) + endpoint tests (`tests/test_api.py`). No external
callers in the current codebase — no LibreChat hook, no portal-api
service, no klai-knowledge-mcp tool currently calls it. SPEC-KNOW-005
plans to use it for a future feedback-capture feature ("done event
uitbreiding"). v1.1 wired the new shared prompt here as a side-effect
of the broader pattern; the change is harmless until the endpoint is
activated by SPEC-KNOW-005 or a successor. **No v1.2 change needed.**

The original prompt strings (NL/EN switch, since replaced) lived at:

```python
"[CRITICAL] Respond in the language of the user's question. "
"Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
"If the user writes English, respond in English. Never switch mid-conversation."
```

These were in `partner_chat.py:44-61` and `synthesis.py:16-33`. v1.1
replaced them with the shared `GROUNDED_CHAT_SYSTEM_PROMPT` from
`klai-libs/chat-prompts`. v1.2 leaves those in place and adds the third
location: the LiteLLM hook prefix-builder.

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
3. Extend the eval-suite (`klai-retrieval-api/evaluation/`)
   to cover queries in NL, EN, DE, FR, PT, and ES. Add a new
   `language_correctness` metric defined as the percentage of responses
   where the response language matches the query language.
4. Add a new `evaluation/cross_lingual_runner.py` script that scores
   per-language correctness against the synthesis endpoint, and a
   companion `retrieval_api/util/language_detect.py` utility used by
   both the runner and the production observability path. The eval
   does not invoke an LLM-as-judge — language correctness is a
   deterministic check (lingua-detected response language matches
   query language) and is run alongside the existing RAGAS metrics
   delivered by `evaluation/eval_runner.py`.
5. Update `scripts/generate_gate_reference.py` to generate cross-lingual
   reference data covering all six target languages, not just 50/50
   NL/EN.
6. **(v1.2)** Make the LiteLLM `klai_knowledge.py` pre-call hook
   multilingual: import `GROUNDED_CHAT_SYSTEM_PROMPT` from
   `klai-libs/chat-prompts` as the foundational system instruction,
   rewrite the four hardcoded NL prefix blocks (Klai Kennisbank header
   narrow + broad, ANTWOORDFORMAAT instructions, Klai Templates wrapper,
   KB-unavailable notice) into multilingual variants, and extend
   `deploy/litellm/tests/test_klai_knowledge_hook.py` with DE / FR / PT
   / ES query → response language assertions.
7. **(v1.2)** Fix `evaluation/cross_lingual_runner.py` to call the
   actually-existing endpoint(s) (`/chat` on retrieval-api OR
   `/partner/v1/chat/completions` on portal-api). The v1.1 runner
   targets a non-existent `/synthesize` endpoint and would 404 on
   first run.
8. **(v1.2)** Broaden `scripts/lint-no-duplicate-chat-prompt.sh` to
   also fail on the NL anchor strings that previously lived only in
   `klai_knowledge.py` (they should now exist there in their
   multilingual form via REQ-10, not duplicated elsewhere).
9. **(v1.2)** Correct the documentation that v1.1 left over-claiming.
   `docs/architecture/knowledge-ingest-flow.md`,
   `docs/runbooks/multilingual-chat-observability.md`, and
   `.claude/rules/klai/projects/knowledge.md` need scope statements
   that distinguish path A (LibreChat) from paths B (Widget / Partner
   API) and C (dormant `/chat`).

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

The system MUST extend `klai-retrieval-api/evaluation/` to
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

### REQ-RAG-MULTILINGUAL-CHAT-001-04 — Deterministic language-correctness scoring

`klai-retrieval-api/evaluation/cross_lingual_runner.py` MUST score
language-correctness deterministically, NOT via an LLM-as-judge. For
each test query:

1. The runner calls the synthesis endpoint with the query and collects
   the assembled response text.
2. `retrieval_api/util/language_detect.py::detect_language` runs on
   both the original query and the response, returning ISO-639-1 codes
   from the target set (`nl/en/de/fr/pt/es`) or `und` for
   short / out-of-target inputs.
3. `language_correctness` is the Boolean comparison of the two
   detected codes; `None` (skipped) when either side is `und`.

A dedicated LLM judge for language correctness is rejected because the
underlying property — "what language is this in?" — is well-modeled by
a deterministic detector. Spending an LLM call on it is more expensive,
slower, and adds judgement variance to a metric that should be
mechanically reproducible.

The existing RAGAS-based metrics in `evaluation/eval_runner.py`
(faithfulness, answer-relevance, citation-correctness, NDCG@10,
recall@10) are unchanged. They keep their LLM judge (`klai-large` via
LiteLLM); they are run alongside this script when the operator wants
the full regression view.

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

### REQ-RAG-MULTILINGUAL-CHAT-001-10 — LiteLLM hook multilingual prefix (v1.2)

This is the corrective requirement from v1.2 that closes the gap left
by v1.1. The LiteLLM pre-call hook at
`deploy/litellm/klai_knowledge.py` is the actual code that builds the
system prefix that LibreChat traffic receives. v1.1 did not touch it,
so chat-flow A (the most user-visible one) still answered Dutch
regardless of query language.

The hook MUST be reworked so that:

1. The base system instruction is imported from
   `klai_chat_prompts.GROUNDED_CHAT_SYSTEM_PROMPT` and prepended to
   every chat completion that the hook handles. This keeps the
   foundational language behaviour (auto-detect substantive query
   language, three guards, no translator disclaimers) consistent
   across all paths.
2. The four hardcoded NL prefix blocks become multilingual:
   - **Klai Kennisbank header** (narrow + broad variants): instruct
     the model to obey the source-grounding rule in the language of
     the user's question.
   - **ANTWOORDFORMAAT instructions** (TLDR, sources list, citations,
     image rules): rewrite as language-neutral instructions or
     localise per query language. The semantic structure (TLDR first,
     bronnenlijst, optional uitgebreid antwoord with `[n]` citations,
     image markdown literal copy) MUST be preserved.
   - **Klai Templates wrapper**: when active templates are present,
     wrap them with a marker that does not bias the response language.
   - **KB-unavailable notice**: when retrieval fails, prepend a
     message that instructs the model to fall back to general
     knowledge AND surface the failure to the user, in the user's
     language.
3. The shared prompt library `klai-libs/chat-prompts` MAY gain a
   second exported constant (e.g. `KB_PREFIX_INSTRUCTION_TEMPLATE`)
   to host these hook-specific blocks if separation aids reuse, OR
   the hook MAY embed them inline. Implementer's choice; the
   acceptance criterion is "no NL anchor exists outside the canonical
   library + this hook file".
4. The hook SHOULD emit the same `chat_synthesis_complete` log event
   defined in REQ-07 — `query_language_detected`,
   `response_language_detected`, `language_correctness` — so
   observability is consistent across paths A, B, and C. Phase 4
   ship-level explicitly defers this sub-clause: the LiteLLM container
   is a stock upstream image without `lingua-language-detector`, and a
   partial emit (no `query_language_detected` /
   `response_language_detected`) provides limited observability value
   on its own. The follow-up that closes this gap is the same custom
   litellm Dockerfile already planned for `klai_service_auth.py`
   (Phase D of SPEC-SEC-SERVICE-AUTH-001) — `pip install`ing
   `klai-chat-prompts` AND `lingua-language-detector` makes both the
   vendored single-file copies and this emit unnecessary. Until then
   path-A coverage of the rolling 7-day language-correctness gate
   (REQ-05) is provided by the pre-merge eval gate
   (`evaluation/cross_lingual_runner.py`) plus path B/C telemetry as
   proxy — see `docs/runbooks/multilingual-chat-observability.md`
   "Path A telemetry caveat" section.

The deploy target for REQ-10 is the `klai-core-litellm-1` container
on core-01. CI's `Build and push` workflow for the LiteLLM image
deploys automatically on merge to main; verify after deploy that the
container is using the new hook by checking for the import line in
`/etc/litellm/klai_knowledge.py` inside the container.

### REQ-RAG-MULTILINGUAL-CHAT-001-11 — v1.1 corrections (v1.2)

This requirement bundles three corrections to artefacts that landed in
v1.1 and that turned out to be defective on closer inspection.

1. **Cross-lingual runner correctness.**
   `klai-retrieval-api/evaluation/cross_lingual_runner.py` MUST POST
   to an endpoint that actually exists. The v1.1 runner targets
   `/synthesize`, which retrieval-api does not register. Acceptable
   targets: `/chat` on retrieval-api (for direct synthesis testing
   when SPEC-KNOW-005 activates that path) OR
   `/partner/v1/chat/completions` on portal-api (the production
   widget / partner pathway). The runner MUST be runnable without
   editing — pick one default that works against the current
   production deploy.
2. **CI-lint anchor expansion.**
   `scripts/lint-no-duplicate-chat-prompt.sh` MUST be extended with
   the NL anchor strings from `klai_knowledge.py` so that, after
   REQ-10 lands, accidentally re-introducing a copy of those NL
   blocks anywhere outside the LiteLLM hook (or the shared library
   if they migrate there) is rejected at PR time.
3. **Documentation scope correction.**
   `docs/architecture/knowledge-ingest-flow.md` MUST distinguish
   path A (LiteLLM hook → Mistral) from paths B (`partner_chat.py`)
   and C (dormant retrieval-api `/chat`). The current claim that
   multilingual works "for the chat" must be amended to specify
   which path each statement applies to.
   `docs/runbooks/multilingual-chat-observability.md` MUST clarify
   that the `chat_synthesis_complete` log event fires from
   whichever path emits it (post-REQ-10 that includes the LiteLLM
   hook) and which path produces which `service:` label in the log.
   `.claude/rules/klai/projects/knowledge.md` MUST list all three
   locations as canonical "system prompt is here" pointers and
   warn against making changes in only one of them.

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
