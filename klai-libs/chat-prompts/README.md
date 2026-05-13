# klai-chat-prompts

Single source of truth for the grounded chat system prompt used by both
`klai-retrieval-api` (`/synthesize` endpoint) and
`klai-portal/backend` (`partner_chat` service).

Owned by SPEC-RAG-MULTILINGUAL-CHAT-001.

## Why this exists

Before this library, the same ~15-line system prompt was duplicated in
both services with hardcoded `Als de gebruiker Nederlands schrijft,
antwoord je in het Nederlands` language switching. Two copies, no
enforcement of byte-equality, drift inevitable on the next edit.

This library exposes one constant — `GROUNDED_CHAT_SYSTEM_PROMPT` —
that both services import. A change to chat behaviour is a one-line
edit here, picked up by both services on next deploy.

A CI lint at the monorepo level rejects PRs that re-introduce a
hardcoded copy of the prompt anywhere outside this library.

## Usage

```python
from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

messages = [
    {"role": "system", "content": GROUNDED_CHAT_SYSTEM_PROMPT},
    # ... user/assistant turns
]
```

## When NOT to add a new prompt here

This library is for **cross-service chat prompts only**. Per-service
prompts (e.g. `coreference.py` query rewriter, `summarizer.py` meeting
summary) stay in their own service code; they are not duplicated and
have a single owner.

## Future evolution

Per the SPEC research: when prompt count grows beyond 1-2 cross-service
constants, or when A/B testing per prompt is needed, migrate this
package to a managed prompt platform (Langfuse, PromptLayer). Until
then, code-as-source is the cheaper path.
