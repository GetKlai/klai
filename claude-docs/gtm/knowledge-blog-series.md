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
| A3 | Five conversations your company has, five pipelines you need | fund 12 (communication as knowledge source) | planned |
| A4 | Decisions deserve their own data model | fund 10 (Decision as 6th entity type) | planned |
| A5 | The 15-minute correction window | fund 12 meetings (FRAME, correction window, kappa=0.36) | planned |

### Series B: "Modelling knowledge" (mid: architects, knowledge managers)

| # | Working title | Source sections | Status |
|---|---|---|---|
| B1 | Evidence vs. claims: the distinction that changes everything | arch 3.1 (source_document vs knowledge_artifact) | planned |
| B2 | Three axes, not one label | arch 3.2 (provenance, assertion mode, synthesis depth) | planned |
| B3 | The self-managing taxonomy is a myth | arch 6.2-6.5 (BERTopic, outlier rates, human gate) | planned |
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

## Cross-links between existing blog posts

The two existing posts (data residency + EU AI Act) are compliance-focused.
This knowledge series is a second content pillar: "how AI-powered knowledge management actually works."
Link from knowledge blogs to compliance blogs where relevant (e.g. GDPR erasure in C4 links to data residency post).

---

*Created: 2026-03-26*
