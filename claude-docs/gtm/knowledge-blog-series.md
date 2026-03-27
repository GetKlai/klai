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
| B4 | Quality happens at storage, not at search | arch 4.2, fund 13 (Contextual Retrieval, HyPE) | planned |
| B5 | How a knowledge system handles uncertainty | fund 11 (CWA vs OWA, confidence, assertion_mode) | planned |

### Series C: "Retrieval that works" (deep: engineers, AI builders)

| # | Working title | Source sections | Status |
|---|---|---|---|
| C1 | Why 31% of all retrievals make your answer worse | fund 15 (TARG, pre-retrieval gate, Self-RAG) | planned |
| C2 | Dense + sparse: when exact terms matter | fund 14 (BGE-M3, hybrid search, RRF) | planned |
| C3 | How Graph RAG fails for B2B knowledge bases | arch 5.3 (LightRAG token costs, regression on facts) | planned |
| C4 | One collection for all tenants | arch 5.1 (Qdrant multitenancy, GDPR erasure) | planned |
| C5 | Five query intents, three backends | fund 15 (routing, coreference, multi-turn) | planned |

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
| C1 | Case build | Data point first (31% number) | No action section |
| C2 | Framework intro | Direct challenge | "Start here" |
| C3 | Myth-bust | Inversion | Trade-off |
| C4 | Essay | Scenario | Trade-off |
| C5 | Taxonomy | Observation | "Start here" |

---

## Cross-links between existing blog posts

The two existing posts (data residency + EU AI Act) are compliance-focused.
This knowledge series is a second content pillar: "how AI-powered knowledge management actually works."
Link from knowledge blogs to compliance blogs where relevant (e.g. GDPR erasure in C4 links to data residency post).

---

*Created: 2026-03-26*
