---
id: SPEC-KB-026
phase: research
created: 2026-05-25
updated: 2026-05-25
author: Codex
---

# Research — Deterministic Chat Citations

## Trigger

Regular LibreChat chat generated unusable references: invented URLs, invented
source labels, and source names unrelated to retrieved documents. The widget
path was already closer to the desired behavior because portal-api owned source
rendering instead of trusting model-authored links.

An emergency fix moved LibreChat toward deterministic citations, but the current
shape still has a fragile streaming post-processing layer in
`deploy/litellm/klai_knowledge.py`. This SPEC designs the cleaner architecture.

## Current Architecture

### Path A — LibreChat

Files:
- `deploy/litellm/klai_knowledge.py`
- `deploy/litellm/config.yaml`
- `deploy/docker-compose.yml`
- `deploy/litellm/klai_citations.py`

Flow:

1. LibreChat sends an OpenAI-compatible chat completion request to LiteLLM.
2. `KlaiKnowledgeHook.async_pre_call_hook` retrieves chunks from retrieval-api.
3. The hook prepends a grounded system prompt plus retrieved chunk text.
4. The model response streams back through LiteLLM.
5. The current hotfix buffers stream deltas in `_citation_stream_parts`, then
   calls `render_markdown_answer_with_sources()`.

Observations:
- This is still a response-rewrite path, even though it no longer trusts model
  URLs.
- It depends on LiteLLM callback semantics (`async_post_call_streaming_iterator_hook`).
- It can reduce or distort streaming semantics because content is held until
  citations can be composed safely.
- Vendored helper files in `deploy/litellm/` must be individually mounted in
  `deploy/docker-compose.yml`; missing `klai_citations.py` caused a production
  restart loop.

### Path B — Widget / Partner API

File:
- `klai-portal/backend/app/services/partner_chat.py`

Flow:

1. portal-api receives `/partner/v1/chat/completions`.
2. portal-api retrieves chunks and sends augmented messages to LiteLLM.
3. For `citation_output == "markers"`, portal-api collects the model text and
   calls `compose_citations()`.
4. It sends a structured `sources` delta followed by content.

Observations:
- This is closer to the target architecture: portal-api owns source rendering.
- It still contains legacy sanitizer/link-repair helpers alongside the newer
  `compose_citations()` path.
- It has a no-citable-sources fallback that is explicit and deterministic.

### Shared Citation Library

Files:
- `klai-libs/citations/klai_citations/__init__.py`
- `klai-portal/backend/app/services/citations.py`
- `deploy/litellm/klai_citations.py`

Current capabilities:
- Normalizes source URLs.
- Extracts source metadata from chunks.
- Strips model-authored source artifacts.
- Composes inline numeric markers based on chunk/source token overlap.
- Renders Markdown source lists for clients without structured source support.

Observed gap:
- The API is named around "compose citations from text", not around an explicit
  registry/rendering boundary. That makes it tempting for each chat surface to
  keep its own timing and response-shape logic.

## Root Cause

The system did not have a single architectural boundary for source rendering.
Instead:

- The model was previously allowed or encouraged to produce source references.
- LibreChat and widget followed separate response flows.
- Regular chat used LiteLLM streaming callbacks to repair output after the fact.
- Deployment relied on independent single-file mounts for vendored helper code.

The durable fix is not "better normalization". The durable fix is: never make
the model authoritative for citations, and do not attach deterministic citations
through fragile stream-delta rewriting.

## Design Direction

Adopt a **Citation Registry + Renderer** architecture.

The model produces answer prose only. Retrieved chunks produce a deterministic
registry. A renderer combines the two at a deliberate response boundary:

- Widget / Partner API: structured `sources` field.
- LibreChat: non-streaming Markdown answer with a source list appended.

For the regular LibreChat KB path, prefer non-streaming model completion. This
matches the product priority: source correctness is more important than
token-by-token display for knowledge-grounded answers.

## Alternatives Considered

### A. Keep streaming and improve post-processing

Rejected as the target architecture. It can be made safer, but it keeps source
rendering tied to provider/LiteLLM stream behavior. This is operationally
fragile and hard to reason about.

### B. Ask the model for structured citations

Rejected. Even with JSON schema or strict prompting, the model is still a source
authority. The failure mode is exactly what the incident showed: confident,
plausible, wrong references.

### C. Keep inline markers as the main contract

Deferred. Inline markers are useful, but require reliable attribution between
answer spans and source chunks. The MVP should render a deterministic source
list under the answer. Inline markers may remain as a best-effort enhancement,
but the correctness contract is the source list.

### D. Make all KB chat non-streaming

Accepted for regular LibreChat KB answers. Widget/partner can keep an SSE
interface, but the SSE may emit final content and structured sources after the
model completion. It should not pretend to be token streaming if correctness
requires final composition.

## Implementation Constraints

- Do not fork LibreChat for citation rendering.
- Do not expose source URLs inside the LLM prompt.
- Do not reintroduce model-authored source lists or URL repair logic.
- Do not duplicate citation extraction between portal and LiteLLM.
- Do not add a second retrieval/chat flow. Both surfaces must share the same
  registry/renderer library.
- Keep the hotfix path available behind a rollback switch until production
  stability is observed.

## Relevant Files

Core:
- `klai-libs/citations/klai_citations/__init__.py`
- `deploy/litellm/klai_knowledge.py`
- `deploy/litellm/config.yaml`
- `deploy/docker-compose.yml`
- `klai-portal/backend/app/services/partner_chat.py`

Tests:
- `klai-libs/citations/tests/test_citations.py`
- `deploy/litellm/tests/test_klai_citations_drift.py`
- `deploy/litellm/tests/test_klai_knowledge_hook.py`
- `klai-portal/backend/tests/test_partner_chat.py`

Follow-up infrastructure:
- `deploy/litellm/Dockerfile` if a custom LiteLLM image is introduced.
- `.github/workflows/litellm-hook-deploy.yml` for bind-mount deployment.

## Recommendation

Implement SPEC-KB-026 in two phases:

1. **Product-safe architecture change:** regular LibreChat KB answers become
   non-streaming at LiteLLM, and the final response is rendered by a citation
   registry/Markdown renderer.
2. **Deployment hardening:** replace single-file vendored mounts with either a
   custom LiteLLM image or one mounted package directory with import/drift tests.

