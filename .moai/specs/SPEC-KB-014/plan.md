# SPEC-KB-014: Implementation Plan

```yaml
spec_id: SPEC-KB-014
document: plan
```

---

## Overview

Knowledge Gap Detection adds three layers to the existing knowledge retrieval pipeline:

1. **Detection** (LiteLLM hook) -- classify retrieval results and fire gap events
2. **Storage & API** (portal backend) -- persist gap events and expose query endpoints
3. **Visibility** (portal frontend) -- gap dashboard page and KB overview integration

---

## Module Decomposition

### Module 1: LiteLLM Hook Gap Detection

**Scope:** `deploy/litellm/klai_knowledge.py` + `deploy/litellm/tests/test_klai_knowledge_hook.py`

**Technical Approach:**

1. Add gap classification logic after retrieval response parsing (line ~180 in current hook).
2. After `result = resp.json()`, before the existing `if not chunks:` early return:
   - Extract `reranker_score` and `score` from each chunk.
   - Classify: `hard_gap` if `chunks == []` and not `retrieval_bypassed`. `soft_gap` if all chunks have max score below threshold.
   - For soft gaps: extract `nearest_kb_slug` from the chunk with the highest score (chunks have `kb_slug` in their payload). For hard gaps: `nearest_kb_slug = None`.
3. On gap detection, fire `asyncio.create_task()` with a POST to portal internal API. Wrap in try/except to guarantee fire-and-forget (no crash on failure).
4. Add gap metadata to `data["_klai_kb_meta"]` for downstream logging (extend existing dict with `gap_type` field).
5. Read thresholds from env vars with float defaults.

**`nearest_kb_slug` extraction logic:**
```python
def _get_nearest_kb_slug(chunks: list[dict]) -> str | None:
    if not chunks:
        return None
    # Pick chunk with highest reranker_score, fallback to dense score
    best = max(
        chunks,
        key=lambda c: c.get("reranker_score") or c.get("score") or 0.0
    )
    return best.get("kb_slug")
```

**Key Design Decision:** Use `asyncio.create_task()` for the POST instead of `await` -- this ensures the hook returns immediately to LiteLLM without waiting for portal-api to acknowledge the gap event. The task runs in the background on the event loop.

**Files Changed:**

| File | Change |
|---|---|
| `deploy/litellm/klai_knowledge.py` | Add `_classify_gap()`, `_report_gap()`, env var constants, gap detection in `async_pre_call_hook`, extend `_klai_kb_meta` |
| `deploy/litellm/tests/test_klai_knowledge_hook.py` | Test gap classification logic, test fire-and-forget POST behavior |

**Dependencies:** None (extends existing hook, no new packages).

---

### Module 2: Portal Backend -- Model, Migration, API

**Scope:** Portal backend (models, migration, internal API, app API, stats extension)

**Technical Approach:**

1. **Model** (`klai-portal/backend/app/models/knowledge_bases.py` or new `klai-portal/backend/app/models/retrieval_gaps.py`):
   - Add `PortalRetrievalGap` SQLAlchemy model.
   - Columns: `id`, `org_id` (FK), `user_id`, `query_text`, `gap_type`, `top_score`, `chunks_retrieved`, `retrieval_ms`, `occurred_at`.
   - Indexes: `(org_id, occurred_at DESC)`, `(org_id, query_text)`.

2. **Migration** (`klai-portal/backend/alembic/versions/{hash}_add_retrieval_gaps.py`):
   - Standard Alembic migration with `op.create_table()` and indexes.

3. **Internal endpoint** (`klai-portal/backend/app/api/internal.py`):
   - Add `POST /internal/v1/gap-events` endpoint.
   - Uses existing `_require_internal_token()` guard.
   - Validates payload with Pydantic schema, inserts row, returns 201.
   - No org membership check needed -- the hook already verified the user has knowledge entitlement.

4. **App API** (new file: `klai-portal/backend/app/api/app_gaps.py` or extend `klai-portal/backend/app/api/app_knowledge.py`):
   - `GET /api/app/gaps` -- list gap events for caller's org, with `days`, `gap_type`, `limit` query params.
   - `GET /api/app/gaps/summary` -- aggregated counts + top query grouping.
   - Auth: require authenticated user with admin role in org (same pattern as KB management).

5. **Stats extension** (wherever the KB stats endpoint lives):
   - Add `org_gap_count_7d` to the stats response by counting `portal_retrieval_gaps` rows for the org in the last 7 days.

**Files Changed:**

| File | Change |
|---|---|
| `klai-portal/backend/app/models/retrieval_gaps.py` | New file: `PortalRetrievalGap` model (incl. `nearest_kb_slug` column) |
| `klai-portal/backend/alembic/versions/xxxx_add_retrieval_gaps.py` | New file: Alembic migration |
| `klai-portal/backend/app/api/internal.py` | Add `POST /internal/v1/gap-events` endpoint |
| `klai-portal/backend/app/api/app_gaps.py` | New file: `GET /api/app/gaps`, `GET /api/app/gaps/summary` |
| `klai-portal/backend/app/api/knowledge_bases.py` | Extend KB stats with `org_gap_count_7d` (or whichever router serves stats) |
| `klai-portal/backend/app/main.py` | Register new `app_gaps` router |

**Dependencies:** None (uses existing SQLAlchemy, FastAPI, Pydantic stack).

---

### Module 3: Portal Frontend -- Gap Dashboard & Stats

**Scope:** Portal frontend (new route, sidebar nav, i18n, KB overview extension)

**Technical Approach:**

1. **New route** (`klai-portal/frontend/src/routes/app/gaps.tsx`):
   - TanStack Router file-based route at `/app/gaps`.
   - Uses `useQuery` for `GET /api/app/gaps` with inline `queryFn` (per separation-of-concerns pattern).
   - Table component using `Card` with data table pattern from `docs/ui-components.md`.
   - Columns: query text (truncated), gap type badge (hard = destructive color, soft = warning/muted), nearest KB (link to KB for soft gaps, "—" for hard gaps), frequency count, last occurred (relative time).
   - Action per row: "Inhoud toevoegen" button → for soft gaps navigates to `nearest_kb_slug` KB editor with query as page title draft; for hard gaps opens a KB picker modal first.
   - Filter controls: `Select` for days (7, 14, 30), `Select` for gap type (all, hard, soft).
   - Empty state message when no gaps found.

2. **Navigation** (`klai-portal/frontend/src/routes/app/route.tsx`):
   - Add `{ to: '/app/gaps', label: m.gaps_nav_label(), icon: SearchX }` to `allNavItems`.
   - Gate behind `knowledge` product (add `/app/gaps` to `PRODUCT_ROUTES` mapping).

3. **i18n** (`klai-portal/frontend/messages/en.json`, `klai-portal/frontend/messages/nl.json`):
   - Add all `gaps_*` message keys per S6 in spec.md.

4. **KB overview extension** (`klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx`):
   - Extend `KBStats` interface with `org_gap_count_7d: number | null`.
   - Add metric tile in overview tab alongside existing tiles.

**Files Changed:**

| File | Change |
|---|---|
| `klai-portal/frontend/src/routes/app/gaps.tsx` | New file: gap dashboard page |
| `klai-portal/frontend/src/routes/app/route.tsx` | Add nav item and product route mapping |
| `klai-portal/frontend/messages/en.json` | Add `gaps_*` message keys |
| `klai-portal/frontend/messages/nl.json` | Add `gaps_*` message keys (NL translations) |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` | Extend `KBStats` interface, add gap tile |

**Dependencies:** `lucide-react` `SearchX` icon (already available in lucide-react).

---

## Implementation Order

### Primary Goal: M1 -- Hook Gap Detection

**Why first:** Without gap detection, there is no data to display. This is the data source.

**Deliverable:** Modified `klai_knowledge.py` that classifies gaps and fires events to portal-api.

**Verification:** Unit tests in `test_klai_knowledge_hook.py` covering hard gap, soft gap, and success classification.

### Primary Goal: M2 -- Backend Storage & API

**Why second:** The internal endpoint must exist before the hook can POST to it. The app API must exist before the frontend can display data.

**Deliverable:** New model, migration, internal POST endpoint, app GET endpoints, stats extension.

**Verification:** API tests for internal endpoint (auth, validation, insertion) and app endpoints (auth, filtering, aggregation).

### Secondary Goal: M3 -- Frontend Dashboard & Stats

**Why third:** Depends on M2 API endpoints being available.

**Deliverable:** Gap dashboard page, sidebar navigation, i18n keys, KB overview tile.

**Verification:** Visual verification via Playwright MCP, i18n key completeness check.

---

## Architecture Design Direction

```
User Query (LibreChat)
        |
        v
LiteLLM Hook (klai_knowledge.py)
        |
        +-- retrieval-api --> chunks + scores
        |
        +-- classify: success | hard_gap | soft_gap
        |
        +-- if gap: asyncio.create_task(POST /internal/v1/gap-events)
        |
        v
Portal API (internal.py)
        |
        +-- INSERT portal_retrieval_gaps
        |
        v
Portal API (app_gaps.py)
        |
        +-- GET /api/app/gaps         --> gap list
        +-- GET /api/app/gaps/summary  --> aggregated counts
        |
        v
Portal Frontend (gaps.tsx)
        |
        +-- Gap dashboard table
        +-- Date/type filters
        +-- KB overview tile
```

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| High gap event volume overwhelms PostgreSQL | Low | Medium | 90-day retention cleanup + index on (org_id, occurred_at). At current scale (<1000/day/org), PostgreSQL handles this trivially. |
| Hook POST latency affects chat response time | Low | High | Fire-and-forget via `asyncio.create_task()` -- hook returns immediately regardless of POST outcome. |
| Gap thresholds produce too many false positives (soft gaps) | Medium | Low | Configurable via env vars. Start conservative (0.4 reranker threshold) and tune based on admin feedback. |
| Privacy concerns about storing query text | Low | Medium | Only org admins can view gaps. Document in privacy policy. 90-day auto-deletion reduces exposure window. |
| Retrieval-api changes score format | Low | Medium | Gap classification checks for score field existence before using it. Falls back to chunk count only. |

---

## Expert Consultation Recommendations

- **Backend expert:** Recommended for M2 (database schema design, API pagination patterns, Alembic migration best practices).
- **Frontend expert:** Recommended for M3 (gap dashboard component design, table patterns, i18n integration).
- **DevOps expert:** Not required -- no new infrastructure, just extending existing services.
