---
paths:
  - "**/litellm*.yml"
  - "**/litellm*.yaml"
  - "deploy/litellm/**"
---
# LiteLLM

## Tier aliases (HARD)
Never use raw model names. Aliases are tier-/model-named, NEVER role-named (no `klai-eval-judge`, `klai-pipeline` etc). New tiers are added when an existing tier is structurally insufficient for a task.

| Alias | Backing model | Task |
|---|---|---|
| `klai-fast` | `mistral-small-2603` | Lightweight, high-volume, latency-sensitive. Bypasses custom_router. |
| `klai-primary` | `mistral-small-2603` | Standard quality, user-facing. Routed via custom_router (may upgrade to klai-large for tool-calls). |
| `klai-medium` | `mistral-medium-3.5` | Middle tier — used when klai-fast hits its output-token ceiling but klai-large is overkill. |
| `klai-large` | `mistral-large-2512` | Agentic, tool use, MCP flows. |
| `klai-bge-m3` | BGE-M3 on TEI/gpu-01 | Embeddings. Distinct model TYPE (not text-completion). |

## Provider swap
Switch all services by changing 3 entries in `deploy/litellm/config.yaml`, then `docker compose restart litellm`.

## vLLM routing
- Provider prefix: `hosted_vllm/` (NOT `openai/`).
- Always set `drop_params: true` in `litellm_settings`.
- Verify Complexity Router availability (`>= 1.74.9`).

## Health checks
- `/health/liveliness` — no auth, use for service-to-service checks.
- `/health` — requires valid virtual key (NOT master key).

## custom_router.py
- Content heuristics fire on ALL `klai-primary` calls. Internal services must use `klai-fast` to bypass.
- Never use `klai-primary` for background services processing document content with URLs.

## Mistral quota
- Tier 1: 4M tokens/month hard cap. 429 with `x-ratelimit-remaining-tokens-month: 0`.
- Credits don't buy quota. Resets 1st of month.

## Compose env
- Always verify resolved values: `docker compose config litellm | grep -A 30 'environment:'`
- `${WRONG_VAR}` silently injects wrong value — no error.
