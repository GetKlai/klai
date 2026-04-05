# Acceptance Criteria — SPEC-KB-015

## Scenario 1: Happy Path — Thumbs Down Reduces Chunk Score

**Given** a chunk with `chunk_id = "abc-123"`, `quality_score = 0.5`, `feedback_count = 0` in Qdrant  
**And** a user in org "klai-demo" sends a question that retrieves this chunk  
**And** the LiteLLM hook fires a retrieval log event with `chunk_ids = ["abc-123"]` to portal-api  
**And** the user clicks thumbs down on the response 20 seconds later  
**And** LibreChat forwards the feedback with `message_created_at = T` to `/internal/v1/kb-feedback`

**When** portal-api processes the feedback event

**Then** a `portal_retrieval_log` entry for `(org_id, user_id)` exists with `retrieved_at` within `[T-60s, T+10s]`  
**And** the feedback is stored in `portal_feedback_events` with `correlated = true` and `chunk_ids = ["abc-123"]`  
**And** Qdrant payload for `"abc-123"` is updated to `quality_score = 0.0, feedback_count = 1`  
**And** a `knowledge.feedback` product event is emitted with `rating = "thumbsDown", correlated = true, chunk_count = 1`

---

## Scenario 2: Cold Start Guard — Score Not Applied Before 3 Feedback Events

**Given** a chunk with `quality_score = 0.0`, `feedback_count = 2` (two thumbs down)  
**And** a user sends a query that retrieves this chunk

**When** the retrieval-api applies ranking

**Then** the chunk's RRF score is NOT modified by the quality score  
**And** the chunk appears in results at its unmodified rank

**When** a third thumbs-down feedback event is correlated with this chunk  
**Then** `feedback_count = 3` and `quality_score = 0.0`

**When** the next query retrieves this chunk  
**Then** the chunk's final score is `rrf_score * (1 + 0.2 * (0.0 - 0.5)) = rrf_score * 0.9` (10% penalty applied)

---

## Scenario 3: Positive Feedback Boosts a Chunk

**Given** a chunk with `quality_score = 0.5`, `feedback_count = 5` (three thumbs up, two neutral initialisation rounds)  
**When** a user gives thumbs up  
**Then** `quality_score = (0.5 * 5 + 1.0) / 6 ≈ 0.583`, `feedback_count = 6`  
**And** on next retrieval, `boosted_score = rrf_score * (1 + 0.2 * (0.583 - 0.5)) = rrf_score * 1.017` (1.7% boost)

---

## Scenario 4: No Retrieval Log — Feedback Stored But Not Correlated

**Given** a user gives thumbs down on a response  
**And** no matching retrieval log entry exists for `(org_id, user_id)` within the time window (e.g., gate was bypassed, or log expired)

**When** portal-api processes the feedback event

**Then** a `portal_feedback_events` row is created with `correlated = false`, `retrieval_log_id = NULL`, `chunk_ids = []`  
**And** NO Qdrant quality score updates are made  
**And** a `knowledge.feedback` product event is emitted with `correlated = false, chunk_count = 0`

---

## Scenario 5: Idempotency — Duplicate Feedback Ignored

**Given** a feedback event for `(message_id = "msg-1", conversation_id = "conv-1")` has already been processed  
**When** the same event arrives again (LibreChat retry or network duplicate)  
**Then** portal-api returns HTTP 200  
**And** no new `portal_feedback_events` row is created  
**And** no Qdrant updates are made  
**And** no duplicate product event is emitted

---

## Scenario 6: LibreChat Patch — Non-Blocking Feedback Forward

**Given** the LibreChat feedback patch is deployed  
**When** a user clicks thumbs up and portal-api has response latency of 500ms

**Then** LibreChat returns the thumbs-up confirmation to the user in < 50ms (non-blocking)  
**And** the portal-api call completes asynchronously in the background  
**And** no LibreChat error is surfaced to the user regardless of portal-api response status

---

## Scenario 7: Unknown Tenant — 404 Returned

**Given** a feedback event arrives with `librechat_tenant_id = "unknown-db"`  
**When** portal-api looks up `portal_orgs.librechat_container = "unknown-db"`

**Then** portal-api returns HTTP 404  
**And** no feedback event is stored  
**And** an error is logged with `librechat_tenant_id = "unknown-db"`

---

## Scenario 8: Legacy Chunk — No Quality Score Field

**Given** a chunk ingested before SPEC-KB-015 has no `quality_score` or `feedback_count` in its Qdrant payload

**When** the retrieval-api retrieves this chunk and applies ranking

**Then** the chunk is treated as `quality_score = 0.5, feedback_count = 0`  
**And** no boost or penalty is applied  
**And** no error is raised

---

## Performance Criteria

- P99 LiteLLM hook latency increase: ≤ 5ms (fire-and-forget only)
- Qdrant quality score update: completes within 500ms (async, not in critical path)
- Portal-api feedback endpoint: responds within 200ms (DB insert + async Qdrant task spawn)
- Retrieval ranking boost calculation: ≤ 1ms per chunk (simple arithmetic)

---

## Quality Gates

- All new endpoints covered by integration tests (real PostgreSQL, not mocked)
- Idempotency constraint verified by test
- Time-window correlation verified with synthetic retrieval_log and feedback_events data
- Quality score formula verified by unit test with known inputs/outputs
- LiteLLM hook unit test: fire-and-forget does not block response
- LibreChat patch: existing feedback test suite still passes
