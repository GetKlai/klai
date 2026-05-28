---
id: SPEC-CHAT-GUARDRAILS-001
version: 0.1.0
status: draft
created: 2026-05-28
updated: 2026-05-28
author: Codex
priority: critical
lifecycle: spec-first
---

# SPEC-CHAT-GUARDRAILS-001 — Central LLM Safety Guardrails

## HISTORY

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1.0 | 2026-05-28 | Codex | Draft based on responsible-disclosure hotfix, code inventory, and external guardrail research. |

## Overview

Klai currently protects LLM behaviour mostly through system prompts, RAG grounding, citation post-processing, and a hotfix in the public widget path. That is insufficient for direct prompt injection, indirect prompt injection through retrieved/page/tool data, and unsafe generated output.

This SPEC introduces a central safety layer with input, retrieval-context, output, and execution rails that all LLM entrypoints must use.

## Goals

- Prevent the public widget class of prompt-injection jailbreak from reaching retrieval or the model.
- Enforce the same baseline policy across widget, partner chat, LibreChat/agent traffic, retrieval synthesis, scribe summarization, and ingestion LLM helpers.
- Add provider-backed moderation/prompt-attack classification behind a single interface.
- Preserve product availability with explicit fail-open/fail-closed modes per trust boundary.
- Add red-team regression coverage and telemetry so bypass attempts become observable.

## Non-goals

- Building the full admin UI for custom per-tenant guardrail rules in this SPEC. This SPEC creates backend/runtime foundations; UI can follow.
- Replacing all model-provider native safety controls. Provider controls remain defense-in-depth.
- Guaranteeing perfect prompt-injection prevention. The requirement is layered reduction plus bounded blast radius.

## Requirements

### REQ-1 — Shared safety library

- The repo SHALL add `klai-libs/llm-safety` exposing:
  - `check_input(request: SafetyRequest) -> SafetyDecision`
  - `check_context(request: SafetyRequest) -> SafetyDecision`
  - `check_output(request: SafetyRequest) -> SafetyDecision`
  - `refusal_message(locale_or_text: str, reason: str) -> str`
- `SafetyDecision` SHALL include `allowed`, `action`, `reason`, `categories`, `confidence`, `provider`, and `safe_replacement`.
- The library SHALL have no FastAPI dependency.

### REQ-2 — Deterministic baseline policy

- The library SHALL include deterministic checks for:
  - known prompt-injection delimiters and role-hijack language,
  - instruction hierarchy override requests,
  - system/developer prompt extraction attempts,
  - CBRN/weapons/explosives operational guidance,
  - illegal drug synthesis,
  - CSAM/sexual minors,
  - targeted violence and self-harm instructions,
  - encoded/obfuscated wrappers that should be routed to provider checks.
- Deterministic checks SHALL be unit-tested independently from provider checks.

### REQ-3 — Provider-backed classification

- The library SHALL support a provider interface with at least one configured provider in production.
- Provider calls SHALL have strict timeouts and structured error results.
- Provider unavailable behaviour SHALL be caller-configurable:
  - widget input/output: fail-closed,
  - internal enrichment/classification: fail-closed to fallback,
  - low-risk authenticated chat: fail-soft according to config.

### REQ-4 — Portal widget and partner chat

- `partner.py::chat_completions` SHALL call `check_input` before retrieval and before the LiteLLM call.
- `partner_chat.py::retrieve_context` SHALL call `check_context` on page context and retrieved chunks before prompt assembly.
- `chat_completion_non_streaming` and `chat_completion_streaming` SHALL call `check_output` before returning content to the caller.
- Streaming widget output SHALL NOT forward model text before output safety has run.

### REQ-5 — LiteLLM hook coverage

- `deploy/litellm/klai_knowledge.py` SHALL apply:
  - input safety after org/user resolution and before template/retrieval fetch,
  - context safety after retrieval and before KB injection,
  - output safety in non-streaming and streaming post-call hooks.
- `deploy/litellm/custom_router.py` SHALL NOT become a safety owner; it may read safety metadata only for routing/metrics.
- Safe refusal responses SHALL preserve the OpenAI/LiteLLM response shape expected by LibreChat.

### REQ-6 — Retrieval API coverage

- `retrieval_api/services/coreference.py` SHALL check prompt-injection/harmful input before rewrite; unsafe input returns the original query and logs a blocked event.
- `retrieval_api/services/synthesis.py` SHALL scan retrieved context before building the prompt and scan generated output before yielding to clients.
- If synthesis output must be buffered to enforce output safety, the API SHALL document this latency tradeoff and keep streaming shape compatible.

### REQ-7 — Ingestion and scribe coverage

- `knowledge_ingest` LLM helper functions SHALL call context/output checks before accepting LLM-derived metadata.
- Output that drives operational behaviour, such as CSS selectors, SHALL pass deterministic validators in addition to safety checks.
- `scribe-api` summarization SHALL treat transcripts as untrusted context and shall block or neutralize transcript instructions that try to control the summarizer.

### REQ-8 — Telemetry and incident response

- Every block or provider error SHALL emit structured logs with `service`, `surface`, `org_id` when available, `reason`, `provider`, `mode`, and `request_id`.
- Public widget safety blocks SHALL be rate-limit eligible by widget/session/IP hash.
- Raw harmful content SHALL NOT be logged unless an explicit privacy-mode setting permits redacted capture.

### REQ-9 — Red-team regression harness

- The repo SHALL include a guardrail test corpus covering:
  - the 2026-05-18 reported payload family,
  - common direct jailbreaks,
  - indirect RAG injections in chunks/page context/tool output,
  - encoded/obfuscated variants,
  - multi-turn grooming,
  - benign false-positive controls.
- CI SHALL run deterministic tests on every PR and provider-backed smoke tests when credentials are present.

### REQ-10 — Deployment controls

- New settings SHALL support `LLM_SAFETY_MODE={off,shadow,enforce}` and per-surface overrides.
- Production public widget SHALL default to `enforce`.
- Rollback SHALL be possible by switching non-public surfaces to `shadow`; public widget may not be rolled back to unguarded without an explicit emergency flag.
