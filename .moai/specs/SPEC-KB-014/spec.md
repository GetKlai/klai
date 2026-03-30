# SPEC-KB-014: Knowledge Gap Detection & UI

```yaml
spec_id: SPEC-KB-014
title: Knowledge Gap Detection
status: completed
priority: medium
created: 2026-03-27
completed: 2026-03-27
depends_on:
  - SPEC-KB-008  # retrieval-api (provides score/reranker_score signals)
  - SPEC-KB-010  # LiteLLM knowledge hook (injection point for gap detection)
lifecycle: spec-anchored
```

---

## Environment

The Klai knowledge base system ingests content from multiple sources (KB pages, GitHub repos, web crawls, transcripts) and retrieves it during chat via a LiteLLM pre-call hook (`deploy/litellm/klai_knowledge.py`). The retrieval-api returns quality signals per chunk: `score` (dense retrieval), `reranker_score` (cross-attention confidence), `candidates_retrieved`, `reranked_to`, and `gate_margin`.

Currently, when retrieval returns zero chunks or only low-confidence chunks, this event is **not tracked**. The hook logs successful injections (`chunks_injected > 0`) but silently returns when no relevant content is found. Org admins have no visibility into what questions their knowledge base cannot answer.

Gap detection was explicitly deferred in `klai-claude/docs/knowledge-ingest-flow.md` with the note: "Deferred until corpus is >50 indexed documents." The corpus threshold has been met.

### Existing Infrastructure

- **LiteLLM hook** (`deploy/litellm/klai_knowledge.py`): Pre-call hook with access to `org_id`, `user_id`, query text, chunk results, and retrieval timing. Posts to `PORTAL_API_URL` for authorization checks using `PORTAL_INTERNAL_SECRET`.
- **Internal API** (`klai-portal/backend/app/api/internal.py`): Existing service-to-service endpoint pattern with `_require_internal_token()` guard. Used by klai-mailer, Zitadel Actions, klai-connector, and the knowledge hook.
- **Portal KB stats** (`klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx`): Overview tab displays `KBStats` interface with `docs_count`, `connector_count`, `volume`, `usage_last_30d`.
- **App navigation** (`klai-portal/frontend/src/routes/app/route.tsx`): Sidebar driven by `allNavItems` array with product-gated visibility.

### Constraints

- The hook must not block on gap logging failures -- fire-and-forget semantics.
- Gap events are **org-scoped** (not per-KB), because the hook retrieves across all org KBs simultaneously and does not know which specific KB failed to answer.
- Raw query text must be stored for admin review (needed to understand knowledge gaps), but only org admins may view it.
- Privacy policy disclosure is required when storing user queries.
- 90-day retention for gap event data.

---

## Assumptions

1. The retrieval-api `reranker_score` field is reliably populated when a reranker is configured (currently Cohere reranker for all orgs).
2. The `PORTAL_INTERNAL_SECRET` shared secret is available in the LiteLLM container environment (already true for knowledge feature check).
3. Portal-api can accept additional internal POST endpoints without route conflicts.
4. Org admins already have access to KB management UI and can be trusted with query text visibility.
5. The volume of gap events is manageable in PostgreSQL (estimated: <1000/day/org at current scale).

---

## Requirements

### R1: Gap Classification (LiteLLM Hook)

**R1.1** [Ubiquitous] The hook shall classify every retrieval result into one of three categories: `success` (usable chunks returned), `hard_gap` (zero chunks after retrieval), or `soft_gap` (all chunks below confidence threshold).

**R1.2** [Event-Driven] **When** the retrieval-api returns zero chunks AND `retrieval_bypassed` is false, **then** the hook shall classify the result as `hard_gap`.

**R1.3** [State-Driven] **While** all returned chunks have a `reranker_score` below `KLAI_GAP_SOFT_THRESHOLD` (default: 0.4), the hook shall classify the result as `soft_gap`.

**R1.4** [State-Driven] **While** no reranker scores are available (reranker not configured), **and** all returned chunks have a `score` below `KLAI_GAP_DENSE_THRESHOLD` (default: 0.35), the hook shall classify the result as `soft_gap`.

**R1.5** [Optional] **Where** the `KLAI_GAP_SOFT_THRESHOLD` and `KLAI_GAP_DENSE_THRESHOLD` environment variables exist, the hook shall use them to override the default thresholds.

### R2: Gap Event Reporting (LiteLLM Hook)

**R2.1** [Event-Driven] **When** a gap (hard or soft) is detected, **then** the hook shall POST the gap event to `POST {PORTAL_API_URL}/internal/v1/gap-events` asynchronously without blocking the pre-call response.

**R2.2** [Ubiquitous] The gap event payload shall include: `org_id` (int), `user_id` (string), `query_text` (string), `gap_type` (string: "hard" | "soft"), `top_score` (float | null -- highest chunk score), `nearest_kb_slug` (string | null -- kb_slug of the highest-scoring chunk, populated for soft gaps only), `chunks_retrieved` (int), `retrieval_ms` (int).

**R2.5** [Event-Driven] **When** a soft gap is detected (chunks returned but all below threshold), **then** the hook shall set `nearest_kb_slug` to the `kb_slug` of the chunk with the highest score. **When** a hard gap is detected (zero chunks), **then** `nearest_kb_slug` shall be null.

**R2.3** [Unwanted] **If** the gap event POST fails (network error, timeout, non-2xx response), **then** the hook shall NOT retry or block -- it shall log a warning and continue.

**R2.4** [Unwanted] **If** `user_id` or `org_id` is not available in the request context, **then** the hook shall NOT report a gap event (no anonymous tracking).

### R3: Gap Event Storage (Portal Backend)

**R3.1** [Event-Driven] **When** a `POST /internal/v1/gap-events` request is received with a valid internal token, **then** the portal shall insert a row into the `portal_retrieval_gaps` table.

**R3.2** [Ubiquitous] The `portal_retrieval_gaps` table shall have the following columns: `id` (serial PK), `org_id` (FK to portal_orgs), `user_id` (text, not null), `query_text` (text, not null), `gap_type` (text, not null, check constraint: 'hard' or 'soft'), `top_score` (float, nullable), `nearest_kb_slug` (text, nullable -- populated for soft gaps; null for hard gaps), `chunks_retrieved` (int, not null), `retrieval_ms` (int, not null), `occurred_at` (timestamptz, server default now()).

**R3.3** [Ubiquitous] The table shall have indexes on `(org_id, occurred_at)` for efficient time-range queries and on `(org_id, query_text)` for frequency aggregation.

**R3.4** [Unwanted] **If** the internal token is missing or invalid, **then** the endpoint shall return 401 Unauthorized.

### R4: Gap Query API (Portal Backend)

**R4.1** [Event-Driven] **When** an authenticated org admin requests `GET /api/app/gaps`, **then** the portal shall return gap events for the caller's org, ordered by `occurred_at` descending.

**R4.2** [Optional] **Where** query parameters `days` (default: 30, max: 90), `gap_type` (optional: "hard" | "soft"), and `limit` (default: 50, max: 200) are provided, the endpoint shall filter and paginate accordingly.

**R4.3** [Event-Driven] **When** an authenticated org admin requests `GET /api/app/gaps/summary`, **then** the portal shall return aggregated gap counts: `total_7d`, `hard_7d`, `soft_7d`, grouped by distinct `query_text` with occurrence counts.

### R5: KB Stats Extension (Portal Backend)

**R5.1** [Event-Driven] **When** the KB stats endpoint is called, **then** the response shall include `org_gap_count_7d` (int | null) -- the total number of gap events for the org in the last 7 days.

### R6: Gap Dashboard UI (Portal Frontend)

**R6.1** [Event-Driven] **When** an org admin navigates to `/app/gaps`, **then** the portal shall display a gap dashboard showing gap events as a table with columns: query text, gap type (hard/soft badge), nearest KB (populated for soft gaps, "—" for hard gaps), occurrence count, last occurred.

**R6.2** [Ubiquitous] The gap dashboard shall group identical query texts and show a frequency count, sorted by frequency descending (most common unanswered questions first).

**R6.3** [Optional] **Where** the gap dashboard provides date range and gap type filters, the user shall be able to narrow results.

**R6.6** [Event-Driven] **When** the admin clicks the action button on a gap row, **then** the portal shall navigate to the KB editor for the relevant KB (`nearest_kb_slug` for soft gaps, or present a KB picker for hard gaps), with the gap query pre-filled as the page title draft.

**R6.4** [Ubiquitous] The gap dashboard shall use the existing portal UI component library (`Button`, `Card`, `Select`, `Input` from `components/ui/`) and semantic color tokens (`--color-destructive`, `--color-success`, `--color-muted-foreground`).

**R6.5** [Ubiquitous] All user-facing strings in the gap dashboard shall use Paraglide i18n (`import * as m from '@/paraglide/messages'`).

### R7: Knowledge Index Integration (Portal Frontend)

**R7.1** [Event-Driven] **When** an org admin loads the Knowledge index page (`/app/knowledge`), **then** a "Knowledge Gaps" card shall be displayed below the KB list, showing the org-wide gap count for the last 7 days and a link to `/app/gaps`. The card is not visible to non-admin users.

**R7.2** [Ubiquitous] The gap count shown on the card shall be fetched from `GET /api/app/gaps/summary` and displayed as `{total_7d} gaps` with a link arrow. While loading, a skeleton placeholder is shown.

### R8: KB Overview Stats Extension (Portal Frontend)

**R8.1** [Event-Driven] **When** the KB detail overview tab loads, **then** it shall display an additional metric tile showing the org-wide gap count for the last 7 days alongside the existing `docs_count`, `volume`, and `usage_last_30d` tiles.

### R9: Data Retention

**R9.1** [Ubiquitous] Gap event records older than 90 days shall be eligible for deletion.

**R9.2** [Optional] **Where** an automated cleanup mechanism is implemented, it shall run as a periodic background task or database trigger.

---

## Specifications

### S1: Gap Threshold Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `KLAI_GAP_SOFT_THRESHOLD` | `0.4` | Max reranker_score below which all chunks are considered low-confidence |
| `KLAI_GAP_DENSE_THRESHOLD` | `0.35` | Max dense score below which all chunks are considered low-confidence (fallback when reranker unavailable) |

### S2: Gap Event POST Payload

```json
{
  "org_id": 42,
  "user_id": "67890abcdef12345",
  "query_text": "How do I configure SSO for my team?",
  "gap_type": "hard",
  "top_score": null,
  "nearest_kb_slug": null,
  "chunks_retrieved": 0,
  "retrieval_ms": 150
}
```

For a soft gap (chunks returned but low confidence):
```json
{
  "org_id": 42,
  "user_id": "67890abcdef12345",
  "query_text": "What is our notice period?",
  "gap_type": "soft",
  "top_score": 0.22,
  "nearest_kb_slug": "hr-docs",
  "chunks_retrieved": 3,
  "retrieval_ms": 210
}
```

### S3: Gap List Response Schema

```json
{
  "gaps": [
    {
      "query_text": "How do I configure SSO?",
      "gap_type": "hard",
      "top_score": null,
      "nearest_kb_slug": null,
      "occurrence_count": 5,
      "last_occurred": "2026-03-27T10:30:00Z"
    },
    {
      "query_text": "What is our notice period?",
      "gap_type": "soft",
      "top_score": 0.22,
      "nearest_kb_slug": "hr-docs",
      "occurrence_count": 7,
      "last_occurred": "2026-03-27T09:15:00Z"
    }
  ],
  "total": 42
}
```

### S4: Gap Summary Response Schema

```json
{
  "total_7d": 42,
  "hard_7d": 28,
  "soft_7d": 14,
  "top_queries": [
    {
      "query_text": "How do I configure SSO?",
      "gap_type": "hard",
      "count": 5,
      "last_occurred": "2026-03-27T10:30:00Z"
    }
  ]
}
```

### S5: Database Migration

Table: `portal_retrieval_gaps`

```sql
CREATE TABLE portal_retrieval_gaps (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    gap_type TEXT NOT NULL CHECK (gap_type IN ('hard', 'soft')),
    top_score DOUBLE PRECISION,
    nearest_kb_slug TEXT,          -- kb_slug of best-scoring chunk; NULL for hard gaps
    chunks_retrieved INTEGER NOT NULL DEFAULT 0,
    retrieval_ms INTEGER NOT NULL DEFAULT 0,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_retrieval_gaps_org_occurred ON portal_retrieval_gaps (org_id, occurred_at DESC);
CREATE INDEX ix_retrieval_gaps_org_query ON portal_retrieval_gaps (org_id, query_text);
```

### S6: i18n Message Keys

Prefix: `gaps_`

| Key | EN | NL |
|---|---|---|
| `gaps_page_title` | Knowledge Gaps | Kennisleemtes |
| `gaps_index_card_heading` | Knowledge Gaps | Kennisleemtes |
| `gaps_index_card_body` | Questions your knowledge base couldn't answer | Vragen die je kennisbank niet kon beantwoorden |
| `gaps_index_card_link` | View gaps | Bekijk leemtes |
| `gaps_column_query` | Query | Vraag |
| `gaps_column_type` | Type | Type |
| `gaps_column_nearest_kb` | Nearest KB | Dichtstbijzijnde KB |
| `gaps_column_count` | Frequency | Frequentie |
| `gaps_column_last` | Last Occurred | Laatst voorgekomen |
| `gaps_type_hard` | No results | Geen resultaten |
| `gaps_type_soft` | Low confidence | Laag vertrouwen |
| `gaps_action_add` | Add content | Inhoud toevoegen |
| `gaps_action_pick_kb` | Choose KB | Kies kennisbank |
| `gaps_filter_days` | Period | Periode |
| `gaps_filter_type` | Gap type | Leemtetype |
| `gaps_filter_all` | All | Alle |
| `gaps_empty_state` | No knowledge gaps detected in this period. | Geen kennisleemtes gevonden in deze periode. |
| `gaps_overview_tile` | Gaps (7d) | Leemtes (7d) |

---

## Traceability

| Requirement | Plan Milestone | Acceptance Criteria |
|---|---|---|
| R1, R2 (incl. R2.5) | M1 (Hook) | AC-1.x |
| R3, R4, R5 | M2 (Backend) | AC-2.x |
| R6 (incl. R6.6), R7 (incl. R7.2), R8 | M3 (Frontend) | AC-3.x |
| R9 | M2 (Backend) | AC-2.5 |

---

## Implementation Notes

**Completed:** 2026-03-27 | **Commits:** ddadf25, 5b3b9b7 | **Branch:** main

### Scope vs. Original Plan

All requirements implemented as specified. One design decision made during review:

- **R7 navigation placement**: Changed from sidebar nav item (`/app/gaps` in `PRODUCT_ROUTES`) to a "Knowledge Gaps" card on the Knowledge index page (`/app/knowledge`). This keeps the navigation clean for non-admin users and makes the feature discoverable for admins in context.

### Files Created

| File | Purpose |
|---|---|
| `klai-portal/backend/app/api/app_gaps.py` | Gap query and summary endpoints |
| `klai-portal/backend/app/models/retrieval_gaps.py` | SQLAlchemy model for `portal_retrieval_gaps` |
| `klai-portal/backend/alembic/versions/e8f9a0b1c2d3_add_retrieval_gaps_table.py` | Database migration |
| `klai-portal/frontend/src/routes/app/gaps/index.tsx` | Gap dashboard UI |

### Files Modified

| File | Change |
|---|---|
| `deploy/litellm/klai_knowledge.py` | Added `_classify_gap()`, `_fire_gap_event()`, gap detection block |
| `deploy/litellm/tests/test_klai_knowledge_hook.py` | 29 new TDD tests for gap detection |
| `klai-portal/backend/app/api/internal.py` | Added `POST /internal/v1/gap-events` endpoint |
| `klai-portal/backend/app/api/app_knowledge_bases.py` | Extended `KBStatsOut` with `org_gap_count_7d` |
| `klai-portal/backend/app/main.py` | Registered `app_gaps_router` |
| `klai-portal/frontend/messages/en.json` | 18 new `gaps_*` i18n keys |
| `klai-portal/frontend/messages/nl.json` | 18 new `gaps_*` i18n keys (NL translations) |
| `klai-portal/frontend/src/routes/app/knowledge/index.tsx` | Knowledge Gaps card for admins |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` | Gap count metric tile in overview |
| `klai-portal/frontend/src/routeTree.gen.ts` | Regenerated to include `/app/gaps` route |

### Deployment Prerequisites

1. Run `alembic upgrade head` on portal-api before restart (creates `portal_retrieval_gaps` table)
2. Optional env vars for LiteLLM container (safe defaults built in):
   - `KLAI_GAP_SOFT_THRESHOLD=0.4` (reranker score threshold)
   - `KLAI_GAP_DENSE_THRESHOLD=0.35` (dense score fallback threshold)
