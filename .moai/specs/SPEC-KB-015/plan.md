# Implementation Plan — SPEC-KB-015

## Overview

Five services are touched. The change is additive in all five — no existing behaviour is altered.

| Service | Change | Risk |
|---|---|---|
| `deploy/litellm/klai_knowledge.py` | Extract chunk_ids from retrieval response; fire retrieval log event | Low — fire-and-forget, no latency impact |
| `klai-retrieval-api` | Add `quality_score` boost after RRF merge; initialise payload fields at ingest | Low — guarded by `feedback_count >= 3` |
| `klai-knowledge-ingest` | Set `quality_score=0.5, feedback_count=0` on new chunk payloads | Low — additive payload field |
| `klai-portal/backend` | New tables + endpoints: retrieval-log, kb-feedback, feedback-events | Medium — new DB migrations required |
| LibreChat | Minimal patch: forward feedback to portal-api after MongoDB write | Low — fire-and-forget, non-blocking |

---

## Task Decomposition

### Task 1: Database Migrations (portal-api)

Create two new RLS-protected tables following the `portal_retrieval_gaps` pattern exactly.

**Table: `portal_retrieval_log`**
```sql
id                   SERIAL PRIMARY KEY
org_id               INTEGER NOT NULL REFERENCES portal_orgs(id)
user_id              TEXT NOT NULL           -- LibreChat MongoDB ObjectId
chunk_ids            TEXT[] NOT NULL         -- Qdrant point UUIDs
reranker_scores      DOUBLE PRECISION[]      -- Parallel array to chunk_ids
query_resolved       TEXT NOT NULL
embedding_model_version TEXT NOT NULL DEFAULT 'bge-m3-v1'
retrieved_at         TIMESTAMP NOT NULL DEFAULT NOW()
-- Indexes:
ix_retrieval_log_org_user_time  (org_id, user_id, retrieved_at)
-- TTL: rows with retrieved_at < NOW() - 7 days deleted by periodic job
```

**Table: `portal_feedback_events`**
```sql
id                   SERIAL PRIMARY KEY
org_id               INTEGER NOT NULL REFERENCES portal_orgs(id)
user_id              TEXT NOT NULL
conversation_id      TEXT NOT NULL
message_id           TEXT NOT NULL
rating               TEXT NOT NULL CHECK (rating IN ('thumbsUp', 'thumbsDown'))
tag                  TEXT
feedback_text        TEXT
retrieval_log_id     INTEGER REFERENCES portal_retrieval_log(id) ON DELETE SET NULL
chunk_ids            TEXT[]           -- Denormalised copy (survives retrieval_log TTL)
correlated           BOOLEAN NOT NULL DEFAULT FALSE
occurred_at          TIMESTAMP NOT NULL DEFAULT NOW()
-- Unique constraint (idempotency):
UNIQUE (message_id, conversation_id)
-- Indexes:
ix_feedback_events_org_occurred  (org_id, occurred_at)
```

**Alembic migration** in `klai-portal/backend/app/alembic/versions/`.

---

### Task 2: Portal-API Endpoints

**File**: `klai-portal/backend/app/api/internal.py`

**Endpoint 1**: `POST /internal/v1/retrieval-log`

Request body (Pydantic model `RetrievalLogIn`):
```python
org_id: str                     # Zitadel org ID
user_id: str                    # LibreChat ObjectId
chunk_ids: list[str]
reranker_scores: list[float]
query_resolved: str
embedding_model_version: str
retrieved_at: datetime
```

Handler: Resolve zitadel_org_id → portal_org_id, set RLS tenant context, insert row, return 201.

**Endpoint 2**: `POST /internal/v1/kb-feedback`

Request body (Pydantic model `KbFeedbackIn`):
```python
conversation_id: str
message_id: str
message_created_at: datetime
rating: Literal["thumbsUp", "thumbsDown"]
tag: str | None = None
text: str | None = None
librechat_user_id: str
librechat_tenant_id: str
```

Handler logic:
1. Resolve `librechat_tenant_id` → `org_id` via `portal_orgs.librechat_container`. If not found → 404.
2. Check idempotency: if `(message_id, conversation_id)` already in `portal_feedback_events` → 200.
3. Time-window query on `portal_retrieval_log`:
   ```sql
   SELECT id, chunk_ids FROM portal_retrieval_log
   WHERE org_id = $org_id AND user_id = $librechat_user_id
     AND retrieved_at BETWEEN $message_created_at - interval '60 seconds'
                          AND $message_created_at + interval '10 seconds'
   ORDER BY ABS(EXTRACT(EPOCH FROM (retrieved_at - $message_created_at)))
   LIMIT 1
   ```
4. Insert `portal_feedback_events` row with `correlated = (retrieval_log_id IS NOT NULL)`.
5. If correlated: fire-and-forget task to update Qdrant quality scores.
6. Emit `product_event('knowledge.feedback', ...)` (fire-and-forget, existing pattern).
7. Return 201.

**Qdrant update task** (async, fire-and-forget):
```python
async def _apply_quality_score(chunk_ids: list[str], rating: str, org_id: str) -> None:
    signal = 1.0 if rating == "thumbsUp" else 0.0
    # Fetch current payload for each chunk (batch)
    # Compute new quality_score and feedback_count
    # set_payload() via qdrant-client with points filter
```

Note: Qdrant client in portal-api (not retrieval-api) — needs QDRANT_URL env var.

---

### Task 3: LiteLLM Hook Extension

**File**: `deploy/litellm/klai_knowledge.py`

After a successful retrieval call, before text injection, extract chunk_ids:

```python
# Existing: chunks = response.get("chunks", [])
chunk_ids = [c["chunk_id"] for c in chunks if c.get("chunk_id")]
reranker_scores = [c.get("reranker_score", 0.0) for c in chunks]

# Fire retrieval log (fire-and-forget, same pattern as gap events)
_fire_retrieval_log(org_id, user_id, chunk_ids, reranker_scores, query_resolved)
```

The `_fire_retrieval_log` function mirrors `_fire_gap_event` exactly — httpx POST with 2s timeout, asyncio.create_task().

---

### Task 4: Knowledge Ingest — Payload Initialisation

**File**: `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py`

In `upsert_chunks()`, add two fields to every chunk payload:
```python
"quality_score": 0.5,
"feedback_count": 0,
```

This is a one-line addition to the payload dict construction. Existing chunks are unaffected (they have no `quality_score` field — the retrieval-api handles this via REQ-KB-015-21).

---

### Task 5: Retrieval API — Quality Score Boost

**File**: `klai-retrieval-api/retrieval_api/services/search.py` or wherever RRF merge happens

After RRF merge, before returning top-K results, apply quality boost:

```python
for chunk in merged_results:
    feedback_count = chunk.payload.get("feedback_count", 0)
    quality_score = chunk.payload.get("quality_score", 0.5)
    if feedback_count >= 3:
        chunk.score *= (1 + 0.2 * (quality_score - 0.5))
# Re-sort by updated score
merged_results.sort(key=lambda c: c.score, reverse=True)
```

---

### Task 6: LibreChat Patch

**File**: `api/server/routes/messages.js` in the LibreChat container

After the `db.updateMessage({ messageId, feedback })` call, fire-and-forget to portal-api:

```javascript
const portalApiUrl = process.env.PORTAL_INTERNAL_URL;
const internalSecret = process.env.PORTAL_INTERNAL_SECRET;

if (portalApiUrl && internalSecret) {
  // Fire-and-forget — do not await
  fetch(`${portalApiUrl}/internal/v1/kb-feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${internalSecret}`,
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      message_id: messageId,
      message_created_at: updatedMessage.createdAt?.toISOString(),
      rating: feedback.rating,
      tag: feedback.tag ?? null,
      text: feedback.text ?? null,
      librechat_user_id: req.user.id,
      librechat_tenant_id: req.user.tenantId ?? null,
    }),
  }).catch(() => {}); // silently ignore errors
}
```

Two new env vars in `deploy/docker-compose.yml` for the librechat service:
- `PORTAL_INTERNAL_URL`: `http://portal-api:8000`
- `PORTAL_INTERNAL_SECRET`: `${PORTAL_INTERNAL_SECRET}` (already exists)

---

### Task 7: TTL Cleanup Job (portal-api)

Background task or Procrastinate scheduled job that runs daily:
```sql
DELETE FROM portal_retrieval_log WHERE retrieved_at < NOW() - INTERVAL '7 days';
```

Can be a simple `asyncio` scheduled task in portal-api startup, or a Procrastinate cron job following the existing pattern.

---

### Task 8: Analytics Dashboard

Grafana panel additions (or new dashboard panel in existing KB dashboard):
- Time series: `SELECT DATE(occurred_at), rating, COUNT(*) FROM portal_feedback_events GROUP BY 1, 2`
- Correlation rate: `SELECT correlated, COUNT(*) FROM portal_feedback_events GROUP BY 1`
- Top chunks: Qdrant cypher query for chunks with highest `feedback_count` (via future Qdrant integration) or snapshot query

---

## Dependencies

| Dependency | Version | Notes |
|---|---|---|
| qdrant-client | >=1.7 (already installed) | `set_payload` by point IDs |
| httpx | >=0.27 (already used in hook) | Fire-and-forget POST |
| SQLAlchemy async | Already used | New models follow gap events pattern |
| Alembic | Already used | Two new migrations |

No new external dependencies.

---

## Implementation Order

1. DB migrations (portal-api) — foundation for all other tasks
2. Portal-api endpoints — retrieval-log + kb-feedback
3. LiteLLM hook — retrieval log fire-and-forget
4. Knowledge ingest — payload init
5. Retrieval API — quality score boost
6. LibreChat patch — feedback bridge
7. TTL cleanup + analytics

Tasks 3–6 can proceed in parallel after Tasks 1–2 are complete.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Time-window correlation mismatches (wrong chunk assigned to feedback) | Medium | Low | Window is tight; worst case = feedback goes unlinked, no wrong signal applied |
| LibreChat container not having access to portal-api internal URL | Low | Medium | Docker network already shared; same pattern as LiteLLM → portal-api |
| Qdrant set_payload race condition (concurrent thumbs on same chunk) | Low | Low | Running average is commutative; eventual consistency is fine |
| Popularity bias accumulating on high-traffic chunks | Medium | Low | Monitored via Grafana; diversity (MMR) is a future mitigation |
| Embedding model version mismatch | Low | Medium | Version field tracked; old feedback not applied after model update |
