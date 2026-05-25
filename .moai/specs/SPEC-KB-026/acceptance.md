# Acceptance Criteria — SPEC-KB-026

## AC-1 — Model Is Not Source Authority

**Given** a KB-enriched LibreChat request with retrieved chunks containing
trusted source URLs,
**When** the model response includes a fake Markdown link or raw URL,
**Then** the final user-visible answer SHALL NOT contain that fake URL,
**And** the rendered source list SHALL contain only URLs from the citation
registry.

## AC-2 — LibreChat KB Path Is Non-Streaming

**Given** `KLAI_KB_CHAT_RENDER_MODE=deterministic_non_streaming`,
**When** retrieval returns at least one citable chunk for a regular LibreChat
request,
**Then** the LiteLLM hook SHALL set the outgoing model request to
`stream=false`,
**And** the response SHALL be rendered once through the Markdown citation
renderer.

## AC-3 — General Chat Streaming Preserved

**Given** a regular LibreChat request where KB retrieval is disabled or
bypassed,
**When** the incoming request has `stream=true`,
**Then** the LiteLLM hook SHALL preserve streaming behavior,
**And** no citation registry SHALL be attached.

## AC-4 — No Citable Sources Fallback

**Given** retrieval returns chunks without valid source URLs,
**When** a KB answer would otherwise be generated,
**Then** the system SHALL return a deterministic "cannot answer reliably from
available knowledge sources" message,
**And** it SHALL log `kb_citations_no_citable_sources`.

## AC-5 — Widget Structured Sources

**Given** a widget or partner API request with citable chunks,
**When** the answer is rendered,
**Then** the response SHALL include structured sources derived from the same
registry implementation,
**And** no source URL from model text SHALL be used.

## AC-6 — Shared Library Is Canonical

**Given** citation extraction or rendering behavior changes,
**When** the test suite runs,
**Then** drift tests SHALL fail unless the LiteLLM deployed copy and portal
consumer are aligned with `klai-libs/citations`.

## AC-7 — Deployment Import Smoke

**Given** LiteLLM is recreated in production,
**When** the deploy workflow or runbook performs the post-recreate smoke,
**Then** `import klai_knowledge, klai_citations` SHALL succeed inside the
container before the deploy is considered healthy.

## AC-8 — Rollback Switch

**Given** `KLAI_KB_CHAT_RENDER_MODE=legacy_stream_guard`,
**When** a KB-enriched LibreChat request is made,
**Then** the previous guarded streaming path SHALL remain available during the
rollout window.

## AC-9 — Observability

**Given** a KB answer is rendered,
**When** citation rendering succeeds,
**Then** logs SHALL include whether Markdown or structured sources were
rendered and the count of rendered sources.

## AC-10 — Production Smoke

**Given** the implementation is deployed,
**When** a user asks "Hoe open is Klai?" in regular LibreChat against a KB with
citable Klai source documents,
**Then** the answer SHALL include a deterministic source list,
**And** the answer SHALL NOT include hallucinated source names or URLs.

