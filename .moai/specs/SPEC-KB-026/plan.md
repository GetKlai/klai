# Implementation Plan — SPEC-KB-026

## Overview

This plan replaces the hotfix-style streaming citation rewrite with an explicit
citation registry/rendering boundary. The first implementation target is regular
LibreChat KB answers; widget/partner is refactored to the same shared contract
without changing its public API.

| Area | Change | Risk |
|---|---|---|
| `klai-libs/citations` | Add explicit registry + renderers on top of current citation logic | Low |
| `deploy/litellm/klai_knowledge.py` | Route KB-enriched requests through a streaming-safe deterministic render boundary | Medium |
| `klai-portal/backend/app/services/partner_chat.py` | Rename/refactor toward registry renderer; remove duplicate source helpers where safe | Medium |
| `deploy/docker-compose.yml` / LiteLLM deploy | Harden helper loading/import checks | Medium |
| Tests | Add production-path tests for non-streaming KB LibreChat + widget structured sources | Low |

## Phase 1 — Shared Citation Registry

### Task 1.1: Add Registry Types

File:
- `klai-libs/citations/klai_citations/__init__.py`

Add:
- `CitationRegistry`
- `build_citation_registry(chunks: list[dict])`
- `render_markdown_answer(answer: str, registry: CitationRegistry)`
- `render_structured_sources(registry: CitationRegistry)`

Implementation notes:
- Existing `citation_sources_from_chunks()` can be reused internally.
- Keep current `compose_citations()` wrappers for backward compatibility during
  migration.
- Do not remove `strip_model_citation_artifacts()` yet; keep it as defensive
  cleanup.

Tests:
- citable source dedupe by URL key;
- invalid URL excluded;
- stable output order;
- Markdown source list contains only registry URLs;
- structured sources shape matches widget expectation.

## Phase 2 — Regular LibreChat Non-Streaming KB Rendering

### Task 2.1: Add Render Mode Flag

File:
- `deploy/litellm/klai_knowledge.py`

Add env:
- `KLAI_KB_CHAT_RENDER_MODE`

Values:
- `streaming_guard` (default; preserves LibreChat/LangGraph streaming contract)
- `deterministic_non_streaming` (opt-in for compatible non-streaming callers)
- `legacy_stream_guard` (temporary alias for `streaming_guard`)

### Task 2.2: Resolve Render Mode Per Request

In `async_pre_call_hook`, when retrieval returns citable chunks:
- store citation registry metadata in `_klai_kb_meta`;
- preserve `stream=true` requests and render at stream close;
- set `data["stream"] = False` only for explicit deterministic mode on
  callers that did not already request streaming;
- preserve the caller's original stream preference in metadata for logging.

Important:
- General chat and no-KB branches do not change.
- Retrieval failures keep existing fail-open/fail-loud behavior.

### Task 2.3: Render Final Markdown

In `async_post_call_success_hook`:
- read `_klai_kb_meta`;
- use registry renderer to produce final Markdown;
- set `choice.message.content` once;
- do not use stream delta buffering in deterministic mode.

Failure handling:
- if registry has no sources, return deterministic no-citable-sources message;
- log `kb_citations_no_citable_sources`.

### Task 2.4: Keep Temporary Alias

Accept `legacy_stream_guard` as a config alias for one deploy window, but emit
new metadata as `streaming_guard`.

## Phase 3 — Widget / Partner Refactor

### Task 3.1: Move Widget to Registry Names

File:
- `klai-portal/backend/app/services/partner_chat.py`

Replace direct calls to `compose_citations()` with registry renderer calls.

Do not change the public response contract:
- streaming still emits `delta.sources` and `delta.content`;
- non-streaming still includes `message["sources"]` where currently expected.

### Task 3.2: Remove Redundant Source Helpers

Candidate helpers to remove or convert after tests pass:
- duplicate `_normalise_guard_url`
- duplicate `_source_url_key`
- duplicate chunk source metadata extraction helpers

Keep only wrapper functions if they protect public behavior or test readability.

## Phase 4 — Deployment Hardening

### Task 4.1: Import Smoke Test

Add deploy/workflow or script check:

```bash
docker exec klai-core-litellm-1 python -c \
  "import klai_knowledge, klai_citations; print('ok')"
```

The smoke must run after LiteLLM recreate and before the workflow reports
success.

### Task 4.2: Package Directory or Custom Image Decision

Preferred implementation:
- create a small custom LiteLLM image that installs `klai-citations`,
  `klai-chat-prompts`, `klai-service-auth`, and `klai-retrieval-telemetry`.

Interim implementation:
- mount one package directory, not individual files, if custom image is too
  large for this SPEC.

Decision rule:
- If custom image is expected to take > 1 day, ship interim package-directory
  mount and track custom image in `SPEC-LITELLM-CUSTOM-IMAGE-001`.

## Phase 5 — Tests

### LiteLLM Tests

Files:
- `deploy/litellm/tests/test_klai_knowledge_hook.py`
- `deploy/litellm/tests/test_klai_citations_drift.py`

Add/adjust:
- KB-enriched request sets `stream=False` in deterministic mode.
- General chat preserves stream flag.
- Non-streaming KB response appends deterministic Markdown sources.
- Model-authored fake URLs are stripped and never rendered.
- No-citable-source chunks return deterministic fallback.
- Legacy stream guard remains tested only while rollback exists.

### Portal Tests

Files:
- `klai-portal/backend/tests/test_partner_chat.py`

Add/adjust:
- widget structured sources are registry-derived;
- no model-authored URL appears in `sources`;
- no citable source returns fallback message;
- streaming response emits structured sources exactly once.

### Shared Library Tests

Files:
- `klai-libs/citations/tests/test_citations.py`

Add:
- registry construction unit tests;
- Markdown renderer tests;
- structured renderer tests;
- invalid/placeholder URL tests.

## Phase 6 — Rollout and Cleanup

1. Deploy with the default `KLAI_KB_CHAT_RENDER_MODE=streaming_guard`.
2. Confirm regular LibreChat KB answer has deterministic sources.
3. Confirm general chat still streams.
4. Watch LiteLLM logs:
   - `kb_citations_rendered_markdown`
   - `kb_citations_no_citable_sources`
   - import/startup errors
5. After 7 days, remove `legacy_stream_guard` path and its tests.

## Validation Commands

```bash
uv run --with pytest --with pytest-asyncio --with httpx \
  pytest deploy/litellm/tests/test_klai_citations_drift.py \
         deploy/litellm/tests/test_klai_knowledge_hook.py -q

cd klai-libs/citations && uv run pytest -q

cd klai-portal/backend && uv run pytest tests/test_partner_chat.py -q

git diff --check
```

## Rollback

Immediate rollback:
- leave or set `KLAI_KB_CHAT_RENDER_MODE=streaming_guard`;
- recreate LiteLLM.

Code rollback:
- revert the SPEC-KB-026 implementation commit;
- keep `klai_citations.py` mount in place because it is required by the
  existing hotfix.
