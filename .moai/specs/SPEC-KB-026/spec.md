---
id: SPEC-KB-026
version: 0.1.0
status: draft
created: 2026-05-25
updated: 2026-05-25
author: Codex
priority: high
---

# HISTORY

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1.0 | 2026-05-25 | Codex | Initial architecture SPEC for deterministic chat citations. |

---

# SPEC-KB-026: Deterministic Chat Citations Architecture

## Overview

Regular LibreChat KB answers and widget/partner KB answers shall use one
shared citation architecture. The model shall never be trusted to produce URLs,
source names, or source lists. Retrieved chunk metadata is converted into a
deterministic `CitationRegistry`, and each chat surface renders that registry in
the format it supports.

For regular LibreChat KB answers, the system shall prefer correctness over
token-by-token streaming: KB-enriched requests are completed non-streaming and
then rendered once with a deterministic Markdown source list. General chat may
remain streaming.

## Goals

- Stop hallucinated or irrelevant URLs in regular LibreChat.
- Remove fragile stream-delta citation rewriting from the target architecture.
- Keep widget/partner and LibreChat on one shared citation implementation.
- Avoid duplicated source extraction logic between services.
- Make deployment of citation helpers explicit and testable.

## Non-Goals

- No LibreChat frontend fork for citation UI.
- No model-generated source schema.
- No post-hoc "fix up whatever URL the model wrote" behavior.
- No full redesign of retrieval ranking or chunk selection.
- No change to general non-KB chat streaming behavior.

## Ubiquitous Language

- **Citation Registry:** Deterministic collection of source records derived from
  retrieved chunks before model generation completes. Contains only trusted
  metadata from retrieval/ingest.
- **Citation Renderer:** Client-specific formatter that combines answer prose
  with a registry. Examples: Markdown list for LibreChat, structured `sources`
  delta for widget.
- **Answer Prose:** Text produced by the model after retrieval context is
  injected. It is not trusted as source metadata.
- **Citable Source:** A retrieved chunk whose metadata contains a valid canonical
  `http` or `https` source URL and title or fallback title.
- **KB Chat:** A chat request where retrieval returned citable chunks and the
  answer must be grounded in those chunks.
- **General Chat:** A chat request where KB retrieval is disabled, unavailable,
  or intentionally bypassed. Existing streaming behavior may remain.

## Requirements

### Module 1: Shared Citation Registry

**REQ-1.1** WHERE retrieval returns chunks, THE SYSTEM SHALL build a
`CitationRegistry` from chunk metadata before response rendering.

**REQ-1.2** THE SYSTEM SHALL include only citable sources with normalized
`http` or `https` URLs in the registry.

**REQ-1.3** THE SYSTEM SHALL deduplicate sources by normalized source key so
multiple chunks from the same document render as one source.

**REQ-1.4** THE SYSTEM SHALL preserve the first trusted source title for each
source key and fall back to `"Source"` only when no title exists.

**REQ-1.5** THE SYSTEM SHALL expose renderers for:
- Markdown source lists for regular LibreChat.
- Structured source payloads for widget/partner API.

**REQ-1.6** THE SYSTEM SHALL keep the registry/renderer implementation in
`klai-libs/citations` as the canonical source.

### Module 2: Model Prompt Contract

**REQ-2.1** THE SYSTEM SHALL NOT expose source URLs to the model prompt for
regular LibreChat KB answers.

**REQ-2.2** THE SYSTEM SHALL NOT instruct the model to produce source lists,
Markdown links, or URL citations.

**REQ-2.3** THE SYSTEM SHALL instruct the model to answer from the provided
knowledge context only, while the application adds source references after the
model response.

**REQ-2.4** IF a model still writes links, numeric citations, or a source
section, THE SYSTEM MAY strip those artifacts defensively, but SHALL NOT use
them as source truth.

### Module 3: Regular LibreChat KB Rendering

**REQ-3.1** WHEN a regular LibreChat request is KB-enriched and has citable
sources, THE SYSTEM SHALL call the model with `stream=false`.

**REQ-3.2** WHEN the non-streaming model response returns, THE SYSTEM SHALL
render a single final assistant message containing answer prose and a
deterministic Markdown source list.

**REQ-3.3** THE Markdown source list SHALL be derived solely from the
`CitationRegistry`.

**REQ-3.4** IF no citable source exists for a KB-enriched answer, THE SYSTEM
SHALL return a deterministic "cannot answer reliably from available knowledge
sources" message instead of an uncited answer.

**REQ-3.5** General chat requests SHALL retain existing streaming behavior
unless another SPEC changes it.

### Module 4: Widget / Partner Rendering

**REQ-4.1** Widget and partner API responses SHALL use the same registry
implementation as regular LibreChat.

**REQ-4.2** Widget streaming responses MAY keep an SSE transport, but SHALL
emit structured `sources` and final content only after deterministic citation
composition.

**REQ-4.3** Widget and partner API responses SHALL NOT contain model-authored
URLs or source labels.

### Module 5: Deployment Shape

**REQ-5.1** The LiteLLM runtime SHALL load citation code in a way that cannot
silently omit new imports.

**REQ-5.2** Until a custom LiteLLM image exists, every vendored citation helper
needed by `deploy/litellm/klai_knowledge.py` SHALL have:
- a bind mount in `deploy/docker-compose.yml`;
- a drift test against the canonical `klai-libs` implementation;
- a production import smoke test in the deploy workflow or runbook.

**REQ-5.3** The target architecture SHOULD replace single-file vendoring with a
custom LiteLLM image or one mounted package directory.

### Module 6: Observability and Rollback

**REQ-6.1** The system SHALL log citation rendering with enough structure to
distinguish:
- no citable chunks;
- registry built;
- Markdown rendered;
- structured sources rendered;
- model artifacts stripped.

**REQ-6.2** The system SHALL expose a rollback switch for regular LibreChat KB
rendering mode during rollout.

**REQ-6.3** The rollback switch SHALL default to the new deterministic
non-streaming path after rollout validation.

## Architecture

```text
                           Retrieval API
                                │
                                ▼
                         retrieved chunks
                                │
                                ▼
                    klai-libs/citations
                   build CitationRegistry
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
              ▼                                   ▼
      LiteLLM regular chat                 portal-api widget
      KB path stream=false                 /partner/v1/chat
              │                                   │
              ▼                                   ▼
       model answer prose                  model answer prose
              │                                   │
              ▼                                   ▼
   MarkdownCitationRenderer          StructuredSourcesRenderer
              │                                   │
              ▼                                   ▼
   LibreChat assistant message       SSE final content + sources
```

## Key Design Decisions

### D1: KB LibreChat is non-streaming

The regular LibreChat KB path shall not token-stream model output. Source
correctness requires a final rendering boundary. Streaming can remain for
general chat and for future clients that support a structured final-source
event cleanly.

### D2: Source list first, inline markers second

The MVP correctness contract is the deterministic source list. Inline markers
may remain best-effort, but they are not required for correctness. If inline
markers cause attribution ambiguity, they should be disabled before source-list
rendering is compromised.

### D3: Widget and LibreChat share registry, not transport

The shared abstraction is citation metadata and rendering semantics, not the
network transport. Widget may return structured sources; LibreChat receives
Markdown because that is what the client can render today without a fork.

### D4: Defensive cleanup is allowed, source repair is not

The application may strip model-authored links or source sections to prevent
confusing output. It must not repair or normalize a model-invented URL into a
trusted citation.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---:|---:|---|
| LibreChat KB answers feel slower without token streaming | Medium | Medium | Limit non-streaming to KB-enriched requests; keep general chat streaming; monitor latency. |
| Some answers receive a source list but weak inline attribution | Medium | Low | Make source list the correctness contract; treat inline markers as optional. |
| LiteLLM callback/version behavior changes again | Medium | High | Remove dependency on streaming post-call citation rewriting for KB path. |
| Vendored LiteLLM helper drift breaks startup | Medium | High | Custom image or mounted package directory; import smoke tests. |
| Retrieval returns chunks without valid URLs | Medium | Medium | Deterministic no-citable-sources fallback and observability. |

## Rollout

1. Add registry/renderer API to `klai-libs/citations`.
2. Refactor widget/partner code to use registry/renderer names while preserving
   behavior.
3. Change regular LibreChat KB requests to non-streaming rendering behind
   `KLAI_KB_CHAT_RENDER_MODE=deterministic_non_streaming`.
4. Run focused tests and production import smoke.
5. Deploy with rollback env available.
6. Observe LiteLLM logs for 7 days.
7. Remove legacy streaming post-processing path in a cleanup PR.

