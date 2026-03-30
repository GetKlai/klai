# Knowledge Blog Series -- Editorial Plan

Blog series about how Klai Knowledge works, based on the architecture and fundamentals docs.
Source material: `claude-docs/klai-knowledge-architecture.md` and `claude-docs/knowledge-system-fundamentals.md`.

Language: English (published on getklai.com/blog)
Author voice: Mark Vletter (see `klai-claude/rules/gtm/mark-tone-of-voice.md`)

---

## Three series, three audiences

### Series A: "How an organisation learns" (broad: product/management)

| # | Working title | Source sections | Status |
|---|---|---|---|
| A0 | Your company knows more than it can find | arch 12, fund 12 (the problem statement: tacit vs explicit knowledge, knowledge loss) | published |
| A1 | Why you should never delete knowledge | arch 3.4-3.5 (superseded_by, temporal dimensions) | published |
| A2 | The self-improving knowledge base | arch 8, arch 12 (gap detection, self-improving loop) | published |
| A3 | Five conversations your company has, five pipelines you need | fund 12 (communication as knowledge source) | published |
| A4 | Decisions deserve their own data model | fund 10 (Decision as 6th entity type) | published |
| A5 | The 15-minute correction window | fund 12 meetings (FRAME, correction window, kappa=0.36) | published |

### Series B: "Modelling knowledge" (mid: architects, knowledge managers)

| # | Working title | Source sections | Status |
|---|---|---|---|
| B1 | Evidence vs. claims: the distinction that changes everything | arch 3.1 (source_document vs knowledge_artifact) | published |
| B2 | Three axes, not one label | arch 3.2 (provenance, assertion mode, synthesis depth) | published |
| B3 | The self-managing taxonomy is a myth | arch 6.2-6.5 (BERTopic, outlier rates, human gate) | published |
| B4 | Quality happens at storage, not at search | arch 4.2, fund 13 (Contextual Retrieval, HyPE) | published |
| B5 | Why knowledge base search fails users | vocabulary gap, hybrid search, HyPE, query expansion | published |

> Note: originally planned B5 ("How a knowledge system handles uncertainty" — CWA vs OWA, assertion_mode) was replaced by the vocabulary gap topic. The uncertainty post is dropped.

### Series C: "Retrieval that works" (deep: engineers, AI builders)

Based on code audit of retrieval-api, knowledge-ingest, and litellm hook (March 2026).

| # | Working title | Source (actual code) | Status |
|---|---|---|---|
| C1 | How to find and prioritize knowledge base gaps | litellm/klai_knowledge.py `_classify_gap`, app_gaps.py | published |
| C2 | How to know if your knowledge base fix actually worked | gap_rescorer.py, gap_classification.py (SPEC-KB-015) | published |
| C3 | Not every question needs the knowledge base | klai-retrieval-api/services/gate.py `should_bypass`, cosine margin | published |
| C4 | Three signals, one answer | klai-retrieval-api/services/search.py 3-leg RRF, reranker.py, coreference.py | published |
| C5 | Not every document is the same | knowledge-ingest/content_profiles.py, context_strategies.py (includes two-phase ingest note) | published |
| ~~C6~~ | ~~Searchable in seconds, smart in minutes~~ | Merged into C5 as one-liner — not enough for standalone post | dropped |
| ~~C7~~ | ~~Personal knowledge: five things your AI should remember~~ | Topic too obvious for standalone post | dropped |

---

## Format principles

Each post should feel like it was written for that topic, not templated. Vary format intentionally.

### Opener variety

No two consecutive posts should open the same way. Available moves:
- **Narrative scene** — a specific person, a specific moment (A0: "A new colleague joins your team...")
- **Short scenario** — thing goes wrong, problem surfaces (A1: return policy, wiki updated, problem "solved")
- **Direct challenge** — call out the wrong assumption immediately (A4: "Ask any team why they chose their database...")
- **Time-based opening** — a timestamp or deadline that creates urgency (good for A5: "It is 14:32. The meeting just ended.")
- **Data point first** — lead with the number, then explain it (good for C-series)
- **Inversion** — start with what people believe, then immediately flip it (good for B3: "The self-managing taxonomy is a myth")

### Post spine variety

Match the structure to what the content actually is:

| Spine | When to use | Example |
|---|---|---|
| **Essay** — single argument that builds | One key insight with nuance | A1 (why knowledge evolves), A4 (decision model) |
| **Taxonomy** — N types each need different treatment | When the thing actually comes in N forms | A3 (five conversation types) |
| **Myth-bust** — claim → why people believe it → why it's wrong → what's true | Post title is already a refutation | B3 (self-managing taxonomy) |
| **Case build** — experiment → finding → implication | When you have a concrete result to share | C1 (31% of retrievals, TARG finding) |
| **Framework intro** — here is the model, here are its parts | When introducing a new mental model | B2 (three axes) |
| **Walkthrough** — follow one thing through a system step by step | Explaining a pipeline or process; reader walks alongside | C4 (a query through the retrieval pipeline) |
| **Before/after** — show the problem state, then the solved state | When the impact is concrete and visual | C6 (immediate upsert vs enriched) |
| **Analogy-driven** — one extended metaphor carries the post | When the concept is abstract but a good metaphor exists | (use sparingly, max 1 per series) |
| **Question cascade** — one question, each section peels back a layer | When "but what about X?" drives the logic naturally | C5 (why is my PDF chunked differently?) |

**Format distribution rule:** No two consecutive posts should use the same spine. If the last three posts were essays, the next one must not be. Check the series table before choosing.

### Closing variety

"What you can do today" with 3 bullet tips appears in A0, A1, A2, A4. Do not use it in every post.

Alternatives:
- **"Start here"** — one specific entry point, not a list (for posts where there is one right answer)
- **Objection handling** — "But does this mean X?" section (A1 already does this well)
- **The honest trade-off** — end with what you give up by doing it right (good for B-series)
- **No action section** — some posts earn the right to just end on the insight (use sparingly)
- **Open question** — close with the problem this post does not solve (threads into the next post)

### Section count

4–6 H2s is the range. Fewer, denser sections read as more confident. Many short sections read as listicle padding.

### 90/10 principle

Already introduced in A0. Do not re-explain it. Reference it by name in later posts where relevant.

### Per-post format notes (remaining posts)

| Post | Intended spine | Opener move | Closing move |
|---|---|---|---|
| A5 | Essay — one mechanism examined deeply | Time-based (14:32, meeting just ended) | No action section — end on the implication |
| B1 | Myth-bust | Inversion ("Not all sources are equal" is obvious; this is about what that actually means) | Trade-off — what you lose if you treat everything as equal |
| B2 | Framework intro | Direct challenge | "Start here" — one axis to start with |
| B3 | Myth-bust | Inversion (title is already the claim) | Objection handling |
| B4 | Case build | Data point (the cost of bad storage) | The honest trade-off |
| B5 | Essay | Scenario (system confidently wrong) | Open question |
| C1 | Essay | Observation (every failed query is data) | Open question (threads to C2) |
| C2 | Essay | Short scenario (you added the article, gap still showing) | No action section |
| C3 | Essay | Inversion (more retrieval = better, right?) | The honest trade-off |
| C4 | Walkthrough — follow one query through the full pipeline | Narrative scene (a user asks a follow-up question) | "Start here" |
| C5 | Question cascade — why is my PDF chunked differently? | Direct challenge (not every document is the same) | The honest trade-off |
| C6 | Before/after — immediate upsert vs async enrichment | Short scenario (user saves, searches immediately) | No action section |
| C7 | Taxonomy | Narrative scene (personal saves) | Open question |

---

## Cross-links between existing blog posts

The two existing posts (data residency + EU AI Act) are compliance-focused.
This knowledge series is a second content pillar: "how AI-powered knowledge management actually works."
Link from knowledge blogs to compliance blogs where relevant (e.g. GDPR erasure in C4 links to data residency post).

---

*Created: 2026-03-26*
*Updated: 2026-03-28 — code audit, B4/B5 marked published, C-series rewritten from actual codebase*
