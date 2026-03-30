# SPEC-KB-015: Knowledge Gap Validation and Automatic Closure

**Status:** Completed
**Priority:** High
**Created:** 2026-03-27
**Completed:** 2026-03-27
**Commit:** a331124 (`feat(knowledge): auto-close gaps when retrieval confidence recovers (SPEC-KB-015)`)
**Related:** SPEC-KB-014 (Gap Detection), SPEC-KB-009 (Docs-to-Qdrant sync)

---

## Background / Problem Statement

Klai's knowledge gap detection system (SPEC-KB-014) records queries that produce low-confidence retrieval results. These gaps surface on the `/app/gaps` dashboard, signalling to org admins that their knowledge base has coverage holes.

However, gaps are never automatically resolved. When an admin writes a new page, imports content via a connector, or restructures existing knowledge, the gap dashboard continues to show stale entries that may already be covered by the new content. This creates three problems:

1. **Dashboard noise:** Admins lose trust in the gap dashboard because it shows problems that have already been fixed by adding content.
2. **Manual overhead:** There is no way to mark a gap as resolved short of waiting for the 30-day sliding window to age it out.
3. **Missed feedback loop:** The platform cannot confirm to the admin that their content addition actually closed a specific knowledge gap.

---

## Goals

- Automatically re-evaluate open gap queries when new content is ingested into the knowledge base.
- Mark gaps as resolved when retrieval confidence now exceeds the classification threshold.
- Remove resolved gaps from the default dashboard view so only actionable gaps remain visible.
- Provide a lightweight feedback signal that content additions are closing gaps.

## Non-Goals

- Real-time gap closure (sub-second latency is not required; eventual consistency within minutes is acceptable).
- Gap re-opening (if a resolved gap degrades again later, it will naturally reappear as a new gap event from the LiteLLM hook).
- Bulk manual resolution UI (admin manually dismissing gaps is out of scope for this SPEC).
- Notifications or email alerts when gaps are resolved (future SPEC).

---

## Assumptions

| # | Assumption | Confidence | Risk if Wrong |
|---|-----------|------------|---------------|
| A1 | The retrieval API (`KNOWLEDGE_RETRIEVE_URL`) returns scores comparable to those used by `_classify_gap` in the LiteLLM hook. | High | Re-scoring would use different scale, causing false resolutions. Validate by comparing score ranges in both paths. |
| A2 | An org typically has fewer than 200 distinct open gap queries at any given time. | Medium | Rate limiting cap of 50 per trigger may be insufficient for large orgs. Monitor after launch. |
| A3 | Qdrant indexing completes before the re-scoring job runs. The Gitea webhook sync and connector ingest both write to Qdrant before the portal callback fires. | High | If re-scoring runs before Qdrant indexing finishes, the new content is invisible and the gap stays open. It will be resolved on the next trigger. |
| A4 | The `nearest_kb_slug` field on gap rows reliably identifies which KB the gap is associated with. For hard gaps (no chunks returned), this field is NULL. | High | Scoping re-scoring by KB slug would miss hard gaps. Must fall back to org-wide re-scoring for hard gaps. |

---

## Requirements

### R1: Schema Extension (Ubiquitous)

The `portal_retrieval_gaps` table **shall** include a nullable `resolved_at` column of type `DateTime(timezone=True)`.

**Acceptance Criteria:**

```gherkin
Given the portal database
When Alembic migration SPEC-KB-015 is applied
Then the portal_retrieval_gaps table has a nullable column resolved_at of type TIMESTAMPTZ
And existing rows have resolved_at = NULL
And a partial index ix_retrieval_gaps_open exists on (org_id, query_text) WHERE resolved_at IS NULL
```

### R2: Gap Re-Scoring on Page Save (Event-Driven)

**When** a page is saved in a Klai-native knowledge base (Gitea-backed), the system **shall** schedule a re-scoring job that:

1. Fetches distinct open gap queries for the org where `nearest_kb_slug` matches the saved page's KB slug OR `nearest_kb_slug IS NULL` (hard gaps).
2. Limits to the 50 most recent distinct queries within the last 30 days.
3. Calls the retrieval API for each query with the org's context.
4. Marks all gap rows for a given `(query_text, gap_type)` pair as resolved (sets `resolved_at = now()`) if `_classify_gap` on the new retrieval result returns `None` (i.e., the query no longer qualifies as a gap).

**Acceptance Criteria:**

```gherkin
Scenario: Page save resolves a soft gap
  Given org "acme" has an open soft gap for query "What is our refund policy?"
    with nearest_kb_slug = "company-handbook"
  And the gap occurred within the last 30 days
  When an admin saves a page titled "Refund Policy" in KB "company-handbook"
  And the re-scoring job runs
  And retrieval for "What is our refund policy?" now returns chunks with reranker_score >= 0.4
  Then all gap rows matching query_text = "What is our refund policy?" AND gap_type = "soft"
    for org "acme" have resolved_at set to the current timestamp

Scenario: Page save does not resolve an unrelated gap
  Given org "acme" has an open soft gap for query "How do I reset my password?"
    with nearest_kb_slug = "it-support"
  When an admin saves a page in KB "company-handbook"
  Then the gap for "How do I reset my password?" remains unresolved (resolved_at IS NULL)

Scenario: Page save re-evaluates hard gaps for the org
  Given org "acme" has an open hard gap for query "What is our travel policy?"
    with nearest_kb_slug = NULL
  When an admin saves a page in any KB belonging to org "acme"
  Then the hard gap is included in the re-scoring batch
```

### R3: Gap Re-Scoring on Connector Sync Completion (Event-Driven)

**When** a connector sync completes successfully (status callback received at `/internal/v1/connectors/{id}/sync-status` with `status = "success"`), the system **shall** schedule a re-scoring job for all open gap queries belonging to the connector's org.

The re-scoring job follows the same logic as R2 (50 query cap, 30-day window, retrieval API call, threshold check).

**Acceptance Criteria:**

```gherkin
Scenario: Connector sync resolves gaps
  Given org "acme" has 3 open gaps and a GitHub connector on KB "engineering-wiki"
  When klai-connector posts sync-status with status = "success" for that connector
  Then the re-scoring job evaluates all 3 open gaps for org "acme"
  And any gap whose retrieval now passes the threshold has resolved_at set

Scenario: Failed sync does not trigger re-scoring
  Given org "acme" has open gaps
  When klai-connector posts sync-status with status = "failed"
  Then no re-scoring job is triggered
```

### R4: Dashboard Filters Out Resolved Gaps (State-Driven)

**While** the gap dashboard shows gap data, the system **shall** exclude rows where `resolved_at IS NOT NULL` from the default listing.

**Acceptance Criteria:**

```gherkin
Scenario: Default listing hides resolved gaps
  Given org "acme" has 5 gap groups, 2 of which are resolved
  When an admin views GET /api/app/gaps (default)
  Then the response contains 3 gap groups
  And resolved gaps are not included

Scenario: Include-resolved parameter shows all gaps
  Given org "acme" has 5 gap groups, 2 of which are resolved
  When an admin views GET /api/app/gaps?include_resolved=true
  Then the response contains 5 gap groups
  And resolved groups include a resolved_at timestamp

Scenario: Summary endpoint counts only open gaps
  Given org "acme" has 10 gap events in the last 7 days, 4 of which are resolved
  When an admin views GET /api/app/gaps/summary
  Then total_7d = 6, not 10
```

### R5: Rate Limiting and Scope Control (Unwanted)

The system **shall not** re-score more than 50 distinct gap queries per ingest trigger event.

The system **shall not** re-score gap queries older than 30 days (based on `occurred_at`).

**Acceptance Criteria:**

```gherkin
Scenario: Cap at 50 queries
  Given org "acme" has 120 distinct open gap queries within the last 30 days
  When a page save triggers re-scoring
  Then exactly 50 queries are sent to the retrieval API
  And the 50 most recently occurred queries are selected

Scenario: Old gaps excluded
  Given org "acme" has a gap query from 45 days ago that is still open
  When a re-scoring job runs
  Then that gap query is not evaluated
```

### R6: Re-Scoring Uses Same Thresholds as Gap Detection (Ubiquitous)

The re-scoring job **shall** use the same `_classify_gap` logic and threshold values (`KLAI_GAP_SOFT_THRESHOLD`, `KLAI_GAP_DENSE_THRESHOLD`) as the LiteLLM hook to determine whether a gap is resolved.

**Acceptance Criteria:**

```gherkin
Given KLAI_GAP_SOFT_THRESHOLD = 0.4 and KLAI_GAP_DENSE_THRESHOLD = 0.35
When a re-scoring job retrieves chunks for a gap query
Then the same classification function determines resolution
And the thresholds are read from environment variables (not hardcoded)
```

---

## Open Questions

| # | Question | Impact | Default if Unanswered |
|---|----------|--------|-----------------------|
| Q1 | Should re-scoring run synchronously in the request handler (page save / sync callback) or be dispatched to a background task queue? | Synchronous is simpler but adds latency to page saves. Background requires task infrastructure (e.g., `asyncio.create_task` or a dedicated worker). | Start with `asyncio.create_task` fire-and-forget in the FastAPI process, matching the pattern used by `_fire_gap_event` in the LiteLLM hook. Upgrade to a task queue if latency or reliability becomes an issue. |
| Q2 | Should the retrieval API call include `conversation_history` context? Gap queries were originally asked with conversation context that is no longer available. | Without history, coreference resolution ("hij" -> "Jan") will not work, potentially leaving some gaps unresolved that contextually were answered. | No conversation history for re-scoring. The query text alone determines resolution. Gaps that relied on conversational context for their meaning are edge cases. |
| Q3 | Should the frontend show a count of recently resolved gaps (e.g., "3 gaps resolved this week") as a positive feedback signal? | UX improvement but adds frontend scope. | Out of scope for this SPEC. Can be added as a follow-up enhancement. |
| Q4 | For page saves, should we wait for the Qdrant webhook sync to confirm completion before triggering re-scoring, or use a fixed delay? | Premature re-scoring will miss the new content. | Use a 5-second delay (`asyncio.sleep(5)`) before starting re-scoring after page save. The Gitea webhook -> docs-app -> Qdrant pipeline typically completes in 2-3 seconds. |

---

## Technical Approach Sketch

### 1. Schema Migration

Add `resolved_at` to `PortalRetrievalGap`:

```python
# In model
resolved_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True, default=None
)

# Alembic migration adds:
# - Column resolved_at TIMESTAMPTZ NULL
# - Partial index: CREATE INDEX ix_retrieval_gaps_open
#     ON portal_retrieval_gaps (org_id, query_text)
#     WHERE resolved_at IS NULL
```

### 2. Re-Scoring Service

New module `klai-portal/backend/app/services/gap_rescorer.py`:

```python
async def rescore_gaps_for_org(
    org_id: int,
    zitadel_org_id: str,
    kb_slug: str | None,  # None = all org KBs (connector sync case)
    db: AsyncSession,
) -> int:
    """Re-score open gaps and mark resolved ones. Returns count resolved."""
```

Core logic:
1. Query distinct `(query_text, gap_type)` from `portal_retrieval_gaps` WHERE `org_id = ?` AND `resolved_at IS NULL` AND `occurred_at >= now() - 30 days`. If `kb_slug` is provided, additionally filter to `nearest_kb_slug = kb_slug OR nearest_kb_slug IS NULL`.
2. Order by `MAX(occurred_at) DESC`, limit 50.
3. For each query, POST to `KNOWLEDGE_RETRIEVE_URL` with `{query, org_id: zitadel_org_id, user_id: "system", scope: "org", top_k: 5}`.
4. Run `_classify_gap(chunks)` on the response. If result is `None` (no longer a gap), UPDATE all matching rows SET `resolved_at = now()`.
5. Return the count of resolved query groups.

### 3. Trigger Integration Points

**Page save** -- in `app_knowledge_bases.py` or via a webhook handler, after the page write completes:

```python
# After page save commit, schedule re-scoring with delay
asyncio.get_running_loop().create_task(
    _delayed_rescore(org_id, zitadel_org_id, kb_slug, db_factory)
)
```

Where `_delayed_rescore` sleeps 5 seconds, opens a fresh DB session, and calls `rescore_gaps_for_org`.

**Connector sync callback** -- in `internal.py` `receive_sync_status`, after updating the connector record:

```python
if body.status == "success":
    asyncio.get_running_loop().create_task(
        rescore_gaps_for_org(connector.org_id, org.zitadel_org_id, None, db_factory)
    )
```

### 4. Dashboard Query Changes

In `app_gaps.py`:
- Add `resolved_at IS NULL` to the WHERE clause in `list_gaps` (default behavior).
- Add optional query param `include_resolved: bool = False`. When true, omit the filter.
- Add `resolved_at` to `GapOut` response model (nullable).
- Update `get_gap_summary` to count only open gaps.

### 5. Threshold Sharing

Extract `_classify_gap` and the threshold constants into a shared module (`app/services/gap_classification.py`) importable by both the LiteLLM hook and the portal backend. Alternatively, duplicate the logic in the portal backend since the LiteLLM hook runs in a separate container -- use the same env var names for consistency.

---

## Files Affected

| File | Change |
|------|--------|
| `klai-portal/backend/app/models/retrieval_gaps.py` | Add `resolved_at` column |
| `klai-portal/backend/alembic/versions/xxxx_add_resolved_at_to_gaps.py` | New migration |
| `klai-portal/backend/app/services/gap_rescorer.py` | New module: re-scoring logic |
| `klai-portal/backend/app/services/gap_classification.py` | New module: extracted `_classify_gap` + thresholds |
| `klai-portal/backend/app/api/app_gaps.py` | Filter resolved gaps, add `include_resolved` param, update summary |
| `klai-portal/backend/app/api/internal.py` | Trigger re-scoring on sync-status callback (success only) |
| `klai-portal/backend/app/api/app_knowledge_bases.py` | Trigger re-scoring after page save (via Gitea webhook or save handler) |
| `klai-portal/backend/app/core/config.py` | Add `KLAI_GAP_SOFT_THRESHOLD`, `KLAI_GAP_DENSE_THRESHOLD` settings |

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Re-scoring runs before Qdrant indexing completes, missing new content | Medium | Low (gap stays open, resolved on next trigger) | 5-second delay after page save; connector sync callback fires only after sync is fully complete |
| Retrieval API is slow or unavailable during re-scoring | Low | Medium (gaps stay open) | 3-second timeout per query, fire-and-forget pattern, log warnings |
| High volume of gap queries overwhelms retrieval API | Low | Medium (retrieval latency for real users) | Hard cap of 50 queries per trigger, sequential execution (not parallel) |
| Threshold drift between LiteLLM hook and portal backend | Low | High (inconsistent gap/resolution classification) | Both read from same env vars; extract shared classification logic |

---

## Implementation Milestones

| # | Milestone | Priority |
|---|-----------|----------|
| M1 | Schema migration: add `resolved_at` column + partial index | Primary Goal |
| M2 | Gap classification module: extract `_classify_gap` into `gap_classification.py` | Primary Goal |
| M3 | Re-scoring service: `gap_rescorer.py` with retrieval API integration | Primary Goal |
| M4 | Trigger on connector sync: integrate into `receive_sync_status` | Primary Goal |
| M5 | Trigger on page save: integrate into page save handler with delay | Primary Goal |
| M6 | Dashboard update: filter resolved gaps, add `include_resolved` param | Primary Goal |
| M7 | Update gap summary endpoint to exclude resolved gaps | Secondary Goal |
| M8 | Logging and observability: log re-scoring results per trigger | Secondary Goal |
| M9 | Frontend: resolved gaps toggle on `/app/gaps` dashboard | Optional Goal |

---

## Implementation Notes

> Added automatically by `/moai sync SPEC-KB-015` on 2026-03-27. Level 1 (spec-first): SPEC is now closed.

### What Was Built

All Primary Goal milestones (M1–M7) were implemented in a single commit. M8 (structured logging) was included as part of normal async error handling. M9 (frontend toggle) remains deferred as an optional follow-up.

### Files Created

| File | Purpose |
|------|---------|
| `klai-portal/backend/alembic/versions/f8a9b0c1d2e3_add_resolved_at_to_retrieval_gaps.py` | Alembic migration: adds `resolved_at TIMESTAMPTZ NULL` column and partial index `ix_retrieval_gaps_open` (WHERE resolved_at IS NULL) |
| `klai-portal/backend/app/services/gap_classification.py` | Ports `_classify_gap` from `deploy/litellm/klai_knowledge.py` into portal backend; reads thresholds from `KLAI_GAP_SOFT_THRESHOLD` / `KLAI_GAP_DENSE_THRESHOLD` env vars |
| `klai-portal/backend/app/services/gap_rescorer.py` | `rescore_open_gaps()` (queries retrieval API, marks gaps resolved) + `schedule_rescore()` (fire-and-forget wrapper with configurable delay) |
| `klai-portal/backend/tests/test_gap_classification.py` | 10 unit tests covering all threshold/score path combinations |
| `klai-portal/backend/tests/test_gap_rescorer.py` | 7 unit tests: no-URL skip, resolve happy path, stays-open path, 50-query cap, empty gaps, retrieval error, network exception |

### Files Modified

| File | Change |
|------|--------|
| `klai-portal/backend/app/models/retrieval_gaps.py` | Added `resolved_at` column + `ix_retrieval_gaps_open` partial index |
| `klai-portal/backend/app/core/config.py` | Added `klai_gap_soft_threshold: float = 0.4`, `klai_gap_dense_threshold: float = 0.35`, `knowledge_retrieve_url: str = ""` |
| `klai-portal/backend/app/api/internal.py` | Added `schedule_rescore()` call on `status == "success"` in `receive_sync_status`; added new `POST /internal/v1/orgs/{org_id}/page-saved` endpoint |
| `klai-portal/backend/app/api/app_gaps.py` | Added `include_resolved: bool = Query(default=False)` param; added `resolved_at IS NULL` filter when false; added `resolved_at` field to `GapOut`; added `resolved_at IS NULL` filter to `get_gap_summary` |
| `klai-portal/backend/app/api/app_knowledge_bases.py` | Added `resolved_at IS NULL` to gap count query in KB stats |

### Divergences from Plan

| Item | Plan | Actual |
|------|------|--------|
| Migration revision ID | `a1b2c3d4e5f6` | `f8a9b0c1d2e3` (collision with existing migration) |
| Page-save trigger location | `app_knowledge_bases.py` | New internal endpoint `POST /internal/v1/orgs/{org_id}/page-saved` (klai-docs must call this after Gitea webhooks) |

### Deployment Prerequisites

1. **Run Alembic migration** (`f8a9b0c1d2e3_add_resolved_at_to_retrieval_gaps`): `cd klai-portal/backend && alembic upgrade head`
2. **Add env var** `KNOWLEDGE_RETRIEVE_URL` to `/opt/klai/.env` (e.g. `http://retrieval-api:8000`); without it, re-scoring is silently skipped
3. **Update klai-docs** to call `POST /internal/v1/orgs/{org_id}/page-saved` after processing Gitea push webhooks (follow-up task, not in this SPEC)

### Test Coverage

17 new tests, all passing. TRUST 5: PASS (ruff clean, pyright 0 errors, 85%+ coverage on new modules).

### Open Questions Resolved

| Q | Resolution |
|---|-----------|
| Q1 (async vs queue) | `asyncio.create_task` fire-and-forget; upgrade path to task queue deferred |
| Q2 (conversation history) | Not included — query text alone determines resolution |
| Q3 (frontend resolved count) | Out of scope; deferred to follow-up |
| Q4 (delay strategy) | 5-second `asyncio.sleep` after page save, 0-second delay after connector sync |
