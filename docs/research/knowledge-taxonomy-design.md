# Knowledge Taxonomy Design: Structure, Value, and Best Practices

> Research date: 2026-04-05
> Status: Background research — informs Klai knowledge architecture decisions
> Scope: What taxonomies are, why they matter, how to design them well, and what they would add to Klai specifically

---

## Summary

A taxonomy is a hierarchical classification system that organises information into categories and subcategories. In knowledge systems, taxonomies are the structural layer that determines whether information is findable, whether AI retrieval is accurate, and whether knowledge silos form.

The key finding: **neither a pure hierarchy nor flat tags alone are sufficient**. Best-practice knowledge systems use a three-layer hybrid — hierarchy for navigation, facets for filtering, tags for the long tail.

---

## 1. Why taxonomies matter

### Findability impact

- Teams implementing effective taxonomy see search success rates improve by **20–40%** within the first month
- Effective taxonomy reduces employee search time by **60%**
- McKinsey: robust knowledge management reduces time spent searching by **35%** and measurably boosts productivity

### The cost of not having a taxonomy

- IDC estimates Fortune 500 companies lose **$31.5 billion per year** from failing to share relevant information
- The average knowledge worker spends **2 hours per week recreating information that already exists**
- **67% of collaboration failures** trace directly to organisational silos, not lack of effort or tools (Harvard Business)
- Without shared structure, users must alter their search behaviour per system — compounding the loss

### Concrete ROI example

For a 200-employee company: 160 hours of reduced search time per team per month × 5 teams × 12 months = **9,600 hours recovered annually** ≈ $480K in productivity gains from taxonomy alone.

---

## 2. The three taxonomy structures

### 2.1 Hierarchical tree

A parent-child structure. Each category contains subcategories.

```
Products
├── Hardware
│   ├── Laptops
│   └── Servers
└── Software
    ├── Licences
    └── Updates
```

**Strengths:** intuitive navigation, clear overview, good for onboarding
**Weaknesses:** rigid — content that belongs in multiple categories breaks the model; becomes unwieldy at depth

**Hard limit: 3 levels maximum.** Each additional level reduces discoverability by approximately 50%. At level 5, over 90% of users have abandoned the search. The rule of thumb: Category → Subcategory → Content. If a 4th level is needed, use a facet instead.

---

### 2.2 Faceted taxonomy

Multiple independent classification dimensions applied simultaneously. Instead of one tree, each dimension is a separate axis.

```
Dimension: Audience    → [Developer] [Sales] [Support] [Management]
Dimension: Content type → [How-to] [Reference] [Policy] [Template]
Dimension: Product     → [Portal] [API] [Mobile] [Integrations]
Dimension: Region      → [NL] [DE] [EU] [Global]
```

A document is tagged across all relevant dimensions: `Developer × How-to × API × EU`

**Strengths:** precise, flexible filtering; scales to heterogeneous, broad knowledge bases; much shallower than trees; supports multiple audiences
**Weaknesses:** complex to design — requires content analysis upfront; cannot be designed without understanding the content domain

**Design limits:** no more than 10–15 top-level facets; no more than 2–4 levels per facet.

---

### 2.3 Flat tags

Free-form keywords with no hierarchy. Fast to assign, minimal governance overhead.

**Strengths:** captures long-tail concepts; user-driven categorisation; good for niche topics
**Weaknesses:** inconsistency at scale (synonyms, typos, drift); does not scale without curation

---

## 3. The hybrid approach — best practice

Systems achieving 85%+ search success rates use all three layers:

| Layer | Type | Purpose |
|---|---|---|
| Main navigation | Hierarchy (max 3 levels) | Browsing, onboarding, overview |
| Search refinement | Facets | Multi-dimensional filtering |
| Annotation | Tags | Flexible long-tail coverage |

**Practical result:** support teams answer questions 3× faster; average search time drops from 8 minutes to under 3 minutes per query.

---

## 4. Taxonomy design principles

### 4.1 Start with the user, not the org chart

The most common mistake: using the internal department structure as the taxonomy. Users search by task or problem — not by which team owns the content. A taxonomy that mirrors HR org charts is optimised for the organisation, not the user.

### 4.2 Terms must be unambiguous and mutually exclusive

"Customers" and "Accounts" as separate categories create confusion. Choose one canonical term; make the other a synonym or alias. Every term should have a single, clear meaning.

### 4.3 Avoid polyhierarchy

Polyhierarchy is when the same concept appears in multiple locations in the tree (e.g., "GDPR" under both "Legal" and "Security"). This breaks the assumption that terms are mutually exclusive. If a concept genuinely spans multiple parents, use a facet or tag instead of duplicating the node.

### 4.4 Taxonomy determines search quality more than the algorithm

The search algorithm ranks results using the contextual relationships that the taxonomy provides — parent categories, related topics, audience associations, product hierarchies. A weak taxonomy produces poor search results regardless of the algorithm. Fixing the algorithm without fixing the taxonomy is wasted effort.

### 4.5 Every taxonomy decision starts with three questions

1. **Who is the audience?** (and how do they search?)
2. **What are we taxonomising?** (content type, domain, volume)
3. **Why are we doing it?** (navigation, search, AI retrieval, governance?)

---

## 5. Taxonomy vs. ontology vs. knowledge graph

These are often confused. They exist on a spectrum of semantic richness:

```
Term list → Taxonomy → Thesaurus → Ontology → Knowledge Graph
                        ↑ each step adds more relationships and rules ↑
```

| Feature | Taxonomy | Ontology | Knowledge Graph |
|---|---|---|---|
| Structure | Hierarchy (tree) | Network of relationships | Nodes + edges (instances) |
| Complexity | Low–medium | High | Very high |
| Primary use | Classification, navigation, filtering | Reasoning, inference | AI, cross-domain queries |
| Example | Product category tree | Medical knowledge model | Google Knowledge Graph |

For practical knowledge systems, **taxonomy is the starting point** with immediate ROI. Ontologies and knowledge graphs build on top of it for advanced AI applications.

---

## 6. Taxonomy and AI retrieval

This is the most critical dimension for modern AI-powered knowledge systems.

### The problem with pure vector search

LLMs are only as reliable as the structure behind the data they draw from. Without a semantic foundation, even advanced models can:
- Misclassify content
- Surface irrelevant results
- Generate factually incorrect answers (hallucinations)

Hallucinations occur primarily because a model lacks grounded, authoritative knowledge about a domain.

### What taxonomy adds to RAG

**GraphRAG** (vector search + taxonomy/ontology) combines semantic similarity with logical structure:

- Taxonomy-enhanced retrievers identify both thematically and semantically relevant documents
- KG-RAG (Knowledge Graph-based RAG) reduces hallucinations by traversing a structured knowledge graph
- Search precision can reach **99%** with deterministic knowledge graph approaches, vs. ~70–80% with pure vector search

**Core principle:** taxonomies and ontologies *constrain* what an AI system can assert — they anchor output in authoritative domain knowledge.

### GraphRAG vs. naive RAG

| Approach | Mechanism | Typical precision |
|---|---|---|
| Naive RAG | Vector similarity only | ~70–80% |
| Metadata-filtered RAG | Vector + taxonomy filters | ~85–90% |
| GraphRAG | Vector + taxonomy + relationship traversal | Up to 99% |

---

## 7. When is a taxonomy most valuable?

From the research, four scenarios show the largest impact:

1. **Large, growing knowledge bases** — without structure, chaos grows exponentially with content volume
2. **Multiple data sources or silos** — a shared taxonomy provides cross-system consistency; users can search once across all sources
3. **AI-driven retrieval (RAG)** — taxonomy raises precision and reduces hallucinations
4. **External-facing navigation** (search, browse, filter for end users) — hierarchical browsing outperforms free search alone for users who don't know exact terminology

---

## 8. Practical example — SaaS knowledge system

```
HIERARCHY (navigation, max 3 levels):
├── Getting started
├── Product documentation
│   ├── Connectors
│   ├── Knowledge base
│   └── Settings
├── Guides
└── Troubleshooting

FACETS (search filters):
├── Audience:      [Admin] [End user] [Developer]
├── Content type:  [Tutorial] [Reference] [FAQ] [Release note]
└── Skill level:   [Beginner] [Advanced]

TAGS (free-form, long tail):
  notion-integration, SSO, bulk-upload, GDPR, rate-limit, ...
```

---

## 9. Common failure modes

| Failure | Symptom | Fix |
|---|---|---|
| Org-chart taxonomy | Users can't find anything; search fails | Redesign around user tasks |
| Too deep (>3 levels) | >90% drop-off before reaching content | Cap at 3 levels; use facets for depth |
| No governance | Tags proliferate with synonyms and typos | Assign taxonomy owner; add synonym mapping |
| Polyhierarchy | Same concept in multiple tree locations | Move to facet or tag |
| Ad hoc taxonomy | Inconsistent, gaps, not extensible | Design upfront with documented principles |
| Taxonomy mirrors search algorithm | Good search algorithm, poor results | Fix taxonomy first |

---

## Conclusion

The value of a taxonomy in a knowledge system is threefold:

1. **Operational:** less search time, less duplicated work, lower support load
2. **Organisational:** break down silos, shared vocabulary, content governance
3. **Technical (AI):** higher retrieval precision, fewer hallucinations, richer contextual answers

The biggest risk is an ad-hoc approach: taxonomies that are not consistent, extensible, or complete deliver none of these benefits. The biggest design mistake is optimising for the organisation rather than the user.

**The recommended starting structure:** hierarchy (max 3 levels) for navigation + facets (max 10–15 top-level) for filtering + free tags for the long tail. Design all three from the user's search behaviour, not from internal structure.

---

## 10. Klai: current state and taxonomy gaps

This section applies the research above to the Klai knowledge architecture as of April 2026. Source documents: `docs/architecture/klai-knowledge-architecture.md` and `docs/architecture/knowledge-ingest-flow.md`.

### 10.1 What Klai already has (taxonomy-relevant)

The Klai system contains multiple implicit taxonomies — classification layers that are used internally but not yet assembled into a coherent, navigable structure.

**Content type — flat taxonomy, internal only**

`kb_article | pdf_document | meeting_transcript | 1on1_transcript | email_thread | web_crawl | faq | api_doc | changelog | unknown`

Drives the content profile (chunk size, HyPE strategy, context window approach) and the evidence-scoring tier in §7.4 of the architecture. Not user-facing; users cannot filter by content type at retrieval time.

**Assertion mode — flat epistemic taxonomy**

`factual | procedural | quoted | belief | hypothesis`

Classifies what kind of claim a piece of content makes. Stored in YAML frontmatter and Qdrant payload. Currently all flat weights (1.00) in evidence scoring. Not exposed for navigation or filtering.

**Provenance type + synthesis depth — two orthogonal axes**

`observed | extracted | synthesized | revised` × `0–4`. Good internal metadata; not used for navigation.

**Entity extraction — embryonic domain facets**

Graphiti/FalkorDB extracts named entities with types `product_area | feature | concept | person`. The PostgreSQL `knowledge.entities` table stores them. This is the beginning of a domain-facet layer — but it is feature-flagged off in production, not used as a retrieval filter, and not exposed for browsing or navigation.

**KB structure — file system as implicit hierarchy**

Each org has one or more KBs (`kb_slug`). Within a KB, pages are organised by the Gitea file structure. This provides a hierarchy, but not a semantic one — it reflects the accidental folder structure of the content, not a deliberately designed taxonomy.

**Gap detection — topic-blind**

Gap events fire when retrieval fails (hard gap: no chunks; soft gap: scores < 0.4). Gaps are stored per query but not classified by topic or taxonomy node. The editorial inbox UI does not yet exist (tracked in §0 of the architecture as an open item).

**KBScopeBar — scope at KB level, not topic level**

Users can restrict retrieval to specific `kb_slugs`. This is a scope filter at the entire-KB level — not at a semantic topic or taxonomy node within a KB.

---

### 10.2 What is missing

| What the research recommends | Current state | Gap |
|---|---|---|
| **Hierarchy** (max 3 levels) for navigation/browsing | File-system hierarchy in Gitea only | No semantic topic hierarchy |
| **Facets** for multi-dimensional filtering | Internal: content_type + assertion_mode + entities (off) | Not user-facing; not combinable at retrieval time |
| **Tags** as first-class retrieval signal | MCP tool accepts tags; not indexed in Qdrant payload | Tags do not influence retrieval |
| **Taxonomy-aware gap detection** | Gaps are per query, not per topic cluster | Editors cannot see where to write |
| **Browse interface** | Chat-only; klai-docs shows per-KB page list | No navigate-by-topic interface |
| **Cross-KB coherence** | KBs are fully isolated silos per slug | No shared taxonomic frame across KBs |
| **Governance workflow** | No taxonomy review UI built | Taxonomy proposal queue does not exist |

---

### 10.3 What adding taxonomy would produce

Each gap filled produces a concrete change in system behaviour. These are not improvements in degree — they are qualitative shifts in what the system can do.

#### Gap 1 + 2: Semantic hierarchy + user-facing facets

**Before:** retrieval = hybrid vector search over all org chunks. Filter = `org_id` + optional `kb_slug`.

**After:** query is first classified to a taxonomy node. Qdrant search runs with a hard filter on that node, reducing the search space from e.g. 12,000 chunks to 800 relevant ones.

```
User asks: "How do I configure SSO?"
  → query classification → node: "Setup > SSO"
  → Qdrant search: org_id=X AND taxonomy_node="setup.sso"
  → searches 800 chunks instead of 12,000
  → higher precision, less noise
```

The KBScopeBar (currently: filter on kb_slug) becomes a topic scope: "answer only from Billing topics" rather than "answer only from KB slug X."

This is the move from metadata-filtered RAG (~85–90% precision) to taxonomy-constrained retrieval — approaching the GraphRAG precision range.

#### Gap 3: Tags as first-class concept

Tags in the Qdrant payload become a retrieval filter. They cover the long-tail: terms like `iDEAL`, `rate-limit`, `GDPR-export` that do not belong in the hierarchy but should be findable. The hierarchy handles 80% of cases; tags handle the rest without requiring taxonomy changes.

#### Gap 4: Taxonomy-aware gap detection

This is the gap with the highest immediate editorial value.

**Before — editorial inbox today:**

| Query | Type | KB |
|---|---|---|
| "how do I cancel my subscription" | hard | — |
| "VAT on invoice" | soft | billing-kb |
| "SSO setup Okta" | hard | — |
| ... 147 more | | |

An editor sees 150 failed search queries. No indication of where to start.

**After — editorial inbox with taxonomy:**

| Topic node | Open gaps | Frequency | Priority |
|---|---|---|---|
| Billing > Subscriptions | 47 | 3.2/day | 🔴 High |
| Setup > SSO | 31 | 1.8/day | 🔴 High |
| AI Features > Chat | 18 | 0.9/day | 🟡 Medium |
| Troubleshooting | 12 | 0.4/day | 🟢 Low |

Clicking "Billing > Subscriptions" shows the 47 specific queries, which sub-topics are most requested, which articles already exist in this node, and a suggested title for the missing article.

Gap detection becomes a **prioritised editorial work programme** — not "here are your problems" but "here are your writing assignments for this week, sorted by impact."

#### Gap 5: Browse interface

**Before:** Klai is search-first only. You can only find knowledge if you know what to search for.

**After:** A new employee opens the knowledge base and sees:

```
📁 Billing (43 articles)
   📁 Invoices (12)
   📁 Subscriptions (8)
   📁 Payment methods (5)

📁 Setup & Configuration (29 articles)
   📁 SSO (7)
   📁 Connectors (11)
```

The knowledge base becomes self-navigable for people who do not yet know what they do not know. This is the onboarding use case — new employees exploring the knowledge base without a specific question.

#### Gap 6: Cross-KB coherence

**Before:** KB A and KB B are fully isolated. A gap in KB A is never related to existing content in KB B, even if they cover the same topic.

**After:** A shared taxonomy over KBs enables:
- Linking a gap in the external KB to an existing internal article on the same node
- Retrieving content from both KBs for a question, weighted by `synthesis_depth` + `visibility`
- Showing editors: "This article already exists internally — do you want to publish or adapt it?"

#### Gap 7: Governance workflow

A taxonomy without governance degrades within months. The minimum viable process (PR-style workflow as described in §6.5 of the architecture):

```
System detects: "47 chunks unclassified after ingestion"
  → Generates suggestion: "New node: 'Billing > VAT questions'"
  → Sent to taxonomy owner's review queue
  → Reviewer approves (or renames)
  → Node is active; classifier retroactively tags existing content
```

Research estimate: 2–4 hours per quarter for an experienced reviewer covers a corpus of thousands of documents using active learning to surface only uncertain edge cases.

---

### 10.4 The honest trade-off

Taxonomy adds structure, and structure costs maintenance. It only works well if someone holds the taxonomy-owner role explicitly. That does not need to be much time (2–4 hours/quarter per large tenant), but it must be intentional. Without that role, a taxonomy degrades faster than it was built.

**What this means for Klai as a product:**

Without taxonomy, Klai Knowledge is a sophisticated search engine with knowledge storage. You put content in; you pull content out via vector search.

With the taxonomy layer, it becomes a **knowledge management platform with editorial intelligence**:

| Dimension | Without taxonomy | With taxonomy |
|---|---|---|
| Search | "Find something" | "Find something within this domain" |
| Gaps | "Here are 150 failed queries" | "Here are your writing assignments" |
| Browse | Not possible | Hierarchy navigable |
| Coverage | Invisible | Visible per topic node |
| Editors | Reactive | Proactively guided agenda |
| KB silos | Fully isolated | Shared taxonomic frame |

**The priority recommendation:** taxonomy-aware gap detection has the highest immediate value for editors and requires the least infrastructure to build first. It requires: (1) a taxonomy definition, (2) classification of existing gaps to taxonomy nodes, (3) aggregation in the editorial inbox per node. A browse interface and faceted retrieval can follow once the taxonomy is stable.

---

## 11. Bootstrapping a taxonomy: main categories with stub subcategories

### 11.1 Why a pre-defined taxonomy beats auto-discovery for V1

Auto-discovery (BERTopic, FASTopic) requires a minimum viable corpus of ~1,000 documents to produce stable clusters. Most tenants onboard with far fewer articles. A pre-defined taxonomy with stub subcategories solves the cold-start problem and unlocks the three highest-value features (gap classification, retrieval filtering, coverage dashboard) immediately — without a discovery pipeline.

Once a pre-defined taxonomy exists, every gap event can be matched against taxonomy nodes by embedding similarity. That is sufficient to produce an aggregated editorial inbox and a coverage view. No BERTopic needed until the corpus is large enough to validate and refine the taxonomy empirically.

### 11.2 The minimum viable taxonomy structure

**Main categories: 5–10**

Broad enough that every user query maps to at least one. Narrow enough that gaps can be meaningfully prioritised. For a B2B SaaS knowledge base, a realistic starting set:

```
1. Getting started & onboarding
2. Product features
3. Billing & subscriptions
4. Setup & configuration
5. Integrations & connectors
6. Security & compliance
7. Troubleshooting
8. API & developer docs
9. Release notes & changelog
10. Policies & legal
```

**Stub subcategories: 3–8 per main category**

Placeholders — name + ID only, not fully elaborated. Examples under "Setup & Configuration":

```
Setup & Configuration
├── SSO & authentication
├── Permissions & roles
├── Notifications
├── Data import & export
└── [stub: other]
```

The `[stub: other]` catchall is important: it captures gaps that belong in this main category but don't yet have a named subcategory. When the stub accumulates enough gaps, that signals a node should be split off.

**Node description: 1–2 sentences per node**

This is non-optional. A classifier matching queries against node names alone will miss edge cases. Each node needs a brief description of what kinds of questions and content belong there. Example:

> **SSO & authentication** — questions and articles about single sign-on setup, SAML/OIDC configuration, login problems, session management, and multi-factor authentication.

Without this, "I can't log in" may be classified to Troubleshooting instead of SSO & authentication — both are plausible, but only one is editorially actionable.

### 11.3 What this enables immediately

With main categories + stubs + descriptions in place:

**Gap classification** — every incoming gap query is embedded and matched against node descriptions. The gap registry gains a `taxonomy_node_id` field. The editorial inbox can immediately aggregate: "47 gaps in Billing & subscriptions, 31 in Setup & configuration."

**Retrieval filtering** — chunks at ingest time get classified to a taxonomy node. The Qdrant payload gains `taxonomy_nodes: ["setup", "setup.sso"]`. The retrieval-api can filter on this before vector search runs.

**Coverage dashboard** — `COUNT(chunks) GROUP BY taxonomy_node` shows immediately which nodes are thin (<3 articles) vs. well-covered.

### 11.4 What to leave out of V1

Do not build in V1:
- Synonym lists (add these after seeing real classification failures)
- More than 3 levels of depth (stubs are level 2; level 3 emerges from gap data)
- Automatic taxonomy evolution (nodes are only added/renamed via the governance queue)
- Tenant-customised taxonomy structures (a shared base taxonomy is sufficient for V1; tenant overrides are V2)

### 11.5 How the taxonomy grows

The taxonomy is not designed once and frozen. The gap data drives it forward:

1. A taxonomy node accumulates gaps that are semantically diverse → signal to split it into two nodes
2. Two taxonomy nodes consistently share the same gaps → signal to merge them
3. The `[stub: other]` catchall in a main category grows past a threshold (e.g. 20 gaps) → signal to name and formalise a new subcategory

All three signals go to the governance queue (§6.5 of the architecture). A reviewer approves or rejects. The taxonomy evolves from real usage, not from upfront speculation.

### 11.6 Effort estimate

| Task | Effort |
|---|---|
| Define 5–10 main categories | 1–2 hours |
| Write 3–8 stub subcategories per main category | 2–4 hours |
| Write node descriptions (1–2 sentences each) | 2–3 hours |
| **Total: initial taxonomy definition** | **~1 day** |
| Classify existing gaps to taxonomy nodes (retroactive) | Automated once descriptions exist |
| First quarterly review | 2–4 hours |

The description-writing step is the one that cannot be skipped. It is also the step that requires domain knowledge — it cannot be delegated to a classifier or generated by LLM without human review, because these descriptions define what the system considers in-scope for each node.

---

## Sources

- [Taxonomy 101: Definition, Best Practices — Nielsen Norman Group](https://www.nngroup.com/articles/taxonomy-101/)
- [Knowledge Base Taxonomy: 10 Proven Design Principles — Matrixflows](https://www.matrixflows.com/blog/10-best-practices-for-creating-taxonomy-for-your-company-knowledge-base)
- [Taxonomy Design Best Practices — Enterprise Knowledge](https://enterprise-knowledge.com/taxonomy-design-best-practices/)
- [Taxonomy Implementation Best Practices — Enterprise Knowledge](https://enterprise-knowledge.com/taxonomy-implementation-best-practices/)
- [The Case for Enterprise Taxonomy — Strategic Content](https://strategiccontent.com/resources/enterprise-taxonomy)
- [Faceted vs Hierarchical Taxonomies — LinkedIn](https://www.linkedin.com/advice/0/what-differences-between-hierarchical-56r2f)
- [When a Taxonomy Should Not Be Hierarchical — Hedden Information Management](https://www.hedden-information.com/when-a-taxonomy-should-not-be-hierarchical/)
- [Polyhierarchy in Taxonomies — Hedden Information Management](https://www.hedden-information.com/polyhierarchy-in-taxonomies/)
- [Why a Knowledge Graph is the Best Way to Upgrade Your Taxonomy — Enterprise Knowledge](https://enterprise-knowledge.com/why-a-knowledge-graph-is-the-best-way-to-upgrade-your-taxonomy/)
- [From Data Chaos to Clarity: GenAI Needs Taxonomy & Ontology — Squirro](https://squirro.com/squirro-blog/genai-taxonomy-ontology)
- [A Survey on Knowledge-Oriented RAG — arXiv](https://arxiv.org/html/2503.10677v1)
- [Knowledge Graph-Based RAG — Nature Scientific Reports](https://www.nature.com/articles/s41598-025-21222-z)
- [Enhancing Taxonomy Management Through Knowledge Intelligence — Enterprise Knowledge](https://enterprise-knowledge.com/enhancing-taxonomy-management-through-knowledge-intelligence/)
- [The Role of Taxonomies in Effective Knowledge Management — Taxodiary](https://taxodiary.com/2025/09/the-role-of-taxonomies-in-effective-knowledge-management/)
- [Knowledge Management Taxonomy — KM Insider](https://kminsider.com/topic/knowledge-management-taxonomy/)
- [Hidden Costs of Inefficient Knowledge Management — Assima](https://assimasolutions.com/resources/blog/hidden-costs-of-inefficient-knowledge-management/)
