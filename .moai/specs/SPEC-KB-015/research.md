# Research: SPEC-KB-015 — Self-Learning Knowledge Base via Feedback Signals

> Produced during Plan phase. DO NOT implement from this document — implementation follows SPEC approval.
> Date: 2026-04-04

---

## 1. State of the Art: How RAG Systems Self-Learn

### 1.1 Qdrant Native Relevance Feedback (Production-Tested)

Qdrant v1.17+ supports a native relevance feedback query using context pairs (one marked more relevant, one less). The scoring formula:

```
F = a · score + Σ confidence^b · c · delta
```

Where `delta` is the distance to the positive example minus the distance to the negative example. Parameters (a, b, c) are calibrated on ~500 domain queries. **Measured improvement: ~13% relative gain in retrieval quality** with no model retraining. This is the most immediately deployable production pattern.

Reference: https://qdrant.tech/articles/relevance-feedback/

### 1.2 Real-Time Payload Updates (Chosen for Phase 1)

The cheapest and most immediate approach: update a `quality_score` field in Qdrant payload after each feedback event. The retrieval pipeline then blends this score into ranking.

- **Latency**: <100ms per update (Qdrant `set_payload` is fast)
- **Effect**: Immediate — next query to the same chunk picks up the new score
- **Formula**: Running weighted average over binary [0.0, 1.0] signal (thumbsUp = 1.0, thumbsDown = 0.0)
- **Guard**: Only apply to ranking when `feedback_count >= 3` (prevents noise from single data points)

### 1.3 Batch Cross-Encoder Fine-Tuning (Phase 2)

Once 1000+ labeled `(query, chunk, rating)` triples are accumulated, the cross-encoder reranker (`bge-reranker-v2-m3`) can be fine-tuned on domain data. This is the most impactful long-term approach but requires substantial data.

- **Minimum for meaningful gain**: 500–1000 labeled pairs
- **Hard-negative mining is critical**: include plausible but wrong chunks as negatives
- **Schedule**: Weekly batch, not real-time
- Reference: FlagEmbedding fine-tuning suite (https://github.com/FlagOpen/FlagEmbedding)

### 1.4 Self-RAG and LLM-as-Judge (Phase 3)

Self-RAG (ICLR 2024, Oral) trains a model to emit reflection tokens indicating retrieval quality, bypassing the need for human labels. DynamicRAG (2025) uses RL with LLM-judged output quality as the reward signal.

These approaches require significant infrastructure and are out of scope for Phase 1, but the golden dataset collected in Phase 1 feeds into them.

### 1.5 RAGAS for Offline Evaluation (Parallel Track)

RAGAS provides reference-free RAG evaluation metrics (Faithfulness, Answer Relevancy, Context Precision) without ground truth labels. Thumbs-up examples become the golden eval set. **100–200 examples** are sufficient to start detecting retrieval quality patterns.

### 1.6 Critical Pitfalls to Avoid

- **Embedding drift**: Feedback collected against model version N becomes misleading when the embedding model updates. Solution: version all feedback with the embedding model version.
- **Popularity bias**: High-traffic chunks accumulate feedback faster, creating a feedback loop. Solution: apply diversity metrics (MMR) downstream, monitor coverage.
- **Feedback gaming**: Coordinated fake feedback (BRRA attack). Solution: RLS tenant isolation, monitor outlier patterns per org.
- **Cold start**: No feedback yet → quality scores uninitialized. Solution: quality score only influences ranking when `feedback_count >= 3`.
- **Sparse signal**: Most messages never receive feedback. Solution: accept this — even 1–2% feedback rate yields actionable signal over time.

---

## 2. LibreChat Internals

### 2.1 Feedback Storage

LibreChat stores feedback in MongoDB on the Message document:

```typescript
feedback: {
  rating: 'thumbsUp' | 'thumbsDown',  // Required
  tag: TFeedbackTagKey,                // Optional (11 options: 'accurate_reliable', 'not_helpful', etc.)
  text: string                         // Optional, max 1024 chars
}
```

Route: `PUT /:conversationId/:messageId/feedback` — writes directly to MongoDB.

### 2.2 No External Feedback Hook

**Critical finding**: LibreChat does NOT support a `FEEDBACK_URL` or webhook system. Feedback is always stored only in MongoDB. There is no built-in mechanism to forward feedback to an external service.

**Implication**: We must patch LibreChat's feedback endpoint to fire-and-forget a POST to portal-api. This is a minimal change (~15 lines in `api/server/routes/messages.js`).

### 2.3 No Conversation ID in LiteLLM Requests

**Critical finding**: LibreChat does NOT send `X-Conversation-ID`, `X-Message-ID`, or any custom headers to the LLM backend. `BaseClient` sends only standard OpenAI-format requests.

**Implication**: The LiteLLM hook cannot key retrieval logs by conversation/message ID. Time-windowed correlation is required.

### 2.4 Message Document Key Fields

| Field | Type | Notes |
|---|---|---|
| `messageId` | String | Unique, indexed |
| `conversationId` | String | Links to conversation |
| `user` | String | LibreChat user ObjectId |
| `tenantId` | String | Multi-tenant isolation |
| `createdAt` | Timestamp | Auto-generated, used for time-window correlation |
| `metadata` | Mixed | Flexible JSON — could store chunk IDs if we inject them |
| `feedback` | Object | Only set after user clicks thumbs |

### 2.5 Correlation Strategy: Time-Windowed Match

Since LibreChat doesn't send IDs to LiteLLM, the correlation bridge works as follows:

1. LiteLLM hook fires a `retrieval_log` event with `{org_id, user_id, chunk_ids[], query_resolved, retrieved_at=now()}`
2. LibreChat receives the response and stores the message with `createdAt ≈ retrieved_at + 500ms–3s`
3. LibreChat patch sends feedback to portal-api including `message_created_at` (from the stored message)
4. Portal-api finds the retrieval_log entry WHERE `retrieved_at BETWEEN message_created_at - 60s AND message_created_at + 10s` ORDER BY ABS distance, LIMIT 1

This is a robust match because: LiteLLM retrieval is always immediately followed by model generation and then LibreChat message storage. The window is generous (60s before to handle slow models) and narrow on the right side (10s after, since message is stored within seconds of response).

---

## 3. Klai Codebase Analysis

### 3.1 Retrieval API Response

The `/retrieve` endpoint returns `RetrieveResponse` containing `ChunkResult` objects. Each `ChunkResult` includes:

```python
chunk_id: str      # Qdrant point UUID — the key for feedback correlation
text: str
score: float
reranker_score: float | None
scope: str | None  # "org", "personal", "notebook"
artifact_id: str | None
```

**`chunk_id` maps directly to Qdrant's point UUID**, generated at ingest time. This is the ID we store in the retrieval log and update with feedback.

### 3.2 LiteLLM Hook: Current State

**File**: `deploy/litellm/klai_knowledge.py`

The hook currently:
- Retrieves chunks from retrieval-api
- Injects them into the system message
- Stores `_klai_kb_meta` in request metadata: `{org_id, user_id, chunks_injected, retrieval_ms, gate_bypassed}`

**Missing**: chunk_ids are NOT stored in metadata or logged. The hook knows which chunks were used but discards this after injecting text.

**Fix required**: Extract `chunk_id` from each `ChunkResult` before text injection, then fire-and-forget the retrieval log event.

### 3.3 Gap Events: Reference Implementation Pattern

**File**: `klai-portal/backend/app/models/retrieval_gaps.py`  
**Table**: `portal_retrieval_gaps`

This is the exact pattern to replicate for the retrieval log and feedback events:

```sql
portal_retrieval_gaps (
  id            SERIAL PRIMARY KEY,
  org_id        INTEGER FK → portal_orgs,
  user_id       TEXT,              -- LibreChat ObjectId
  query_text    TEXT,
  gap_type      TEXT,
  top_score     DOUBLE PRECISION,
  chunks_retrieved INTEGER,
  retrieval_ms  INTEGER,
  occurred_at   TIMESTAMP DEFAULT NOW()
)
-- RLS protected via set_tenant()
```

The fire-and-forget pattern in the LiteLLM hook:
```python
def _fire_gap_event(...) -> None:
    async def _post():
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(url, json=payload, headers=auth_headers)
    asyncio.get_running_loop().create_task(_post())
```

### 3.4 Qdrant Payload Updates: Existing Patterns

**File**: `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py`

Payload updates are already used in production:
- `update_kb_visibility()` — updates visibility for all chunks in a KB via filter
- `update_link_counts()` — updates `incoming_link_count` and `links_to` per chunk by point ID
- `set_entity_graph_data()` — bulk payload updates with concurrency pooling

**The point-ID-based update pattern** (what we need for quality scores):
```python
await client.set_payload(
    collection_name=COLLECTION,
    payload={"quality_score": 0.75, "feedback_count": 3},
    points=[chunk_id],  # List of Qdrant point UUIDs
)
```

This is cheap, fast, and already battle-tested in the codebase.

### 3.5 Internal API Authentication

All internal service-to-service calls use:
```python
headers = {"Authorization": f"Bearer {settings.internal_secret}"}
```

The secret is set via `PORTAL_INTERNAL_SECRET` env var, already used by the gap events flow.

### 3.6 Multi-Tenant MongoDB Isolation

`portal_orgs.librechat_container` stores the MongoDB database name per tenant. When the LibreChat patch sends feedback to portal-api, it includes `librechat_tenant_id`, which portal-api resolves to `org_id` via this field.

---

## 4. Architecture Decision: What to Build in Phase 1

### In Scope (SPEC-KB-015)

1. **Retrieval Log**: LiteLLM hook fires chunk_ids to `portal_retrieval_log` table (TTL 7 days)
2. **LibreChat Bridge**: Minimal patch (~15 lines) forwards feedback to portal-api with `message_created_at`
3. **Feedback Endpoint**: `POST /internal/v1/kb-feedback` in portal-api — correlates, stores, triggers Qdrant update
4. **Qdrant Quality Score**: Real-time `set_payload` updates on feedback correlation
5. **Retrieval Ranking Integration**: Blend quality score into RRF/reranker output in retrieval-api
6. **Analytics**: Emit feedback events to `product_events` table (per existing SPEC-GRAFANA-METRICS pattern)

### Out of Scope (Phase 2+)

- **RAGAS evaluation pipeline**: Requires separate eval infrastructure
- **Cross-encoder fine-tuning**: Needs 1000+ examples first; schedule for Phase 2
- **Qdrant relevance feedback query**: Production-tested but adds latency; schedule for Phase 2
- **Feedback UI in portal**: Klai-native feedback outside LibreChat
- **Implicit signals**: Dwell time, re-ask patterns — Phase 3

### Key Design Constraints

- **Tenant isolation**: All feedback strictly scoped to org_id. No cross-tenant signal leakage.
- **Non-blocking**: All feedback processing is fire-and-forget. Zero impact on retrieval latency.
- **Graceful degradation**: If correlation fails (no matching retrieval log), store feedback without chunk_ids for analytics. Quality scores remain unchanged.
- **Versioned feedback**: Store `embedding_model_version` with every retrieval log entry. When BGE-M3 updates, old feedback is preserved but flagged as stale.
- **Minimum confidence**: Quality score only influences ranking when `feedback_count >= 3`.

---

## 5. Papers and References

| Source | Key Insight |
|---|---|
| Qdrant Relevance Feedback (v1.17+) | 13% improvement, production-tested, no retraining needed |
| Self-RAG (ICLR 2024) | Reflection tokens for adaptive retrieval |
| DynamicRAG (2025) | RL with LLM-judged reward signal |
| RaFe: Ranking Feedback (EMNLP 2024) | Reranker scores as training signal, no labels needed |
| RAGAS Framework | Reference-free RAG evaluation, golden dataset generation |
| FlagEmbedding Suite | Fine-tuning infrastructure for BGE-M3 and rerankers |
| BiasRAG attack paper (2025) | Adversarial feedback poisoning vectors |
| Zapier feedback UX study | UI copy change → 5x feedback volume |
