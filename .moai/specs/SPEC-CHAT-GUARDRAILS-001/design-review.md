# Klai-Specific Design Review — SPEC-CHAT-GUARDRAILS-001

Created: 2026-05-28

## Scope correction

This review supersedes any generic "put guardrails in the LLM proxy" wording. Klai's current user-facing model stack is Mistral via LiteLLM aliases, not OpenAI-hosted chat models:

| Alias | Upstream | Current role |
|---|---|---|
| `klai-primary` | `mistral/mistral-small-2603` | Default LibreChat/user-facing traffic; may be rerouted by `custom_router.py`. |
| `klai-fast` | `mistral/mistral-small-2603` | Lightweight user-facing and many background calls; bypasses `custom_router.py`. |
| `klai-large` | `mistral/mistral-large-2512` | Tool-call/agentic and long-user-message routes. |
| `klai-medium` | `mistral/mistral-medium-3.5` | Evaluation/medium-tier tasks. |
| `klai-bge-m3` | TEI BGE-M3 embeddings | Embeddings only; not a chat safety surface. |

Therefore provider-specific moderation must be optional and adapter-based. The deterministic Klai policy must work without assuming OpenAI runtime models.

## Runtime inventory

| Surface | Files | Model path | Safety boundary that exists now | Correct next boundary |
|---|---|---|---|---|
| Public widget / partner chat | `klai-portal/backend/app/api/partner.py`, `klai-portal/backend/app/services/partner_chat.py` | Portal calls LiteLLM `/v1/chat/completions` directly with requested alias. | Enforced deterministic input block before KB resolution/retrieval for widget keys; output block in non-streaming and backend-managed citation streaming path. | Keep earliest input guard in `chat_completions`; add page-context/chunk context rail in `retrieve_context`; make legacy streaming output guard explicit before raw user visibility. |
| LibreChat via LiteLLM | `deploy/litellm/klai_knowledge.py`, `deploy/litellm/custom_router.py` | LiteLLM callbacks on `klai-primary`; router may switch to `klai-large`/`klai-fast`. | KB entitlement, template injection, retrieval, citation composition, no general safety rail. | Add staged safety inside `klai_knowledge.py`, not `custom_router.py`; keep router as routing-only. |
| LiteLLM non-KB/general chat | `deploy/litellm/klai_knowledge.py` early-return branches | Same proxy; hooks return early for title generation, no org/user, no KB, meta/general paths. | Enforced input rail after org/user resolution. | Output rail still needs buffering design; input blocks return a normal LiteLLM rejected chat response, not a 500. |
| Retrieval API `/chat` synthesis | `klai-retrieval-api/retrieval_api/services/synthesis.py` | Streams `settings.synthesis_model` tokens from LiteLLM immediately. | No output guard; final text is only accumulated for telemetry. | Guard context before prompt assembly. Output enforcement requires buffering or chunk-gating and is a UX/perf change. |
| Retrieval coreference | `klai-retrieval-api/retrieval_api/services/coreference.py` | Non-streaming `settings.coreference_model`. | Timeout/failure falls back to original query. | Input/history guard; unsafe/malformed output falls back to original query. |
| Knowledge ingest | `klai-knowledge-ingest/knowledge_ingest/*.py` direct LiteLLM calls and Graphiti OpenAI-compatible clients | Mostly `klai-fast`; graph clients use LiteLLM OpenAI-compatible base URL. | Schema validation exists in some places; no prompt-injection context rail. | Context rail on source/DOM content and strict output validators; fallback to deterministic unknown/none, not user-visible refusals. |
| Scribe summaries | `klai-scribe/scribe-api/app/services/summarizer.py` | Direct LiteLLM `/v1/chat/completions`. | JSON parse for extraction; no prompt-injection/content rail. | Treat transcript as untrusted context; validate extraction JSON and final Markdown before storing/returning. |

## Why the first implementation slice was intentionally narrow

The first committed runtime slice only moved the widget/partner deterministic policy into `klai-libs/llm-safety` and preserved the already-tested public helper names in `partner_chat.py`. This avoided changing the broadest choke point (`deploy/litellm/klai_knowledge.py`) before verifying model routing, streaming behaviour, and callback branch contracts.

That caution is necessary because `klai_knowledge.py` is not just a prompt hook. It also:

- resolves org/user entitlement,
- fetches tenant templates,
- handles meta-query and general-chat branches,
- rewrites/classifies queries,
- calls retrieval-api with dual auth,
- injects KB context and low-confidence instructions,
- changes `stream` depending on citation render strategy,
- composes citations in both non-streaming and selected streaming modes.

A broad guardrail there can break correctness or latency if it is inserted before the metadata it needs, after the retrieval call it is meant to prevent, or in a streaming path that currently yields raw chunks.

## Placement decisions

### Portal widget / partner chat

Current guard placement is right for the disclosed bug:

1. `chat_completions()` validates model/messages.
2. `_widget_safety_block_response()` runs before KB access, slug resolution, retrieval, page context, or LiteLLM.
3. Refusal returns OpenAI-compatible shape or SSE refusal with `finish_reason=content_filter`.

Next portal changes must be separate and tested:

- `retrieve_context()` should scan `cleaned_page_context` and retrieved chunks as `SafetyPhase.CONTEXT` before `_build_system_prompt()`.
- Context handling should drop offending context where possible, not reject the entire user question if safe evidence remains.
- Legacy linked-citation streaming currently yields sanitized text incrementally. Full output enforcement there requires a buffer strategy; doing it silently would change latency and streaming UX.

### LiteLLM

`custom_router.py` must remain routing-only. It currently reroutes only `klai-primary` and has no org/user/tenant context. Safety enforcement belongs in `klai_knowledge.py` because that hook has access to request metadata, user/org resolution, KB metadata, and post-call citation hooks.

LiteLLM placement:

1. Resolve `org_id` and `librechat_user_id`.
2. Read last user message.
3. Run deterministic input rail before templates, meta-query detection, query rewrite, or retrieval.
4. Store decision metadata under `data["metadata"]["_klai_safety"]`.
5. In `enforce` mode, return a refusal string from `async_pre_call_hook`; LiteLLM formats it as a normal chat/text completion response.
6. `shadow` remains available via `LLM_SAFETY_LITELLM_MODE=shadow` only as an emergency false-positive diagnostic mode.

Output rail:

- Non-streaming KB responses can be checked in `async_post_call_success_hook()` before returning.
- Existing KB streaming citation mode already has a composition point, but the hook still forwards a pending item. Tests must prove no unsafe content is yielded before the block/refusal.
- Non-KB streaming currently passes through untouched. Enforcing output there needs an explicit `LLM_SAFETY_STREAMING_BUFFER` rollout because buffering changes responsiveness.

### Retrieval API

`synthesis.py` streams tokens as soon as they arrive and only later yields final citations. Output blocking after `full_text` is complete would be too late. The safe near-term implementation is:

- input/context guard before prompt assembly,
- chunk-level filtering before `_build_context()`,
- no output enforcement on streaming until a deliberate buffer/chunk-gate design is accepted.

`coreference.py` is safer to change first: it is non-streaming, already fail-softs to the original query, and has a small output contract.

### Ingest and scribe

These are not public chat, so refusals are the wrong UX. Unsafe source content should cause deterministic fallback:

- selector detection returns `None`,
- enrichment returns existing fallback/unknown behaviour,
- classifiers return empty/unknown,
- scribe extraction/synthesis refuses storage or produces a safe redacted summary depending on product requirements.

## Robustness status

Implemented now:

- deterministic recognition of the disclosed delimiter/GODMODE family,
- hazardous-instruction topic+instruction detection, including the `c4` leetspeak regression fix,
- system-prompt extraction blocking,
- encoded-wrapper detection as `needs_provider`,
- shared policy package with tests and a portal adapter,
- public widget block before retrieval/model,
- portal context rail that drops unsafe widget page context before retrieval/prompt injection,
- portal context rail that drops unsafe retrieved chunks before prompt assembly,
- non-streaming and backend-managed citation streaming output block.

Not robust enough yet:

- no provider-backed prompt-attack classifier is enforced,
- no multi-turn grooming state,
- no LiteLLM output enforcement for raw streaming yet,
- no red-team corpus runner across all services,
- no output rail for raw non-KB streaming without buffering.

## Next safe implementation slice

After the portal context rail and LiteLLM input/context enforcement, the next slice should be:

1. Add a deployment/runbook checklist for `LLM_SAFETY_LITELLM_MODE=enforce` and emergency rollback to `shadow`.
2. Review LiteLLM safety block logs for false positives after rollout.
3. Design retrieval-api/LiteLLM raw streaming output buffering before enforcing generated-token blocks there.

This keeps broad model-routing and streaming behaviour unchanged while expanding coverage from the disclosed public-widget surface toward global chat.
