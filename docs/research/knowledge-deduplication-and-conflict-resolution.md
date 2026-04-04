# Knowledge Deduplication, Conflict Resolution & Canonical Truth

> Research document exploring how to prevent knowledge redundancy, detect conflicts,
> and maintain a single source of truth in Klai's knowledge layer.

## Problem Statement

Klai's knowledge layer accepts information from multiple sources: meeting transcripts,
document syncs (Gitea), chat interactions, file uploads, and web crawls. This creates
a fundamental **knowledge recycling problem**:

1. A user queries the KB via chat and gets an AI-generated answer
2. The user writes a document based on that answer (adding their own insights)
3. The document syncs via Gitea back into the KB
4. The KB now contains both the original knowledge AND a reformulated version of it

This is not a simple duplicate — the document is a **mix** of existing KB knowledge
and new human-authored content. Content hash deduplication cannot distinguish between
"already known" and "genuinely new" parts of the document.

### The Deeper Issue

The problem extends beyond documents:

- **Chat-to-KB**: A user learns something in a chat session, then manually adds it
  as a knowledge item — but it was already in the KB (that's where the chat got it)
- **Meeting summaries**: Structured decisions from meetings overlap with existing
  KB articles about the same topic
- **Knowledge updates**: A user says "this is outdated" in chat, triggering an update —
  but the system can't distinguish "new fact replacing old fact" from "user restating
  existing knowledge differently"
- **Cross-KB pollution**: The same fact exists in an org KB and a personal KB,
  both indexed and retrievable, creating noise in search results

The root cause: **there is no identity for a fact independent of the document it lives in.**
The same decision can appear in a meeting summary, a doc page, and a chat-created item,
and the system treats them as three separate pieces of knowledge.

## How Existing Systems Solve This

### Systems That Don't (Most of Them)

Confluence, Notion, SharePoint, and most wiki platforms **do not solve this problem**.
They allow redundancy and rely on humans to maintain consistency. This works at small
scale but breaks down as organizations grow. The result is "knowledge rot" — outdated
and contradictory information that erodes trust in the knowledge base.

### KCS (Knowledge-Centered Service) — The Methodology

The Consortium for Service Innovation developed KCS, the most mature methodology for
knowledge lifecycle management. Key concepts:

**Article States**: Every knowledge item moves through explicit states:
```
Work In Progress → Not Validated → Validated → Archived
```

Items can move backwards (Validated → Not Validated) when someone flags them as
potentially outdated. This is crucial: **updates don't silently overwrite; they
trigger re-validation.**

**Double-Loop Process**:
- **Solve Loop**: Individual interactions create/update knowledge (reactive)
- **Evolve Loop**: Patterns across interactions improve the KB structure (proactive)

The solve loop is where deduplication happens: before creating new content, the agent
searches for existing articles. If one exists, they improve it rather than creating
a duplicate. This is a human process, but it maps directly to what an AI system
should do at ingest time.

**Relevance**: KCS proves that the staging/validation pattern works at enterprise scale.
Organizations like Cisco, Oracle, and HP use it to manage millions of knowledge articles.

### Guru — The Most Complete Product Implementation

Guru's knowledge management platform implements several mechanisms directly relevant
to our problem:

**Verification Workflow**:
- Every card has an assigned verifier (person or group)
- Scheduled review cycles (configurable: weekly to annually)
- Cards auto-expire to "unverified" if not reviewed on time
- AI-generated answers are restricted to verified content only

**Duplicate Detection**:
- Weekly automated scan (every Monday)
- 90% cosine similarity threshold
- Groups duplicates and presents them to content owners
- Permission-aware: users only see duplicates they have access to
- Maximum 20 duplicate groups shown at a time (prevents overwhelm)

**Trust Score**: A measurable metric for KB health based on verification rates,
staleness, and duplicate ratios.

**Relevance**: Guru's model of "verified content only" for AI answers directly addresses
the recycling problem. If chat responses are derived from verified KB items, and the
derivation is tracked, re-ingestion can be blocked.

### Glean — Knowledge Graph Over Sources

Glean takes a different approach: it doesn't copy content into a central KB. Instead,
it builds a **knowledge graph on top of existing sources** (documents, tickets, messages,
wikis). Each answer is traceable to specific document chunks with line-level provenance.

**Relevance**: This is the "KB as index, not as store" model. It avoids the duplication
problem by never creating a second copy. However, it requires all sources to be
accessible at query time, which doesn't work for offline or ephemeral sources
(like meeting transcripts that aren't stored elsewhere).

### Starmind — Question-Level Deduplication

Starmind detects at the question level: when someone asks a question, the system
checks if the same or similar question has already been answered. If so, it surfaces
the existing answer rather than generating a new one.

**Relevance**: This is a lightweight form of deduplication that works at the retrieval
layer rather than the ingest layer. It doesn't prevent storage duplication but does
prevent the user from seeing duplicates.

### Academic Approaches

**SemHash / SemDeDup**: Embedding-based semantic deduplication. SemHash combines
lightweight static embeddings with approximate nearest neighbor search for cross-dataset
deduplication — comparing new content against an existing corpus. SemDeDup (from Meta)
can remove 50% of redundant content with minimal information loss.

**CRDL (Detect-Then-Resolve)**: Uses LLMs to first detect conflicts between existing
knowledge and new information, then identify "truths" using filtering strategies
tailored to the type of relationship or attribute.

**HALO (Half-Life Based Outdated Fact Filtering)**: Assigns facts a "half-life" based
on their type. "CEO of company X" has a shorter half-life than "headquarters location."
Facts exceeding their half-life are flagged as potentially outdated.

**LiveVectorLake**: Architecture for versioned knowledge bases in RAG systems. Instead
of replacing old vectors, it keeps them alongside new vectors with timestamps, using
delta encoding. You can reconstruct KB state at any point in time.

**SHACL (Shapes Constraint Language)**: Schema-based validation for knowledge graphs.
Defines constraints (e.g., "a person can only have one birth date") and automatically
detects violations.

## Proposed Architecture for Klai

Based on this research, the solution combines three layers:

### Layer 1: Canonical vs. Derived Classification

Every piece of content entering the system is classified:

| Classification | Description | Examples | Re-ingestable? |
|---|---|---|---|
| **Canonical** | Original human-authored knowledge or direct observations | Meeting decisions, manual KB edits, uploaded docs | Yes (as update) |
| **Derived** | Content generated from or based on canonical items | Chat responses, AI summaries, docs written from KB queries | No |
| **Mixed** | Human content that incorporates canonical knowledge | Doc written during a chat session, edited meeting summary | Partial (new parts only) |

**Implementation**: Track `derivation_chain` metadata on every artifact — a list of
canonical KB item IDs that contributed to the content. Content with a derivation chain
is never re-ingested as canonical.

For Gitea-synced documents: if the document was created while the user had an active
chat session that retrieved KB items X, Y, Z, the document's `derivation_chain` includes
those items. The ingest pipeline knows to treat it as derived.

### Layer 2: Semantic Similarity + Contradiction Detection at Ingest

When new content arrives (regardless of source), before it enters the canonical KB:

```
New content
    │
    ▼
[Embed + Compare] ── similarity against existing canonical items
    │
    ├── >= 0.95 similarity: DUPLICATE
    │   → Block ingestion
    │   → Log as "attempted duplicate" for analytics
    │
    ├── 0.80-0.95 similarity: OVERLAP
    │   → Route to staging area
    │   → Flag: "Similar to existing item [X]. New information or duplicate?"
    │
    ├── 0.60-0.80 similarity: RELATED
    │   → Run contradiction check (LLM)
    │   → If contradiction: create conflict item
    │   → If compatible: proceed to staging
    │
    └── < 0.60 similarity: NEW
        → Proceed to staging (low-risk auto-approve eligible)
```

**Contradiction detection prompt pattern**:
```
Given existing knowledge item:
"{existing_fact}"

And proposed new knowledge:
"{new_fact}"

Do these contradict each other? Classify as:
- COMPATIBLE: Both can be true simultaneously
- SUPERSEDES: New fact replaces old fact (e.g., updated information)
- CONTRADICTS: Facts cannot both be true, requires human resolution
- REFINES: New fact adds detail to old fact without contradicting it
```

### Layer 3: Staging Area with Human-in-the-Loop

The staging area is a review queue where proposed knowledge changes wait for validation.
Not everything requires manual review — the system triages based on impact:

**Auto-approve (with periodic sampling)**:
- New facts with < 0.60 similarity to anything existing
- Low synthesis depth (raw observations)
- From trusted sources (verified connectors)

**Assisted review** (human sees the diff, one-click approve/reject):
- Overlap detected (0.80-0.95 similarity)
- Updates to existing items (same path, different content)
- Mixed canonical/derived content

**Mandatory review**:
- Contradictions detected
- High synthesis depth (synthesized/revised content)
- Deletions or archival of validated items

**Staging area fields**:
```
id                    UUID
proposed_artifact_id  UUID (the artifact waiting to be promoted)
status                "pending" | "approved" | "rejected" | "conflict"
review_type           "auto" | "assisted" | "mandatory"
similarity_matches    JSONB [{artifact_id, score, relationship}]
contradiction_check   JSONB {result, explanation, compared_to}
submitted_at          timestamp
reviewed_at           timestamp (NULL until reviewed)
reviewed_by           user_id (NULL for auto-approved)
resolution_note       text (optional reviewer comment)
```

### How This Solves Each Scenario

**Scenario: User writes a doc based on KB knowledge, synced via Gitea**
1. Gitea sync triggers ingest
2. Document has `source_type: "docs"` — not automatically derived
3. Semantic comparison finds 0.85 similarity with existing KB items
4. Routed to assisted review: "This document overlaps with [existing items]. Is this a summary/view of existing knowledge, or does it contain new information?"
5. Human reviewer either:
   - Marks as derived (linked but not canonical)
   - Extracts the new parts and approves only those as canonical updates

**Scenario: User says "this is outdated" in chat, wants to update KB**
1. User's correction is captured as a proposed update
2. System identifies the existing canonical item being corrected
3. Contradiction detection classifies as SUPERSEDES
4. Routed to staging: "Proposed update to [item]. Old: [X]. New: [Y]."
5. On approval: old item gets `valid_until = now`, new item becomes active
6. Full audit trail preserved (bi-temporal versioning already exists)

**Scenario: Meeting decisions overlap with existing KB articles**
1. Meeting summary is ingested with structured decisions
2. Each decision is compared against existing KB items
3. Matches found: "Decision 'migrate to Hetzner' matches existing item 'Infrastructure: Hetzner migration plan'"
4. System suggests: link (corroboration) or update (if decision adds new detail)
5. No duplicate created — the meeting becomes evidence for the existing item

**Scenario: Chat-created knowledge item that already exists**
1. User says "add this to the KB: we use LiteLLM for model routing"
2. Semantic comparison finds 0.96 similarity with existing item
3. Blocked: "This knowledge already exists in [item link]. Would you like to update it instead?"

## Integration with Existing Klai Architecture

This proposal builds on existing infrastructure:

| Existing Component | How It's Used |
|---|---|
| `content_hash` dedup | Keep as first-pass filter (exact duplicates) |
| `provenance_type` field | Map to canonical/derived classification |
| `synthesis_depth` field | Use as triage signal (higher depth = more review) |
| `assertion_mode` field | Use for contradiction detection (factual vs. belief) |
| Bi-temporal versioning | Already supports superseding; add staging states |
| Qdrant embeddings | Already computed; use for similarity comparison |
| Graphiti enrichment | Entity extraction identifies what facts are about |

**New components needed**:
- `knowledge.staging` table (review queue)
- `derivation_chain` field on artifacts (provenance tracking)
- Contradiction detection service (LLM-based, async)
- Review UI in the portal (staging queue viewer)
- Similarity comparison step in the ingest pipeline

## KCS Article States Mapped to Klai

Adopt KCS-style states for canonical items:

| State | Meaning | Who Can Create | Queryable by Chat? |
|---|---|---|---|
| `draft` | Being worked on | Any user | No |
| `proposed` | In staging, awaiting review | System (ingest pipeline) | No |
| `validated` | Reviewed and confirmed | Reviewer/auto-approve | Yes |
| `disputed` | Contradiction detected, under review | System | Yes (with caveat) |
| `archived` | No longer current, superseded | Reviewer/system | No (but preserved) |

Chat and RAG retrieval should **only use `validated` items** by default, with an option
to include `disputed` items with a warning. This prevents the recycling problem: derived
content from chat is always based on validated items, and if it's re-ingested, the
similarity check catches it.

## Similarity Threshold Tuning

Starting point based on Guru's production experience:

| Threshold | Classification | Action |
|---|---|---|
| >= 0.95 | Near-duplicate | Block, notify user |
| 0.85-0.95 | Significant overlap | Assisted review |
| 0.70-0.85 | Related content | Contradiction check |
| < 0.70 | New content | Auto-approve eligible |

These thresholds should be tunable per KB and per content type. Meeting transcripts
will naturally have lower similarity to KB articles about the same topic (different
language register), so the overlap threshold might need to be lower for that source type.

## Open Questions

1. **Granularity**: Should similarity comparison happen at the document level or at the
   claim/fact level? Document-level is simpler but misses partial overlaps. Claim-level
   requires fact extraction (additional LLM call) but is more precise.

2. **Mixed content handling**: When a document is 70% existing knowledge and 30% new
   insight, how does the system extract and promote only the new parts? This may require
   a "diff against KB" step that's more sophisticated than simple similarity.

3. **Review queue UX**: How to prevent the staging area from becoming a backlog that
   nobody checks? Guru solves this with expiry (auto-archive after X days unreviewed)
   and trust scores. KCS solves it by making review part of the daily workflow, not a
   separate task.

4. **Cross-KB deduplication**: Should the system detect duplicates across org KB,
   personal KBs, and group KBs? Or is some redundancy acceptable across KB boundaries?

5. **Retroactive deduplication**: The existing KB likely already contains duplicates.
   A one-time dedup scan (like Guru's weekly scan) would be needed to clean up.

## References

- [KCS v6 Practices Guide](https://library.serviceinnovation.org/KCS/KCS_v6/KCS_v6_Practices_Guide/) — Consortium for Service Innovation
- [Guru Verification](https://www.getguru.com/features/verification) — Card verification workflows
- [Guru Duplicate Detection](https://www.getguru.com/features/duplicate-detection) — Semantic duplicate detection
- [Glean Platform](https://www.glean.com/) — Knowledge graph over existing sources
- [SemHash](https://github.com/MinishLab/semhash) — Semantic deduplication library
- [SemDeDup](https://openreview.net/forum?id=IRSesTQUtb) — Semantic deduplication at web scale
- [CRDL](https://www.mdpi.com/2227-7390/12/15/2318) — LLM-based knowledge conflict detection
- [HALO](https://arxiv.org/html/2505.07509) — Half-life based fact expiry
- [LiveVectorLake](https://arxiv.org/html/2601.05270) — Versioned vector knowledge bases
- [SHACL](https://www.w3.org/TR/shacl/) — Shapes Constraint Language for validation
