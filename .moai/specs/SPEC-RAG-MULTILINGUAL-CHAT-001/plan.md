# Plan: SPEC-RAG-MULTILINGUAL-CHAT-001

## Implementation phases

The SPEC has two phases, each landing as one PR. Phase 1 must merge
before Phase 2. There is no Phase 3 — per-tenant model-tier override
was considered and explicitly deferred to a future SPEC.

### Phase 1 — Cross-lingual eval-suite expansion (REQ-03, REQ-04)

**Why first**: we cannot validate REQ-01/02 without a multilingual
eval. Merging Phase 2 first would mean we have no baseline to compare
against, and any cross-lingual quality regression goes undetected.

**Files**:

- `klai-retrieval-api/retrieval_api/eval/judge_client.py` — replace
  hardcoded Dutch judge prompt with language-agnostic version that
  takes `query_language` parameter; add `language_correctness` boolean
  to scored output.
- `klai-retrieval-api/retrieval_api/eval/test_set.py` (new file or
  extension of existing) — define cross-lingual test fixture: 20
  queries per language × 6 languages = 120 queries against the existing
  Dutch reference corpus.
- `klai-retrieval-api/scripts/generate_gate_reference.py` — extend to
  generate reference answers in all six languages, replacing the
  current 50/50 NL/EN behaviour.
- `klai-retrieval-api/retrieval_api/eval/runner.py` (or equivalent
  entry point) — add per-language score aggregation and the
  `language_correctness` metric to the report output.
- `klai-retrieval-api/tests/eval/` — unit tests for the new judge
  client and the language-correctness metric, on synthetic fixtures
  (does NOT call the LLM).

**Out of Phase 1**: any change to synthesis.py or partner_chat.py.

**Acceptance for Phase 1**:
- The eval-suite runs on CI against the existing NL/EN test data and
  produces a baseline scorecard (faithfulness / answer-relevance /
  citation-correctness per language). No regression vs prior eval-suite
  output on the same NL/EN data.
- The new `language_correctness` metric is computable on the existing
  NL/EN data and reports ~100% (because both NL and EN queries
  currently get matching responses under the existing prompt).
- The cross-lingual test set (DE/ES/Afrikaans queries) runs against
  the **current** synthesis prompt and produces the **baseline**
  (likely poor) cross-lingual scores. This is the "before" measurement.

### Phase 2 — System prompt rewrite + shared library (REQ-01, REQ-02, REQ-07, REQ-08)

**Files**:

- `klai-libs/chat-prompts/` (new sub-library) — create a new
  klai-libs sub-package matching the pattern of the six existing ones
  (connector-credentials, identity-assert, image-storage, log-utils,
  service-auth, webhook-replay). Contains:
  - `pyproject.toml`
  - `klai_chat_prompts/__init__.py` exposing
    `GROUNDED_CHAT_SYSTEM_PROMPT` as a module-level constant
  - `tests/` with a single test that asserts the constant is
    non-empty and includes the three guards as substrings
    (regression-guard against accidental strip)
- `klai-retrieval-api/retrieval_api/services/synthesis.py:16-33` —
  remove `_SYSTEM_PROMPT` constant and import from
  `klai_chat_prompts`.
- `klai-portal/backend/app/services/partner_chat.py:44-61` — same
  removal + import.
- `klai-retrieval-api/pyproject.toml` and
  `klai-portal/backend/pyproject.toml` — add the new library as a
  dependency (path-relative or workspace-style, matching how
  existing klai-libs are wired).
- `klai-retrieval-api/retrieval_api/services/synthesis.py` — add
  passive `lingua`-based `language_correctness` log enrichment per
  REQ-07.
- `klai-portal/backend/app/services/partner_chat.py` — same logging.
- `klai-retrieval-api/pyproject.toml` and
  `klai-portal/backend/pyproject.toml` — add `lingua-language-detector`
  if not already present (already in knowledge-ingest, can copy that
  version pin).
- Grafana dashboard JSON in `deploy/grafana/dashboards/` — add panel
  for `language_correctness` rate per detected language.
- A CI lint (ast-grep rule or simple grep in CI script) — verify no
  `klai-*` service contains a hardcoded copy of the prompt's opening
  line. Catches future drift.

**Decision for the prompt body**:

The replacement string MUST contain (English, since system prompts are
in EN per `language.yaml`):

```
[CRITICAL] Detect the language of the user's most recent SUBSTANTIVE
message and respond in that exact language.

Guards:
- Messages with fewer than 5 words inherit the language of the most
  recent prior longer message in the conversation. The first user
  message is always treated as substantive regardless of length.
- Single foreign-language words inside an otherwise consistent-language
  message do not change the response language.
- A clearly switched substantive message DOES switch the response
  language and stays switched until another substantive switch.

You are Klai AI, a knowledge assistant. You answer questions based on
the knowledge base chunks provided. The knowledge base may be in a
different language (often Dutch) — translate cited content into the
user's language naturally. Do not apologize for source-language
differences and do not add translator disclaimers. Citations [n] link
to the original source URL regardless of language.

## How to answer
[remainder of existing prompt unchanged: Start with the answer ...]
```

**Acceptance for Phase 2** — see REQ-05 in spec.md.

### What happens if Phase 2 eval fails

If `language_correctness < 95%` or faithfulness drop > 0.05 for any
target language during Phase 2 eval, the implementer MUST either:

- Revise the system prompt (REQ-01) until the gate passes, or
- Remove the failing language from the target list and document the
  removal as a HISTORY entry on this SPEC.

There is **no per-tenant or per-language model-escalation escape valve
in this SPEC**. Per-tenant model selection was explicitly considered
and deferred to a future SPEC.

## Technology stack and dependencies

No new dependencies are introduced. Existing tooling:

- `lingua-language-detector` already in knowledge-ingest's dependencies
  for document-level detection. Add to retrieval-api `pyproject.toml`
  if REQ-08 chooses passive lingua-based observability detection.
  Adds ~10 MB to the image, deterministic, no external calls.
- LLM-as-judge already runs against the LiteLLM proxy at
  `settings.judge_model`. Same pattern, multilingual prompt.
- Eval reference generation already uses LiteLLM proxy. Same pattern,
  per-language prompts.

## Risk analysis and mitigations

### Risk 1: LLM cross-lingual quality varies per language

Mistral Small (klai-fast) is documented strong in EN/DE/FR (Mistral is
French-trained, FR is native), reasonable in ES, less documented in
PT. There is no public benchmark for klai-fast on cross-lingual RAG
synthesis specifically.

**Mitigation**: REQ-05 measures empirically. If a language fails the
gate, the prompt is revised or the language is removed from the target
list. There is no automatic model-escalation fallback in this SPEC.

**Acceptance test**: a Spanish query against the Dutch corpus produces
a coherent Spanish answer with citation accuracy comparable to the NL
baseline.

### Risk 2: Single-foreign-word guard misclassification

The model may misclassify "asap", "ok", "merci" as language switches.
The system prompt explicitly addresses this in the guards block but
LLMs can drift.

**Mitigation**: REQ-08 logs `language_correctness` per response. A
weekly Grafana panel surfaces drift. A specific acceptance scenario
(AC-MIXED-NL-EN, see `acceptance.md`) tests this case.

### Risk 3: Eval reference set quality

Reference answers for DE/ES/Afrikaans need to be produced. A naive
LLM-generated reference is circular — the same model that generates
references will likely score well against itself.

**Mitigation**: For REQ-03, the reference generator must use a
**different model** than the synthesis model. If synthesis uses
klai-fast (Mistral Small), the reference generator should use
klai-smart (Mistral Large). This is the same pattern as the existing
`generate_gate_reference.py` (which already uses a stronger model).

### Risk 4: Future drift if a third service starts using the prompt

If a future service (e.g. a new chat surface) re-introduces a hardcoded
copy of the prompt instead of importing from `klai-libs/chat-prompts`,
the asymmetric-NL/EN-switch problem returns.

**Mitigation**: REQ-02 mandates a CI lint that grep-scans every
`klai-*` service tree for the prompt's opening line and fails if found
outside the shared library.

### Risk 5: klai-libs sub-library overhead is real

A new sub-library means a new pyproject.toml, a new directory, and one
extra dependency declaration in two consumers' pyproject.toml files.
For a single-string library this is overhead.

**Mitigation accepted**: the overhead is the price for a single source
of truth across services. This matches the existing klai-libs pattern
(`log-utils` is similarly small) and is the industry-standard approach
at klai's scale per the research validation. A prompt-management
platform like Langfuse is the alternative for 10+ prompts or A/B
testing — explicitly deferred.

## Methodology

Klai uses TDD per `quality.development_mode`. Each phase follows
RED-GREEN-REFACTOR:

- **RED**: write the eval-suite tests (Phase 1) or characterization
  tests on the new prompt behaviour (Phase 2). Verify failures.
- **GREEN**: minimal implementation that passes.
- **REFACTOR**: clean up duplication (Phase 2 prompt extraction
  decision lands here).

All phases pass through `manager-quality` for TRUST 5 validation
before sync.

## @MX tag plan

Files where @MX annotations are appropriate after implementation:

| File | Tag | Reason |
|---|---|---|
| `klai-libs/chat-prompts/klai_chat_prompts/__init__.py` | `@MX:ANCHOR` on `GROUNDED_CHAT_SYSTEM_PROMPT` | High fan_in: imported by retrieval-api and portal-api; any change affects all chat behaviour platform-wide |
| `klai-retrieval-api/retrieval_api/services/synthesis.py` | `@MX:NOTE` on the import | Points future readers to the shared library as source of truth |
| `klai-portal/backend/app/services/partner_chat.py` | `@MX:NOTE` on the import | Same reasoning |
| `klai-retrieval-api/retrieval_api/eval/judge_client.py` | `@MX:NOTE` on the multilingual judge prompt | Magic constants for language codes; future translators must update both the test set and this dispatch |

@MX:REASON sub-lines must follow the @MX TAG protocol per
`.claude/rules/moai/workflow/mx-tag-protocol.md`.

## Reference implementations

- Existing system prompts in the codebase (NL/EN dispatch pattern in
  `synthesis.py`, agnostic pattern in `coreference.py`) — REQ-01 follows
  the agnostic style of `coreference.py:18`.
- `klai-portal/backend/app/services/summarizer.py:45` already takes
  language from the user message — same intent, simpler context (no RAG
  citations to translate).
- ChatGPT system prompts (cited in research; not directly accessible
  but the pattern is well-documented in the Anthropic prompt
  engineering guide).
- Anthropic contextual-retrieval pattern (already used in
  SPEC-RAG-CONTEXTUAL-001) — same per-prompt language detection idea
  applied to the synthesis layer instead of the enrichment layer.

## Open questions

None at the time of writing. The annotation cycle will surface any.
