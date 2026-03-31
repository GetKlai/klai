# Klai Model Policy

**[HARD] Never use OpenAI, Anthropic, or other US cloud provider model names anywhere in Klai code.**

Klai is a privacy-first, EU-only platform. Using OpenAI/Anthropic model names in code would:
- Route data to US cloud providers at runtime
- Violate GDPR and Klai's data residency guarantee
- Break the entire product promise

## Forbidden model names

Never use these (or any variant) as default values, config defaults, or hardcoded strings:
- `gpt-4`, `gpt-4o`, `gpt-4o-mini`, `gpt-3.5-turbo`, or any `gpt-*`
- `claude-*` (Anthropic API — not the same as running Claude locally)
- `text-davinci-*`, `text-embedding-*`
- Any model name from openai.com, anthropic.com, or cohere.com

## Correct model aliases (LiteLLM)

Klai's LiteLLM proxy exposes three tier aliases — use ONLY these:

| Alias | Mistral | Claude equivalent | Use for |
|---|---|---|---|
| `klai-fast` | `mistral-small-2603` (Small 4) | `claude-haiku-4-5` | Lightweight, high-volume, latency-sensitive |
| `klai-primary` | `mistral-small-2603` (Small 4) | `claude-sonnet-4-6` | Standard quality, user-facing |
| `klai-large` | `mistral-large-2512` (Large 3) | `claude-sonnet-4-6` | Agentic, tool use, MCP flows |

### Which tier to use

| Task | Tier | Reason |
|---|---|---|
| Coreference / query rewrite | `klai-fast` | 1-sentence output, latency-sensitive |
| LLM enrichment (HyPE, context prefix) | `klai-fast` | Short structured output, high volume |
| Graphiti entity extraction + graph search | `klai-fast` | Structured extraction, background |
| KB chat synthesis | `klai-primary` | User-facing, 6k token context, citations |
| Meeting / transcription summarization | `klai-primary` | Quality matters, moderate length |
| LibreChat general chat | `klai-primary` | Main UX — custom_router may upscale |
| MCP tool use / agentic reasoning | `klai-large` | Multi-step function calling |

### Tier model rationale (researched 2026-03-31)

**Why `klai-fast` and `klai-primary` map to the same Mistral model:**
Mistral Small 4 (`mistral-small-2603`) is a 119B MoE with only 6.5B active parameters — it
combines low inference cost with quality that matches or exceeds older Large models. It handles
both lightweight tasks (fast tier) and user-facing synthesis (primary tier) at $0.15/$0.60 per M
tokens, one-third the cost of Large. The tiers remain separate aliases so each can be routed to a
different provider model independently (e.g. Claude Haiku vs Sonnet, or vLLM fast vs slow model).

**Why `klai-large` uses Mistral Large 3:**
41B active parameters (675B MoE total) with strong function-calling reliability. Justified for
low-volume agentic flows where correctness matters more than latency or cost.

**Mistral Nemo is retired.** It was the July 2024 predecessor to Small 4. Do not use
`open-mistral-nemo` in new configurations.

Example — Python:
```python
# WRONG
model: str = "gpt-4o-mini"

# CORRECT — pick the right tier
coreference_model: str = "klai-fast"    # lightweight
synthesis_model:   str = "klai-primary" # user-facing quality
```

Example — any config/env:
```bash
# WRONG
SUMMARIZE_MODEL=gpt-4o-mini

# CORRECT
SUMMARIZE_MODEL=klai-primary
```
