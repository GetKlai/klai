---
paths:
  - "**/litellm*.yml"
  - "**/litellm*.yaml"
  - "deploy/litellm/**"
---
# LiteLLM

## Tier aliases (HARD)
Never use raw model names. Only `klai-fast`, `klai-primary`, `klai-large`, `klai-pipeline`.

| Alias | Mistral (default) | Task |
|---|---|---|
| `klai-fast` | `mistral-small-2603` | Lightweight, high-volume, latency-sensitive |
| `klai-primary` | `mistral-small-2603` | Standard quality, user-facing |
| `klai-large` | `mistral-large-2512` | Agentic, tool use, MCP flows |
| `klai-pipeline` | `mistral-small-2603` | Internal services (bypasses custom_router) |

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
- Content heuristics fire on ALL `klai-primary` calls. Internal services must use `klai-pipeline` to bypass.
- Never use `klai-primary` for background services processing document content with URLs.

## Mistral quota
- Tier 1: 4M tokens/month hard cap. 429 with `x-ratelimit-remaining-tokens-month: 0`.
- Credits don't buy quota. Resets 1st of month.

## Compose env
- Always verify resolved values: `docker compose config litellm | grep -A 30 'environment:'`
- `${WRONG_VAR}` silently injects wrong value — no error.
