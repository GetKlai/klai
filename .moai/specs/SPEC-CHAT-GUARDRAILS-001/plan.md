# Plan — SPEC-CHAT-GUARDRAILS-001

Methodology: TDD for shared library and endpoint integration; DDD characterization-first for LiteLLM hook and streaming paths.

## Dependency graph

```
Phase 1: llm-safety library + corpus
        │
        ├──► Phase 2: portal widget/partner chat integration
        │
        ├──► Phase 3: LiteLLM hook integration
        │
        ├──► Phase 4: retrieval-api integration
        │
        └──► Phase 5: ingest + scribe integration
                 │
                 ▼
        Phase 6: telemetry, red-team CI, rollout controls
```

Hard ordering:

- Phase 1 MUST land before any consumer deletes local guard logic.
- Phase 2 MUST replace the 2026-05-28 hotfix without weakening the widget refusal path.
- Portal context scanning MUST land before global LiteLLM enforcement, because it closes the public-widget indirect-context gap without changing global model routing or streaming behaviour.
- Phase 3 MUST be staged carefully because LiteLLM is the broadest chat choke point.
- Deterministic high-confidence enforcement is enabled for public widget and LiteLLM input/context. Provider-backed enforcement MUST start in `shadow` outside public widget until false positives are reviewed.

## Phase 1 — Shared `klai-libs/llm-safety`

Files:

| File | Action |
|---|---|
| `klai-libs/llm-safety/pyproject.toml` | New package, Python 3.13, no FastAPI dependency. |
| `klai-libs/llm-safety/klai_llm_safety/models.py` | `SafetyRequest`, `SafetyDecision`, enums for surface, phase, action, category. |
| `klai-libs/llm-safety/klai_llm_safety/policy.py` | Deterministic baseline checks. Migrate the partner-chat hotfix logic here, then expand. |
| `klai-libs/llm-safety/klai_llm_safety/providers.py` | Provider interface and provider result normalization. |
| `klai-libs/llm-safety/klai_llm_safety/openai_moderation.py` | Optional OpenAI moderation adapter if `OPENAI_API_KEY` is configured. |
| `klai-libs/llm-safety/klai_llm_safety/refusals.py` | Localized safe refusal messages. |
| `klai-libs/llm-safety/tests/` | Unit tests for every category and provider error mode. |
| `klai-libs/llm-safety/tests/corpus/guardrail_cases.yaml` | Red-team corpus with `id`, `surface`, `phase`, `input`, `expected_action`, `tags`. |

Implementation notes:

- Normalize text by lowercasing, Unicode NFKC, whitespace collapse, simple leetspeak folding, and code-fence/delimiter extraction.
- Treat encoded wrappers as `needs_provider` rather than trying to decode everything deterministically.
- Keep policy reasons stable; tests and telemetry depend on them.
- Use explicit surface names: `widget`, `partner_chat`, `librechat`, `retrieval_synthesis`, `retrieval_coreference`, `ingest_enrichment`, `scribe_summary`.

Tests:

- RED: reported `GODMODE` payload returns `action=block`.
- RED: benign questions about Klai pricing/help docs pass.
- RED: indirect chunk text like "Ignore all previous instructions..." returns context block/drop.
- RED: provider timeout returns configured fallback action.

## Phase 2 — Portal widget and partner chat

Files:

| File | Action |
|---|---|
| `klai-portal/backend/pyproject.toml` | Add editable source for `klai-llm-safety`. |
| `klai-portal/backend/app/core/config.py` | Add `llm_safety_mode`, provider URLs/keys/timeouts, per-surface fail mode. |
| `klai-portal/backend/app/services/llm_safety_adapter.py` | Thin async adapter from portal settings/logging to library. |
| `klai-portal/backend/app/api/partner.py` | Replace `_widget_safety_block_response` internals with adapter call. Apply to partner API too, with stricter mode for widget. |
| `klai-portal/backend/app/services/partner_chat.py` | Replace local deterministic safety helpers with library calls. Add context scan for page context + chunks. |
| `klai-portal/backend/tests/test_partner_chat.py` | Keep existing hotfix tests; assert adapter is called before retrieval and before model. |
| `klai-portal/backend/tests/test_llm_safety_adapter.py` | New adapter tests for modes and provider failures. |

Rollback:

- Keep local hotfix behaviour in tests until library integration proves equivalent.
- If provider false positives spike, switch provider to `shadow`; deterministic widget blocks remain enforced.

## Phase 3 — LiteLLM hook as broad chat choke point

Files:

| File | Action |
|---|---|
| `deploy/docker-compose.yml` | Mount `klai-libs/llm-safety/klai_llm_safety` into LiteLLM container like `klai_chat_prompts` and `klai_citations`. Add env vars. |
| `.github/workflows/deploy-compose.yml` and/or `litellm-hook-deploy.yml` | Sync the new shared library to `/opt/klai/litellm/` during deploy. |
| `deploy/litellm/klai_knowledge.py` | Add input rail in `async_pre_call_hook`; add retrieval rail before KB context injection; add output rail in post-call hooks. |
| `deploy/litellm/custom_router.py` | No enforcement; preserve as routing only. |
| `deploy/litellm/tests/test_klai_knowledge_hook.py` | Add direct, indirect, non-streaming, streaming, and benign pass-through tests. |

Implementation notes:

- Do not call provider safety before org/user resolution; policy needs surface metadata and logs should include org/user when possible.
- Keep `deploy/litellm/custom_router.py` routing-only. It only has routing signals and must not own safety enforcement.
- Klai runtime chat models are Mistral aliases behind LiteLLM. Provider-backed moderation must be optional/adapted; do not assume OpenAI is the serving model provider.
- For streaming post-call hook, prefer the existing KB render path that already buffers enough to compose citations. For non-KB streaming, add a safety-buffer mode or force non-streaming when `LLM_SAFETY_STREAMING_BUFFER=1`.
- Preserve `data["metadata"]` safety decisions so post-call hooks know whether input/context was already blocked.

Risk:

- This phase affects all LibreChat/agent traffic. High-confidence deterministic prompt-injection/hazardous/context blocks are enforced. `LLM_SAFETY_LITELLM_MODE=shadow` remains the emergency rollback for false positives while preserving telemetry.

## Phase 4 — Retrieval API

Files:

| File | Action |
|---|---|
| `klai-retrieval-api/pyproject.toml` | Add `klai-llm-safety`. |
| `klai-retrieval-api/retrieval_api/config.py` | Add safety settings with service defaults. |
| `klai-retrieval-api/retrieval_api/services/llm_safety_adapter.py` | Service adapter. |
| `klai-retrieval-api/retrieval_api/services/coreference.py` | Check input/history before rewrite; unsafe returns original query. |
| `klai-retrieval-api/retrieval_api/services/synthesis.py` | Check chunks before prompt; buffer generated text before yielding or gate chunks before flush. |
| `klai-retrieval-api/tests/test_llm_safety_coreference.py` | New tests. |
| `klai-retrieval-api/tests/test_llm_safety_synthesis.py` | New tests for indirect chunks and unsafe model output. |

Implementation notes:

- Retrieval context safety should drop/block only the offending chunk when enough safe evidence remains; otherwise return the existing no-citable-sources response.
- Synthesis output should fail closed to the same no-citable-sources/refusal message, not partial unsafe tokens.

## Phase 5 — Knowledge ingest and scribe

Files:

| File | Action |
|---|---|
| `klai-knowledge-ingest/pyproject.toml` | Add `klai-llm-safety`. |
| `klai-knowledge-ingest/knowledge_ingest/llm_safety_adapter.py` | Adapter with fail-closed-to-fallback defaults. |
| `knowledge_ingest/enrichment.py` | Check stripped document context and output JSON content. |
| `knowledge_ingest/selector_ai.py` | Check DOM summary context; require strict CSS selector validation after LLM. |
| `knowledge_ingest/taxonomy_classifier.py`, `content_labeler.py`, `description_generator.py` | Add context/output checks and fallback to deterministic `unknown`/empty result. |
| `klai-scribe/scribe-api/pyproject.toml` | Add `klai-llm-safety`. |
| `klai-scribe/scribe-api/app/services/summarizer.py` | Treat transcript as untrusted context; add output safety before summary is stored/returned. |

Tests:

- Fixture transcript/document containing hidden "ignore previous instructions" does not change output schema.
- Unsafe transcript/request refuses or redacts according to policy.
- Selector output cannot include multi-line text, JS, URLs, or instruction text.

## Phase 6 — Telemetry, rate limits, and red-team CI

Files:

| File | Action |
|---|---|
| `docs/runbooks/llm-safety-guardrails.md` | New runbook: modes, provider failure, false positive review, emergency rollback. |
| `deploy/grafana/provisioning/alerting/llm-safety-rules.yaml` | Alert on spike in blocks/provider errors for widget and LiteLLM. |
| `klai-portal/backend/app/services/partner_rate_limit.py` | Reuse for widget safety-block rate limiting if needed. |
| `.github/workflows/llm-safety-corpus.yml` | Run deterministic corpus across library and key service adapters. |
| `scripts/run-llm-safety-corpus.py` | Shared local/CI runner. |

Operational rollout:

1. Merge Phase 1 with corpus only.
2. Enforce public widget via Phase 2.
3. LiteLLM Phase 3 with deterministic input/context enforcement and `shadow` rollback.
4. Review VictoriaLogs false positives and tune thresholds.
5. Add retrieval/ingest/scribe enforcement.
6. Evaluate provider-backed enforcement in shadow before blocking.

## Open decisions

| Decision | Recommendation |
|---|---|
| Primary prompt-attack provider | Evaluate Lakera vs Azure Prompt Shields vs Bedrock Guardrails in shadow mode. Pick one based on EU data residency, latency, prompt-attack quality, and operational ownership. |
| OpenAI moderation use | Use for harmful-content moderation if policy/legal accepts provider. It does not replace prompt-attack detection. |
| Streaming UX | For public widget and KB answers, prefer safe buffering over raw token streaming. For authenticated low-risk chat, allow configurable chunk-buffering in shadow first. |
| Admin-managed guardrail rules | Defer UI and `portal_rules` table to a follow-up unless needed for launch. Runtime safety must not wait for UI. |

## Pre-merge checklist for `/moai run`

- [ ] `codeindex impact` on every modified symbol before code edits.
- [ ] Corpus RED tests fail before integration and pass after.
- [ ] Existing hotfix tests in `test_partner_chat.py` remain green.
- [ ] LiteLLM hook tests cover both `stream=True` and `stream=False`.
- [ ] No raw harmful payloads in logs by default.
- [ ] Public widget cannot be configured to `off` without explicit emergency env.
