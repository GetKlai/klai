# Progress — SPEC-CHAT-GUARDRAILS-001

## 2026-05-28 — Slice 1 implemented

Implemented:

- Created `klai-libs/llm-safety` with deterministic input/output policy, typed models, refusal copy, provider interface skeleton, OpenAI moderation placeholder, and initial corpus.
- Added portal-api dependency wiring for `klai-llm-safety`.
- Added `app/services/llm_safety_adapter.py` as the portal adapter.
- Rewired the existing `partner_chat.py` hotfix helpers to call the shared library through the adapter, preserving existing public function names and test coverage.
- Added adapter tests and kept the existing widget prompt-injection/output-regression tests green.

Verification:

- `cd klai-libs/llm-safety && uv run pytest -q && uv run ruff check . && uv run pyright`
- `cd klai-portal/backend && uv run pytest tests/test_llm_safety_adapter.py tests/test_partner_chat.py -q`
- `cd klai-portal/backend && uv run ruff check app/services/llm_safety_adapter.py app/services/partner_chat.py app/api/partner.py tests/test_llm_safety_adapter.py tests/test_partner_chat.py`
- `cd klai-portal/backend && uv run pyright app/services/llm_safety_adapter.py`
- `git diff --check`

Deferred deliberately:

- LiteLLM hook integration.
- Retrieval API synthesis/coreference integration.
- Knowledge ingest and scribe integration.
- Provider-backed enforcement beyond interface skeleton.
- Production rollout settings and Grafana alerts.

Rationale:

The first slice centralizes the existing public-widget hotfix without broadening runtime blast radius.

## 2026-05-28 — Klai-specific runtime design review

Reviewed:

- LiteLLM model aliases and routing in `deploy/litellm/config.yaml` and `deploy/litellm/custom_router.py`.
- The actual LibreChat callback path in `deploy/litellm/klai_knowledge.py`, including early returns, retrieval, citation composition, and streaming hooks.
- Portal widget/partner chat input, retrieval, non-streaming output, backend-managed citation streaming, and legacy streaming paths.
- Retrieval API synthesis/coreference paths.
- Knowledge ingest and scribe direct LiteLLM callers.

Added:

- `design-review.md` with a Klai-specific placement and rollout analysis.

Corrections:

- Klai user-facing chat currently uses Mistral through LiteLLM aliases (`klai-primary`, `klai-fast`, `klai-large`, `klai-medium`). OpenAI moderation remains an optional provider adapter, not an assumed runtime dependency.
- `custom_router.py` should stay routing-only. Broad chat enforcement belongs in `klai_knowledge.py`, with a normal refusal response instead of a proxy error.
- The next low-risk runtime slice should be portal context scanning for page context and retrieved chunks, not immediate global LiteLLM enforcement. That directly expands the disclosed public-widget fix without changing global model routing or streaming behaviour.

## 2026-05-28 — Slice 2 implemented: portal context rail

Implemented:

- Added `check_context_text()` in the portal safety adapter.
- Added a `SafetyPhase.CONTEXT` regression test in `klai-libs/llm-safety`.
- Added context checks in `partner_chat.retrieve_context()`:
  - unsafe widget page context is dropped before it is sent to retrieval-api or appended to the prompt,
  - unsafe retrieved chunks are dropped before `_build_system_prompt()`,
  - trusted source metadata is filtered to the remaining safe chunks.
- Added portal tests for malicious page context and malicious retrieved chunks.

Verification:

- `cd klai-libs/llm-safety && uv run pytest -q && uv run ruff check . && uv run pyright`
- `cd klai-portal/backend && uv run pytest tests/test_llm_safety_adapter.py tests/test_partner_chat.py -q`
- `cd klai-portal/backend && uv run ruff check app/services/llm_safety_adapter.py app/services/partner_chat.py app/api/partner.py tests/test_llm_safety_adapter.py tests/test_partner_chat.py && uv run pyright app/services/llm_safety_adapter.py app/services/partner_chat.py`
- `git diff --check`

Deferred at this point:

- LiteLLM global shadow/enforcement.
- Retrieval API streaming output rail.
- Ingest/scribe rails.
- Provider-backed prompt-attack classifier enforcement.

## 2026-05-28 — Slice 3 implemented: broad adapters and shadow rails

Implemented:

- Added corpus-backed library test coverage for `tests/corpus/guardrail_cases.yaml`.
- Vendored `klai_llm_safety` into `deploy/litellm/` with a drift test.
- Mounted `deploy/litellm/klai_llm_safety` into the LiteLLM container and added `LLM_SAFETY_LITELLM_MODE=enforce` default.
- Added LiteLLM input/context safety decisions in `klai_knowledge.py`; in enforce mode unsafe requests/context return a normal LiteLLM refusal response before model generation.
- Added retrieval-api adapter:
  - coreference unsafe input/output falls back to the original query,
  - synthesis drops unsafe context chunks before prompt assembly and rebuilds evidence from remaining safe chunks.
- Added scribe summarizer guards:
  - transcript/facts are explicitly marked as untrusted instructions in both extraction and synthesis prompts,
  - unsafe generated summary output is replaced with a refusal.
- Added knowledge-ingest guards:
  - selector AI skips LLM calls for unsafe DOM summaries,
  - enrichment returns a deterministic `reference` fallback without calling LLM when source context is unsafe.

Verification:

- `klai-libs/llm-safety`: `uv run pytest -q && uv run ruff check . && uv run pyright`
- `klai-portal/backend`: `uv run pytest tests/test_llm_safety_adapter.py tests/test_partner_chat.py -q`
- `klai-retrieval-api`: `uv run --extra dev pytest tests/test_coreference.py tests/test_synthesis.py -q`
- `klai-scribe/scribe-api`: `uv run --extra dev pytest tests/test_summarizer_safety.py -q`
- `klai-knowledge-ingest`: `uv run --extra dev pytest tests/test_enrichment.py tests/test_selector_ai_safety.py -q`
- `deploy/litellm`: `PYTHONPATH=../../klai-libs/citations uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_klai_llm_safety_drift.py tests/test_klai_knowledge_hook.py::TestKlaiKnowledgeHookKB010::test_litellm_safety_shadow_records_direct_prompt_injection tests/test_klai_knowledge_hook.py::TestKlaiKnowledgeHookKB010::test_litellm_safety_enforce_blocks_direct_prompt_injection tests/test_klai_knowledge_hook.py::TestKlaiKnowledgeHookKB010::test_litellm_safety_enforce_blocks_indirect_context_injection -q`
- Focused `ruff check` on touched files in every package.

Still deferred deliberately:

- Retrieval API streaming output rail. Context is guarded now; generated-token blocking still requires an accepted buffering/chunk-gating design.
- Provider-backed prompt-attack classifier enforcement.

## 2026-05-28 — Slice 4 implemented: LiteLLM hard enforcement

Implemented:

- Changed LiteLLM safety default from `shadow` to `enforce`.
- `async_pre_call_hook` now returns a refusal string for unsafe input/context. LiteLLM formats that as a normal chat/text completion response, so blocked security cases do not become generic proxy 500s.
- Kept `LLM_SAFETY_LITELLM_MODE=shadow` as emergency rollback/diagnostic mode.
- Added tests for:
  - shadow records direct prompt injection,
  - enforce blocks direct prompt injection before retrieval/model,
  - enforce blocks indirect retrieved-context injection after retrieval and before prompt/model.

Verification:

- `PYTHONPATH=../../klai-libs/citations uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_klai_knowledge_hook.py tests/test_klai_llm_safety_drift.py -q` (`94 passed`)
- `uv run --with ruff ruff check klai_knowledge.py klai_llm_safety tests/test_klai_llm_safety_drift.py tests/test_klai_knowledge_hook.py`
