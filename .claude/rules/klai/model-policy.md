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

Klai's LiteLLM proxy exposes these aliases — use ONLY these:

| Alias | Maps to | Use for |
|---|---|---|
| `klai-primary` | Mistral Small (EU) | Default for most tasks |
| `klai-fast` | Mistral Nemo (EU) | Fast, lightweight tasks |
| `klai-large` | Mistral Large (EU) | Complex reasoning |

Example — Python:
```python
# WRONG
model: str = "gpt-4o-mini"

# CORRECT
model: str = "klai-primary"
```

Example — any config/env:
```bash
# WRONG
SUMMARIZE_MODEL=gpt-4o-mini

# CORRECT
SUMMARIZE_MODEL=klai-primary
```
