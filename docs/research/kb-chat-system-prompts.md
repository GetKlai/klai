# KB chat system prompt research

**Date:** 2026-04-05
**Applies to:** `klai-retrieval-api` (Chat), `klai-focus/research-api` (Focus notebooks)

---

## Context

The Klai chat interface (`/app/chat`) was returning answers that were too long and too verbose when querying the knowledge layer. This prompted a review of what makes a good base prompt for knowledge-backed chat — looking at both the OpenClaw project and the recently leaked Claude Code source code for inspiration, then applying Klai's own brand voice to the results.

---

## Sources reviewed

### 1. OpenClaw

OpenClaw is a self-hosted personal AI assistant framework (~348k GitHub stars as of early 2026). Its system prompt is assembled dynamically from `system-prompt.ts` — combining identity, tooling, skills, and workspace context files (`SOUL.md`, `IDENTITY.md`, `USER.md`).

The most relevant communication principles from the OpenClaw philosophy:

- **"Just answer."** Start with the answer, not a warm-up.
- **No filler.** "Great question!", "Certainly!", "Feel free to ask!" are explicitly forbidden.
- **Have actual opinions.** Honest disagreement is better than false validation.
- **Earn trust through competence**, not through being nice.
- **Match the user's register.** Technical user: technical answer. Casual: conversational.

Note: OpenClaw is a *personal* assistant — it may supplement with general knowledge freely. A knowledge-backed chat assistant has a hard boundary at its source material. The communication style transfers; the scope policy does not.

### 2. Claude Code source leak (March 31, 2026)

Anthropic accidentally shipped a 59.8 MB JavaScript sourcemap in `@anthropic-ai/claude-code` v2.1.88. The ~512,000-line TypeScript codebase was mirrored across GitHub. Anthropic confirmed it as a packaging error.

Key prompt patterns from the leaked source:

- **Lead with the answer.** "Responses optimized for CLI display — leading with answers over reasoning, limiting responses to essential information."
- **Honest disagreement.** "It is best for the user if Claude honestly applies rigorous standards and disagrees when necessary, even if it may not be what they want to hear."
- **Anti-scope-creep.** "Only make changes that are directly requested or clearly necessary."
- **No time estimates.** "Never give time estimates or predictions for how long tasks will take."
- **Anti-narration.** "Do not narrate routine, low-risk tool calls — just call the tool."
- **Frustration detection** uses regex pattern matching, not LLM inference — faster and cheaper.

### 3. RAG/support prompt best practices (general research)

The canonical structure for a knowledge-grounded chat prompt:

```
[Language directive]
[Identity]
[How to answer — style and length]
[How to cite sources]
[What to do when the answer is missing]
```

Key findings:

- Start constraints with negatives first — the model stops before it invents.
- Require citation for every factual claim — uncited claims are implicitly disallowed.
- The "I don't know" fallback is the single most important directive for avoiding hallucination. Make it explicit and specific: "Say it plainly, then offer a next step."
- Fewer high-quality retrieved chunks outperform many low-quality ones.
- Chain-of-thought is useful for complex multi-source questions; overkill for simple ones.

---

## What we changed

### Before

All five system prompts were generic, corporate, and language-ambiguous:

```
You are a research assistant. Answer the user's question using only the provided source excerpts below.
If the answer is not found in the provided sources, respond with:
"Ik kan dit niet vinden in de geselecteerde documenten."
Do not use any knowledge beyond what is explicitly present in the sources.
Always cite which source and page your answer is based on.
```

Problems:
- No explicit language directive — model defaulted inconsistently
- "Ik kan dit niet vinden in de geselecteerde documenten" hardcoded in Dutch regardless of user language
- No answer-length guidance — model gave exhaustive answers by default
- No URL citation support in Chat mode
- Tone was generic and corporate, not Klai

### After

All prompts now share a consistent structure. Example (Chat / `synthesis.py`):

```
[CRITICAL] Respond in the language of the user's question.
Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands.
If the user writes English, respond in English. Never switch mid-conversation.

You are Klai AI, a knowledge assistant. You answer questions based on the knowledge base chunks provided.

## How to answer
Start with the answer. No warm-up, no rephrasing the question, no 'great question!'
Simple question: 1-3 sentences. Complex question: the core answer first, then the detail.
Be direct. Be honest. If the sources say something unexpected, say it.

## How to cite
Every factual claim gets a [n] citation where n is the chunk number.
If a chunk includes a URL or help page link, include it: [n] (https://...).
If sources contradict each other, say so — don't pick a side silently.

## When the answer isn't there
Say it plainly: 'That's not in the knowledge base.'
Don't guess. Don't fill the gap with general knowledge.
If you're partially sure, say that too: 'The knowledge base touches on this, but doesn't fully answer it.'
```

---

## Key decisions

| Decision | Rationale |
|---|---|
| `[CRITICAL]` language directive in both NL and EN | Models respect explicit dual-language instructions more reliably than a single "respond in the user's language" line |
| "That's not in the knowledge base." (not an apology) | Klai brand voice: honest, direct, not corporate. No "I'm afraid I wasn't able to locate..." |
| URL citation support in Chat (`[n] (https://...)`) | Help pages and external sources are frequently indexed — surfacing their URLs directly saves the user a click |
| "If you're partially sure, say that too" | Partial matches are common in KB retrieval — forcing a binary found/not-found hides useful partial information |
| No filler, no preamble | Consistent with OpenClaw SOUL.md philosophy and Claude Code leaked prompt — validated independently by two sources |
| Answer length tied to question complexity | "1-3 sentences for simple questions" gives the model a concrete heuristic rather than a vague "be concise" instruction |

---

## Files changed

| File | Prompt |
|---|---|
| `klai-retrieval-api/retrieval_api/services/synthesis.py` | Chat KB (retrieval-api) |
| `klai-focus/research-api/app/services/retrieval.py` | Focus NARROW, BROAD, BROAD_FOCUS_ONLY, WEB |

Commit: `6e5f065` — deployed to core-01 via CI on 2026-04-05.
