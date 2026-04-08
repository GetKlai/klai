---
id: SPEC-KB-021
phase: acceptance
---

# Acceptance Criteria — SPEC-KB-021

## AC-1: Chunk tagging at ingest

**Given** a KB with taxonomy nodes `["Billing", "Technical Support"]`
**When** a document titled "How to pay your invoice" is ingested
**Then** all Qdrant chunks for that document have `taxonomy_node_id = <id of Billing node>`

---

**Given** a KB with taxonomy nodes `["Billing", "Technical Support"]`
**When** a document is ingested whose content does not match any node with confidence ≥ 0.5
**Then** all chunks for that document have `taxonomy_node_id = null`

---

**Given** a KB with NO taxonomy nodes
**When** any document is ingested
**Then** the Qdrant chunk payloads do NOT contain a `taxonomy_node_id` field
**And** the document IS added to the unmatched batch for proposal generation

---

**Given** the classification LLM call times out (> 5 seconds)
**When** a document is being ingested
**Then** ingest completes successfully and chunks have `taxonomy_node_id = null` (no crash, no retry)

## AC-2: Qdrant payload index

**Given** the application starts up
**When** `_ensure_payload_indexes()` runs
**Then** a keyword index exists on `taxonomy_node_id` in the `klai_knowledge` collection

## AC-3: Retrieval filter

**Given** a KB with chunks tagged `taxonomy_node_id = 5` (Billing) and `taxonomy_node_id = 7` (Technical)
**When** a retrieve request includes `{ "taxonomy_node_ids": [5] }`
**Then** only chunks with `taxonomy_node_id = 5` are returned (Technical chunks excluded)

---

**Given** a retrieve request with no `taxonomy_node_ids` field
**When** the retrieval pipeline runs
**Then** no taxonomy filter is applied and all chunks are eligible (existing behavior)

---

**Given** a retrieve request with `taxonomy_node_ids: []` (empty list)
**When** the retrieval pipeline runs
**Then** no taxonomy filter is applied (empty = all, same as absent)

## AC-4: Proposal generation

**Given** a batch of 5 documents ingested into a KB with 0 existing taxonomy nodes (self-bootstrap scenario)
**And** `PORTAL_INTERNAL_TOKEN` is configured
**When** the batch ingest completes
**Then** proposals are generated for the clustered documents
**And** the proposals appear in the portal review queue with status `pending`

---

**Given** a batch of 5 documents ingested into a KB where 4 have `taxonomy_node_id = null`
**And** `PORTAL_INTERNAL_TOKEN` is configured
**And** no pending proposal with the same suggested name exists
**When** the batch ingest completes
**Then** exactly 1 proposal is submitted to the portal with `proposal_type = "new_node"`
**And** the proposal appears in the portal's review queue with status `pending`

---

**Given** `PORTAL_INTERNAL_TOKEN` is NOT set
**When** a batch ingest with ≥3 unmatched documents completes
**Then** no proposal is submitted
**And** a warning is logged: `taxonomy_proposal_skipped: missing PORTAL_INTERNAL_TOKEN`
**And** ingest does NOT fail

---

**Given** a proposal with name "API Documentation" was submitted < 24 hours ago for the same KB
**When** another batch ingest would generate the same proposal name
**Then** the proposal is NOT resubmitted (deduplication)

## AC-5: Backfill endpoint

**Given** a KB with 100 existing chunks, all without `taxonomy_node_id`
**And** the KB has 2 taxonomy nodes
**When** `POST /ingest/v1/taxonomy/backfill` is called with `{ "org_id": "...", "kb_slug": "..." }`
**Then** the response contains `{ "processed": N, "tagged": M, "skipped": 0 }` where M ≤ N
**And** chunks that matched a node have `taxonomy_node_id` set in Qdrant
**And** chunks with confidence < 0.5 have `taxonomy_node_id = null`

---

**Given** backfill is run a second time on the same KB (already-tagged chunks)
**When** `POST /ingest/v1/taxonomy/backfill` is called again
**Then** the response contains `{ "processed": 0, "tagged": 0, "skipped": N }` (idempotent)

---

**Given** the endpoint is called without `X-Internal-Token` header
**When** the request hits the endpoint
**Then** the response is `401 Unauthorized`

## AC-6: Gap event taxonomy signal

**Given** a retrieve request includes `taxonomy_node_ids: [5]`
**And** retrieval returns no results (hard gap)
**When** the gap event is fired
**Then** the gap event payload includes `taxonomy_node_ids: [5]`

---

**Given** a retrieve request has NO `taxonomy_node_ids`
**And** a gap is detected
**When** the gap event is fired
**Then** the gap event payload does NOT include `taxonomy_node_ids` (no change to existing behavior)

## AC-7: No regression

**Given** all existing unit and integration tests
**When** this feature is deployed
**Then** all existing tests pass (zero regressions)

---

**Given** production retrieval latency baseline (P95 ~400ms)
**When** taxonomy filter is active in a retrieve request
**Then** P95 latency does NOT increase by more than 20ms

## Test Coverage Targets

| Component | Target |
|---|---|
| `taxonomy_classifier.py` | 90%+ (mock LLM calls) |
| `portal_client.py` | 85%+ |
| `qdrant_store.py` additions | 85%+ |
| Retrieval filter | 90%+ |
| Backfill endpoint | 80%+ |
| Proposal generation | 85%+ |
