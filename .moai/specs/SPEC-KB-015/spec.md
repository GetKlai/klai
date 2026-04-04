---
id: SPEC-KB-015
version: 0.1.0
status: draft
created: 2026-04-04
updated: 2026-04-04
author: Mark Vletter
priority: medium
---

# SPEC-KB-015 — Self-Learning Knowledge Base via Feedback Signals

## HISTORY

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-04-04 | Initial draft |

---

## Context

Klai's retrieval pipeline is sophisticated but static — it never learns from user behaviour. LibreChat exposes a thumbs up/down button on every AI response. This signal currently disappears. This SPEC closes the loop: every feedback event becomes a quality signal that improves retrieval for the same organisation.

Phase 1 (this SPEC): capture retrieval context → correlate with feedback → update Qdrant quality scores → blend into ranking → emit analytics.
Phase 2 (SPEC-KB-016): cross-encoder fine-tuning on 1000+ accumulated feedback triples.

See [research.md](research.md) for full literature review, LibreChat internals analysis, and codebase investigation.

---

## Requirements

### Module 1: Retrieval Log Capture

**REQ-KB-015-01** (UBIQUITOUS)
The system shall log every successful knowledge retrieval event to an ephemeral store (Redis), recording the Qdrant point IDs of all returned chunks, the resolved query, reranker scores, org/user context, and the embedding model version, within 200ms of the retrieval response being sent to LiteLLM.

**REQ-KB-015-02** (EVENT-DRIVEN)
WHEN the `KlaiKnowledgeHook` retrieves chunks from the retrieval-api and the gate is not bypassed, the system shall write a retrieval log entry to Redis containing: `org_id`, `user_id`, `chunk_ids[]`, `reranker_scores[]`, `query_resolved`, `embedding_model_version`, and `retrieved_at` timestamp.

**REQ-KB-015-03** (UNWANTED BEHAVIOR)
IF the Redis write fails, THEN the system shall silently discard the log event and continue serving the enriched response without delay, retry, or error propagation.

**REQ-KB-015-04** (UBIQUITOUS)
Retrieval log entries shall expire automatically after 1 hour via Redis TTL. No persistent database table is used for retrieval logs.

---

### Module 2: LibreChat Feedback Bridge

**REQ-KB-015-05** (EVENT-DRIVEN)
WHEN a user submits thumbs up or thumbs down feedback in LibreChat, the system shall forward the feedback event to `POST /internal/v1/kb-feedback` on portal-api, including: `conversation_id`, `message_id`, `message_created_at` (ISO 8601), `rating` ('thumbsUp'/'thumbsDown'), `tag`, `text`, and `librechat_tenant_id`. The `librechat_user_id` is used only transiently for Redis correlation and shall not be stored persistently.

The bridge is implemented as a volume-mounted patch file (`deploy/librechat/patches/feedback.js`) following the established Klai patching pattern. The patch overrides `/app/api/server/routes/messages.js` in the LibreChat container and adds a fire-and-forget `fetch()` call after the existing `db.updateMessage()` write. The env vars `PORTAL_INTERNAL_URL` and `PORTAL_INTERNAL_SECRET` gate the forward — if either is unset, no forward occurs.

**REQ-KB-015-06** (UBIQUITOUS)
The feedback forwarding shall be non-blocking: LibreChat shall return the feedback confirmation to the user immediately, without waiting for portal-api to respond.

**REQ-KB-015-07** (UNWANTED BEHAVIOR)
IF portal-api is unreachable when feedback is forwarded, THEN the system shall silently discard the forward without affecting the user experience. The feedback remains stored in MongoDB by LibreChat's existing mechanism.

---

### Module 3: Feedback Endpoint and Correlation

**REQ-KB-015-08** (UBIQUITOUS)
Portal-api shall expose `POST /internal/v1/kb-feedback` secured by the existing internal bearer token, that accepts feedback events from LibreChat, resolves `org_id` from `librechat_tenant_id` via `portal_orgs.librechat_container`, and correlates the feedback with a Redis retrieval log entry.

**REQ-KB-015-09** (UBIQUITOUS)
The correlation shall look up the Redis key for `(org_id, user_id)` and select the entry where `retrieved_at` falls in the interval `[message_created_at − 60s, message_created_at + 10s]`. When multiple entries exist within the window, the system shall select the entry with `retrieved_at` closest to and before `message_created_at`.

**REQ-KB-015-10** (UBIQUITOUS)
Every feedback event shall be persisted to `portal_feedback_events` regardless of whether correlation succeeds. The table shall store: `org_id`, `rating`, `tag`, `feedback_text`, `chunk_ids[]` (empty when uncorrelated), `correlated` (bool), `model_alias`, `occurred_at`. No user identifier shall be stored persistently.

**REQ-KB-015-11** (UNWANTED BEHAVIOR)
IF `librechat_tenant_id` does not match any `portal_orgs.librechat_container`, THEN the endpoint shall return HTTP 404 and discard the event without storing it.

**REQ-KB-015-12** (UNWANTED BEHAVIOR)
IF a `(message_id, conversation_id)` combination has already been processed (tracked in Redis for 1 hour), THEN the endpoint shall return HTTP 200 (idempotent) without creating a duplicate feedback event or re-applying Qdrant updates.

**REQ-KB-015-13** (UBIQUITOUS)
All feedback data shall be strictly tenant-isolated via RLS, identical to the `portal_retrieval_gaps` pattern. A feedback event for org A shall never influence Qdrant quality scores for chunks belonging to org B.

---

### Module 4: Qdrant Quality Score Updates

**REQ-KB-015-14** (EVENT-DRIVEN)
WHEN a feedback event is successfully correlated with a retrieval log entry, the system shall update the `quality_score` and `feedback_count` payload fields on each correlated chunk in Qdrant.

**REQ-KB-015-15** (UBIQUITOUS)
The quality score update formula shall be a running weighted average over a binary signal:
- `signal = 1.0` for thumbsUp, `signal = 0.0` for thumbsDown
- `quality_score_new = (quality_score_old * feedback_count + signal) / (feedback_count + 1)`
- `feedback_count_new = feedback_count + 1`

**REQ-KB-015-16** (UBIQUITOUS)
The `quality_score` field shall be initialised to `0.5` (neutral) and `feedback_count` to `0` at chunk ingest time for all newly ingested chunks.

**REQ-KB-015-17** (UNWANTED BEHAVIOR)
IF a chunk_id in the retrieval log no longer exists in Qdrant, THEN the system shall silently skip that chunk_id without error and continue processing remaining chunks.

**REQ-KB-015-18** (UBIQUITOUS)
Qdrant payload updates shall be non-blocking: portal-api shall fire and forget the update task. The feedback HTTP response shall not wait for Qdrant confirmation.

---

### Module 5: Quality Score Integration in Retrieval

**REQ-KB-015-19** (STATE-DRIVEN)
WHILE a chunk's `feedback_count >= 3`, the retrieval-api shall apply a quality score boost to the chunk's final ranking score after RRF merging:
`boosted_score = rrf_score * (1 + 0.2 * (quality_score - 0.5))`

This formula produces: thumbsUp-dominant chunks (quality_score > 0.5) get a positive boost; thumbsDown-dominant chunks (quality_score < 0.5) get a penalty; neutral chunks are unaffected.

**REQ-KB-015-20** (UNWANTED BEHAVIOR)
IF a chunk's `feedback_count < 3`, THEN the system shall not apply any quality score adjustment (cold start guard).

**REQ-KB-015-21** (UNWANTED BEHAVIOR)
IF Qdrant returns a chunk without `quality_score` or `feedback_count` payload fields (chunks ingested before this SPEC), THEN the system shall treat them as `quality_score = 0.5, feedback_count = 0` and apply no boost.

---

### Module 6: Analytics

**REQ-KB-015-22** (EVENT-DRIVEN)
WHEN a feedback event is received and stored, the system shall emit a product event of type `knowledge.feedback` to the `product_events` table with fields: `org_id`, `user_id`, `rating`, `correlated` (bool), `chunk_count` (int, 0 if uncorrelated).

**REQ-KB-015-23** (UBIQUITOUS)
A Grafana dashboard panel shall display: feedback volume per org per day, thumbsUp/thumbsDown ratio over time, correlation success rate, and the 10 chunks with the highest `feedback_count`.

---

## Non-Functional Requirements

**NFR-KB-015-01** — The retrieval log fire-and-forget shall add ≤ 5ms to LiteLLM hook processing time.

**NFR-KB-015-02** — Qdrant quality score updates shall complete within 500ms per chunk batch (fire-and-forget, measured separately).

**NFR-KB-015-03** — All feedback tables shall be RLS-protected identical to `portal_retrieval_gaps`.

**NFR-KB-015-04** — Feedback processing shall be idempotent on `(message_id, conversation_id)`.

**NFR-KB-015-05** — All processing steps shall emit structured log entries with `org_id`, `correlation_result`, `chunk_count`, and timing.

**NFR-KB-015-06** — Every retrieval log entry shall store `embedding_model_version`. When the embedding model is updated, existing feedback with the old version string shall not be applied to quality score calculations.

---

## Design Rationale

### Why Redis for retrieval logs (not PostgreSQL)

Feedback is given within minutes of receiving an AI response — never days later. The retrieval log is a transient correlation key, not persistent data. Redis with a 1-hour TTL matches the actual use case exactly and avoids storing user-linked retrieval data in the application database. If Redis is unreachable, feedback is stored as uncorrelated (correlated=false) and the user experience is unaffected.

### Privacy design: no persistent user identifier

`portal_feedback_events` stores no `user_id`. The feedback signal (which chunks were good/bad) is valuable at org level and for training data (KB-016), but does not need to be attributed to an individual. The `librechat_user_id` is used transiently during Redis correlation and is never written to PostgreSQL.

Idempotency (preventing duplicate feedback processing) is handled in Redis via a short-lived key on `(message_id, conversation_id)` — the raw IDs are never persisted.

### Why model_alias is stored

Future analytics goal: understand whether user satisfaction correlates with the LLM model used (klai-primary vs klai-large). This does not require user-level attribution — org-level aggregation is sufficient.

### Quality score formula design

The running weighted average `(old * count + signal) / (count + 1)` is a standard online learning pattern for binary signals. It has two properties that matter here:

- **Commutativity**: the order of feedback events does not affect the final score
- **Recency-neutral**: older and newer feedback have equal weight (acceptable for Phase 1; SPEC-KB-016 can introduce decay if needed)

The `signal = 1.0 / 0.0` encoding means the quality_score converges toward the fraction of positive feedback: 1.0 = all thumbs up, 0.0 = all thumbs down, 0.5 = neutral or equal split.

### Boost factor (0.2) and cold start threshold (3)

`boosted_score = rrf_score * (1 + 0.2 * (quality_score - 0.5))`

At maximum signal (quality_score = 1.0 or 0.0), the boost/penalty is ±10% of the RRF score. This is intentionally conservative — feedback is sparse and potentially noisy. A 10% adjustment influences ranking without dominating it.

The cold start guard (feedback_count >= 3) prevents a single data point from affecting ranking. 3 is a conservative minimum chosen for a B2B platform with lower per-org traffic. This value is configurable and should be revisited once real feedback volume data is available.

These values (0.2, 3) are design choices, not hard standards. They should be tuned based on observed correlation between quality_score and actual user satisfaction after Phase 1 data accumulates.

**Literature validation (2026-04-04):**
- The running average formula is workable but Bayesian averaging (Evan Miller / Algolia pattern) is more principled for sparse feedback. Bayesian averaging uses a prior (prior mean m, confidence weight C) to moderate early scores toward a neutral prior — this eliminates the need for a hard cold start threshold. Deferred to Phase 2 when feedback volume data is available.
- The boost factor of 0.2 is conservative relative to production systems (Algolia uses ~0.3-0.4 in A/B tests). 0.2 is the safe starting posture; candidate values for A/B testing are 0.25 and 0.35.
- The cold start threshold of 3 has no empirical consensus in literature. The range 3-5 is cited as reasonable. Revisit after observing feedback rate per org.
- The Qdrant `set_payload` approach for real-time quality updates is a validated production pattern, explicitly supported in Qdrant 1.17+.

Sources: Evan Miller on Bayesian Ratings, Algolia Custom Ranking docs, Qdrant 1.17 release notes, FLAIR (arXiv 2508.13390).

---

## Out of Scope

- RAGAS evaluation pipeline integration
- Cross-encoder fine-tuning on accumulated feedback — SPEC-KB-016
- Qdrant native relevance feedback query — SPEC-KB-016
- Feedback UI natively in the Klai portal (outside LibreChat)
- Implicit signals: dwell time, re-ask patterns
- Feedback on non-KB responses (`gate_bypassed = true`)
