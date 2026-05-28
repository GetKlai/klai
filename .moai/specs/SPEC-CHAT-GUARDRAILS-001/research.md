# Research — SPEC-CHAT-GUARDRAILS-001

Created: 2026-05-28

## Trigger

Responsible disclosure on 2026-05-18 showed that the public Klai widget accepted a single-message direct prompt-injection payload using fake output delimiters and `GODMODE`-style format hijacking. The model produced prohibited operational explosive guidance after an initial refusal. A hotfix now blocks this concrete path in `partner_chat`, but the systemic problem is broader: Klai has multiple LLM entrypoints and no shared input/context/output safety boundary.

## External research summary

Primary sources reviewed:

| Source | Relevant finding |
|---|---|
| OWASP Top 10 for LLM Applications 2025 | LLM01 remains Prompt Injection. OWASP explicitly treats user input, retrieved content, tool output, and agent context as injection surfaces; mitigation requires defense-in-depth, not prompt text alone. <https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf> |
| Microsoft Azure Prompt Shields | Separates direct user prompt attacks from indirect attacks in grounded documents; describes "spotlighting" to make external documents less trustworthy to the model. <https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/content-filter-prompt-shields> |
| AWS Bedrock Guardrails | Provides harmful-content filters, denied topics, and prompt-attack filters; supports scoping guard evaluation to user-provided sections through guard-content tags. <https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-prompt-attack.html> |
| OpenAI Moderation | Recommends pre/post moderation of text/image inputs with `omni-moderation-latest`, including illicit and violent-illicit categories relevant to weapons/explosives. <https://platform.openai.com/docs/guides/moderation/overview> |
| NVIDIA NeMo Guardrails | Uses distinct input, retrieval, execution, dialog, and output rails. The retrieval-rail concept maps directly to Klai's RAG chunk handling. <https://docs.nvidia.com/nemo/guardrails/0.12.0/user-guides/configuration-guide.html> |
| Lakera Guard | Productizes direct/indirect prompt defense plus content moderation and data-leakage prevention; specifically scans retrieved/reference documents. <https://docs.lakera.ai/docs/defenses> |
| NIST AI 600-1 | Frames this as a lifecycle risk-management problem: measure, monitor, log, evaluate, and govern GenAI risks rather than relying on one technical control. <https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence> |

Conclusion: the target architecture should have explicit rails at every untrusted boundary:

1. input rail: user message before retrieval/model,
2. retrieval rail: page context, KB chunks, web snippets, tool output before prompt assembly,
3. output rail: generated content before user visibility,
4. execution rail: tool calls/actions before side effects,
5. telemetry/eval rail: logging, red-team regression suite, rate-limit/ban signals.

## Codebase inventory

### User-facing chat paths

| Path | Current owner | LLM boundary | Current safety state |
|---|---|---|---|
| Public widget + partner chat | `klai-portal/backend/app/api/partner.py`, `app/services/partner_chat.py` | Direct httpx call to LiteLLM `/v1/chat/completions` | Hotfix added local regex/category input/output checks. Needs centralization. |
| LibreChat / agent chat via LiteLLM | `deploy/litellm/klai_knowledge.py` pre/post hooks, `deploy/litellm/custom_router.py` | LiteLLM callbacks wrap most `klai-primary` chat traffic | Has KB grounding, citation rendering, model routing, template injection. No general prompt-injection/content safety rail. |
| LiteLLM non-KB chat | `deploy/litellm/config.yaml` callbacks | Same proxy, but hooks can early-return | Direct safety should live in LiteLLM callback too, because it is the broadest choke point. |
| Retrieval API synthesis | `klai-retrieval-api/retrieval_api/services/synthesis.py` | Streams to LiteLLM and immediately yields tokens | Output currently streams before full safety evaluation; needs buffering or chunk-gated output. |
| Retrieval coreference rewrite | `klai-retrieval-api/retrieval_api/services/coreference.py` | Non-streaming LiteLLM rewrite | Internal helper; should use input/context safety but output should fail-closed to original query on unsafe/malformed result. |

### Background and ingestion LLM paths

| Path | File | Risk |
|---|---|---|
| Contextual enrichment | `klai-knowledge-ingest/knowledge_ingest/enrichment.py` | User/connector documents become prompt input; indirect injection can poison summaries or labels that later enter retrieval. |
| Selector/login detection | `klai-knowledge-ingest/knowledge_ingest/selector_ai.py` | DOM content can contain adversarial instructions; output is a CSS selector used operationally. Needs strict output validator. |
| Taxonomy/content/description classification | `taxonomy_classifier.py`, `content_labeler.py`, `description_generator.py` | Batch classifiers can be manipulated by source content; should use context rail and JSON schema validation. |
| Graphiti/OpenAI-compatible clients | `knowledge_ingest/graph.py`, `retrieval_api/services/graph_search.py` | Third-party library calls bypass direct httpx wrappers; needs provider-level/LiteLLM-level safety or constrained use policy. |
| Scribe summarizer | `klai-scribe/scribe-api/app/services/summarizer.py` | Transcript is untrusted user content; prompt injection can alter summary or extract hidden/system content. |

### Existing patterns to reuse

| Pattern | Existing file |
|---|---|
| Internal effective config endpoint + 30s Redis cache | `SPEC-CHAT-TEMPLATES-001`, `app/services/litellm_cache.py`, `deploy/litellm/klai_knowledge.py::_get_templates` |
| Fail-open cache invalidation for chat configuration | `app/services/litellm_cache.py` |
| Shared library mounted into LiteLLM container | `deploy/docker-compose.yml` bind mounts for `klai_chat_prompts`, `klai_citations`, `klai_service_auth` |
| Post-call citation composition for non-streaming/streaming | `partner_chat.py`, `deploy/litellm/klai_knowledge.py` |
| Security spec style | `SPEC-SEC-SSRF-001`, `SPEC-SEC-CROSS-TENANT-FOLLOWUP-001` |

## Architectural decision

Build a central `klai-libs/llm-safety` Python library and thin service adapters around it.

Why library-first:

- It can be imported by portal-api, retrieval-api, scribe-api, knowledge-ingest, and vendored/mounted into LiteLLM like existing shared libs.
- It keeps deterministic policy and vendor result normalization in one place.
- It avoids putting all enforcement behind an HTTP microservice that would become a new availability dependency on every chat token.

Vendor integration should be pluggable:

- Phase 1: deterministic local policy plus OpenAI moderation for harmful content if configured.
- Phase 2: prompt-attack provider (`Lakera`, `Azure Prompt Shields`, `Bedrock Guardrails`, or self-hosted NeMo/Llama Guard) behind the same interface.
- Phase 3: shadow-mode comparison and provider selection based on Klai traffic.

Default runtime posture:

- Public widget: fail-closed for input and output.
- Authenticated partner/LibreChat user chat: fail-closed for high-confidence harmful content, fail-soft refusal for prompt-injection attempts.
- Internal background enrichment/classification: fail-closed to deterministic fallback when output is unsafe or invalid.
- Vendor unavailable: configurable per caller, but public widget must refuse rather than silently continue.

## Current hotfix limitations

The 2026-05-28 hotfix in `partner_chat.py` is intentionally narrow:

- It catches the reported payload family and some hazardous-output cases.
- It does not cover obfuscation, translated jailbreak variants outside the pattern set, multi-turn grooming, indirect prompt injection in retrieved chunks, tool-output injection, or non-widget routes.
- It should be replaced by calls into the shared safety library once this SPEC lands.
