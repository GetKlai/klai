# Research: SPEC-RAG-MULTILINGUAL-CHAT-001

> Persistent research artifact backing the multilingual-chat SPEC.
> Generated as part of `/moai plan` on 2026-05-06.

## Trigger

User has Dutch knowledge bases (voys/support, getklai/handbook) but
colleagues in DE, ES, UK, ZA, FR, and PT who want to use the same
Klai chat interface in their own language. Target languages: NL
(covers Vlaams), EN (UK + ZA — South Africa team works in English at
work; Afrikaans not targeted), DE, FR (covers Waals + future French-
speaking tenants), PT, ES. Sources may stay Dutch (and the existing
English-language vendor docs); the chat answer layer must become
language-agnostic.

## Question framing

> Can a Dutch-corpus knowledge base serve queries in DE / ES / EN /
> Afrikaans without re-ingesting, re-embedding, or splitting collections?
> If yes, where exactly is the bottleneck today?

## Validated findings

### Finding 1: Retrieval is already cross-lingual

The current ingest pipeline embeds documents with `BAAI/bge-m3` (TEI on
gpu-01, port 7997 — see
`klai-knowledge-ingest/knowledge_ingest/embeddings.py` and
`docs/architecture/knowledge-ingest-flow.md`). bge-m3 was specifically
designed as a multilingual embedding with a unified space across 100+
languages.

Empirical reference: M3-Embedding paper (arXiv 2402.03216) reports
**75.5% Recall@100 on the MKQA cross-lingual benchmark**, beating
OpenAI's text-embedding-large at the time of the paper. A Spanish or
German query already lands in the same vector space as a Dutch chunk
with related meaning.

A 2024 multilingual-RAG study (arXiv 2407.01463) explicitly states
*"BGE-m3 enables reliable retrieval in the cross-lingual scenario"* and
shows it outperforms a translate-then-retrieve baseline (SPLADE +
translation). This means a query-translation layer is **not** required
and would in fact regress retrieval quality.

**Conclusion**: no changes needed to the ingest pipeline, the embedding
model, the Qdrant collection, or the FalkorDB graph. The vector store
is already polyglot.

### Finding 2: The chat synthesis prompt is the actual bottleneck

The blocker is in the chat answer layer, not retrieval. Two prompt
locations both hardcode an NL/EN switch with **identical** wording:

#### Location A: retrieval-api synthesis service

`klai-retrieval-api/retrieval_api/services/synthesis.py:16-33`

```python
_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    ...
)
```

This prompt is used by the streaming-synthesis `/synthesize` endpoint
that LibreChat / klai-knowledge-mcp call directly.

#### Location B: portal-api partner_chat service

`klai-portal/backend/app/services/partner_chat.py:44-61`

```python
_GROUNDED_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    ...
)
```

Same string. Used by the partner-chat completion path
(SPEC-API-001 TASK-008/009) — the API surface third-party partners hit.

**Implication for SPEC scope**: REQ-1 (system-prompt rewrite) MUST
update both files in lockstep. Updating one and not the other leaves a
half-fix that surfaces only when a non-NL/EN user tries the partner-chat
endpoint. This is the `search-broadly-when-changing` rule applied: a
default-string change has unbounded blast radius and both copies are
"defaults" of the same product behaviour.

### Finding 3: Adjacent prompts are already language-agnostic

These were verified during the research grep and need no change:

| File | Status |
|---|---|
| `klai-retrieval-api/retrieval_api/services/coreference.py:18` | "Keep the same language as the input query" — agnostic ✓ |
| `klai-portal/backend/app/services/summarizer.py:5,45` | Meeting summarizer already takes language from user message ✓ |

### Finding 4: Document enrichment has narrower coverage but acceptable defaults

`klai-knowledge-ingest/knowledge_ingest/contextual.py` has dedicated
prompt templates only for NL and EN (`_SUMMARY_PROMPT_NL`,
`_SUMMARY_PROMPT_EN`) with `DEFAULT_PROMPT_LANGUAGE = "en"` fallback.
The `lingua` detector itself supports NL / EN / DE / FR / ES.

When a Spanish document is ingested today, its enrichment summary is
generated in English (fallback) but the actual chunk text remains
Spanish, and bge-m3 still embeds it correctly. The summary mismatch
shows up only inside the contextual-prefix that prepends each chunk
during embedding — a quality drag, not a correctness break.

**Decision (out of scope for V1)**: leave the enrichment templates as
NL + EN with EN fallback. Add DE/ES enrichment templates only if a
post-Phase-2 eval shows a measurable retrieval-quality regression for
non-NL/EN documents. A new tenant ingesting primarily DE or ES content
would be the right trigger; for now, all non-trivial corpora are still
NL.

### Finding 5: Hardcoded Dutch in adjacent paths

| File | Issue | In scope? |
|---|---|---|
| `klai-knowledge-ingest/knowledge_ingest/clustering_tasks.py:280-287` | Taxonomy category-name suggestions hardcoded to Dutch | No — admin-internal labels, not user-facing chat |
| `klai-retrieval-api/evaluation/cross_lingual_runner.py:187` | "Dutch RAG prompt" comment + NL-only LLM-as-judge | **Yes** — REQ-2 covers this |
| `klai-retrieval-api/scripts/generate_gate_reference.py:35,44` | Reference-set generator: "Generate 50% in Dutch and 50% in English" | **Yes** — REQ-2 covers this |
| `klai-portal/backend/app/services/default_templates.py:40` | Seed template "Klantenservice" hardcoded NL prompt | No — user-editable product content owned by product team (per `@MX:NOTE` in file) |

## Industry-pattern validation (web research)

The user explicitly asked for industry-standard validation of the
mid-conversation language-switching choice. Six sources triangulated:

### Source 1: Invent — Multilingual AI Agents 2025 best-practices guide
URL: https://www.useinvent.com/blog/how-to-build-effective-multilingual-ai-agents-2025-best-practices-guide

Direct quote: *"Detect language per message, not just per session ...
treating language as a per-message property rather than a session-level
constraint, prioritizing user autonomy over simplicity."*

Concrete pattern recommended:
1. Detect per message
2. Optionally confirm on switch ("I see you switched to French ...")
3. Allow user reset to preferred language

### Source 2: Quickchat — Multilingual Chatbots 2026 guide
URL: https://quickchat.ai/post/multilingual-chatbots

Direct quote: *"One foreign phrase should not flip the whole chat, and
short messages like 'ok gracias' are a common failure case for language
detection, so a confidence threshold and minimum message length should
be used before switching languages."*

This is the **guard-rail** that turns "match every message" from a brittle
pattern into a robust one.

### Source 3: Multilingual RAG paper (ACL Findings EACL 2026)
URL: https://arxiv.org/html/2407.01463v1

Direct quote: *"Without explicit instructions, models frequently respond
in English even for non-English queries — particularly when retrieving
from English sources."*

In Klai's case the bias is the inverse (Dutch sources, non-Dutch query)
but the lesson is the same: **the system prompt MUST explicitly steer
the answer language**, otherwise the LLM will drift toward the source
language. Documented failure modes in non-source-language generation:

- Code-switching mid-answer (especially in non-Latin scripts)
- Named-entity transliteration mistakes (proper names becoming literal
  translations)
- Fluency dips in lower-resource languages
- Wrong reading of the source document

### Source 4: BGE-M3 paper
URL: https://arxiv.org/abs/2402.03216

Confirms: 100+ languages in shared embedding space; SOTA on multilingual
+ cross-lingual + long-document benchmarks.

### Source 5: XRAG benchmark
URL: https://arxiv.org/html/2505.10089v1

Confirms a community-standard metric: **language correctness** = % of
responses where the response language matches the query language. We
adopt this as REQ-2 metric.

### Source 6: BAAI/bge-m3 model card on Hugging Face
URL: https://huggingface.co/BAAI/bge-m3

Confirms model is in active production use for cross-lingual retrieval;
no migration alternative under consideration.

## Synthesis: industry standard is a hybrid, not a binary

The naive choice is binary: "match every message" vs "lock per
conversation". Both are wrong. Production chatbots (ChatGPT, Claude,
Gemini, plus the practitioner sources above) use **per-message
detection with three guards**:

1. **Minimum message length** — short messages (e.g. <5 words) inherit
   the language of the prior longer message. "thanks!", "merci",
   "ok gracias" are not language switches.
2. **Confidence threshold** — single foreign words inside an otherwise
   consistent-language message do not switch. "Send me die info
   asap." is still NL.
3. **Substantive switch** — a full-sentence question in a different
   language IS a switch and stays switched until another substantive
   switch.

ChatGPT and Claude do NOT add an explicit confirmation step ("I see you
switched to French — continue in French?"). For an internal team tool
like Klai, that confirmation friction is unnecessary; we follow the
ChatGPT/Claude convention.

## Architectural decision: shared prompt library, not platform

The two-prompt-locations problem (Finding 2) raised a follow-up
question: where does the canonical prompt live?

Options considered:

1. **Inline duplication in both services**, kept in sync by a CI lint
   that asserts byte-equality.
2. **Shared sub-library in `klai-libs/`**, imported by both services.
3. **Prompt-management platform** (Langfuse, PromptLayer, Mirascope
   Lilypad, LangChain Hub).

Industry validation (web research):

- Mirascope's 2025 prompt-versioning guide and PromptLayer's 2025 tool
  comparison both treat shared library extraction as the baseline
  pattern for cross-service prompts.
- Prompt-management platforms (Langfuse, PromptLayer) become valuable
  when you have ≥10 prompts, multiple teams editing them, or A/B
  testing requirements. They are explicit overkill for ≤2 prompts
  used by ≤2 services.
- The cited 47billion.com "From Prompt Chaos to Production" piece
  argues the same: start with code-as-source, migrate to a platform
  only when prompt count or operational complexity demands it.

Klai's situation: 1-2 prompts, 2 services, small team, all already
share `klai-libs/` for cross-service code (6 sibling sub-libraries:
connector-credentials, identity-assert, image-storage, log-utils,
service-auth, webhook-replay). Adding `klai-libs/chat-prompts/` is the
natural fit and matches the established klai pattern.

Decision: **option 2 (shared sub-library)**. Specified in REQ-02 of
the SPEC.

Rejected alternatives:

- Option 1 (inline + CI lint) was the V0 plan. Rejected because the
  CI lint is brittle (any whitespace edit breaks it) and a third
  service joining the chat surface in the future would re-introduce
  drift unless explicitly prevented at the same lint layer. A shared
  library makes new consumers cheap (one import) and impossible to
  forget (it's the only path).
- Option 3 (Langfuse / PromptLayer) deferred to a future SPEC if
  prompt count grows.

## Architectural decision: detection lives in the system prompt

A modern frontier LLM (Mistral Small / klai-fast and klai-smart) can
perform language detection inline as part of generation. The three
guards above can be expressed as natural-language instructions in the
system prompt itself.

Rejected alternative: a separate `lingua`-based detection layer in the
retrieval-api / portal-api request path, attaching a `query_language`
parameter to the synthesis request.

Rationale for rejection:
- Adds a new dependency on `lingua-language-detector` to portal-api
  (currently only in knowledge-ingest)
- Introduces a second source of truth that can disagree with the LLM
- Short-message and code-switching guards are harder to express as
  Python rules than as prompt instructions
- LLM has the full conversation history; `lingua` only sees the last
  message
- Extra hop adds 5-15ms latency for no quality gain

## Risk assessment

### Low-risk

- System prompt change in two files (~15 lines each)
- Same code path, same models, same retrieval pipeline
- Reversible by reverting two files

### Medium-risk

- Cross-lingual answer-quality is LLM-dependent. Mistral Small
  (klai-fast) is documented strong in EN/DE/FR, weaker in ES, weak in
  Afrikaans. Without measurement we don't know which tenants will need
  klai-smart escalation.
- Mitigation: REQ-2 (eval-suite expansion) lands BEFORE REQ-1 so we
  measure baseline + post-change scores per language. REQ-3
  (per-tenant model-tier) is the escape valve if a language scores
  below 95% language-correctness or below acceptable faithfulness.

### Risks NOT addressed in this SPEC

- **Frontend i18n**: portal UI labels are NL-only outside the existing
  Paraglide/Inlang-managed strings (which already supports DE per
  `product.md`). User reports that ZA prefers EN; portal UI for new
  tenants will need EN/DE coverage. **Out of scope** — separate
  frontend SPEC.
- **Mailer templates**: `klai-mailer/app/renderer.py` is per-locale via
  `portal_api_url` lookup. Not affected by this SPEC.
- **Default chat templates** (`default_templates.py`): hardcoded NL,
  but these are user-editable product content with explicit @MX:NOTE
  warning against autonomous edits. **Out of scope** — product team
  decision.

## Reference implementations identified

For REQ-1 (system prompt structure):

- ChatGPT system prompt patterns from Anthropic prompt-engineering docs
  (cited in research search results)
- Claude system prompt for chat with retrieval (matches Anthropic
  contextual-retrieval pattern that Klai already uses for enrichment
  via SPEC-RAG-CONTEXTUAL-001)

For REQ-2 (cross-lingual eval):

- XRAG benchmark methodology (arXiv 2505.10089)
- BordIRlines benchmark (referenced in 2026 Multilingual RAG ACL paper
  for cultural-sensitivity test cases)
- Existing `klai-retrieval-api/evaluation/` patterns
  (judge_client + reference generator) — this SPEC extends, does not
  replace

For REQ-3 (per-tenant model override):

- Existing `portal_orgs.enabled_addons` pattern from
  SPEC-PORTAL-PROFILES-001 (per-tenant feature flag)
- Existing `portal_users.language` field at `/api/me/language` (read for
  context only — auto-detect remains source of truth)

## Out-of-scope explicitly enumerated

To prevent scope creep during implementation:

- No ingest pipeline changes
- No embedding model changes (bge-m3 stays)
- No new vector collections
- No taxonomy changes
- No knowledge-graph (FalkorDB) changes
- No reranker changes
- No portal frontend changes
- No mailer template changes
- No chat template (default_templates.py) content changes
- No new auth paths
- No new SOPS secrets
- No query-translation layer

## Estimated scope

| File | Change type | Approximate lines |
|---|---|---|
| `klai-libs/chat-prompts/` (new sub-library) | Single source of truth for prompt | ~80 lines (pyproject + module + test) |
| `klai-retrieval-api/retrieval_api/services/synthesis.py` | Remove `_SYSTEM_PROMPT`, import from new library | ~15 lines replaced |
| `klai-portal/backend/app/services/partner_chat.py` | Same removal + import | ~15 lines replaced |
| `klai-retrieval-api/pyproject.toml` + `klai-portal/backend/pyproject.toml` | Wire new dep | ~2 lines each |
| `klai-retrieval-api/evaluation/cross_lingual_runner.py` | Add multilingual judge prompt support | ~50 lines |
| `klai-retrieval-api/scripts/generate_gate_reference.py` | Extend reference generation to all six languages | ~30 lines |
| `klai-retrieval-api/evaluation/` (new file) | Cross-lingual test set | ~120 lines (data) |
| Grafana dashboard JSON | New language_correctness panel | ~30 lines |
| CI lint (ast-grep rule or shell script) | Catch prompt re-duplication in future services | ~10 lines |

Total estimated diff: ~350 lines across ~9 files. Two PRs: Phase 1
(eval-suite) first, Phase 2 (system prompt + shared library) second.
No Phase 3.
