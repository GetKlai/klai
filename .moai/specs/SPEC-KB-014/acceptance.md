# SPEC-KB-014: Acceptance Criteria

```yaml
spec_id: SPEC-KB-014
document: acceptance
```

---

## AC-1: LiteLLM Hook Gap Detection (M1)

### AC-1.1: Hard Gap Classification

**Given** a user sends a chat message with a non-trivial query
**And** the user has knowledge product entitlement
**And** retrieval-api returns an empty `chunks` array
**And** `retrieval_bypassed` is false
**When** the hook processes the retrieval result
**Then** the hook shall classify the result as `hard_gap`
**And** the hook shall fire an async POST to `/internal/v1/gap-events` with `gap_type: "hard"`, `top_score: null`, `chunks_retrieved: 0`

### AC-1.2: Soft Gap Classification (Reranker Score)

**Given** a user sends a chat message with a non-trivial query
**And** retrieval-api returns 3 chunks with reranker scores [0.25, 0.18, 0.12]
**And** `KLAI_GAP_SOFT_THRESHOLD` is set to 0.4 (default)
**When** the hook processes the retrieval result
**Then** the hook shall classify the result as `soft_gap`
**And** the gap event payload shall contain `top_score: 0.25` and `chunks_retrieved: 3`

### AC-1.3: Success Classification (No Gap)

**Given** a user sends a chat message with a non-trivial query
**And** retrieval-api returns 3 chunks with reranker scores [0.85, 0.72, 0.41]
**When** the hook processes the retrieval result
**Then** the hook shall classify the result as `success`
**And** no gap event POST shall be fired

### AC-1.4: Soft Gap with Dense Score Fallback

**Given** a user sends a chat message with a non-trivial query
**And** retrieval-api returns 2 chunks with no `reranker_score` fields
**And** the chunks have `score` values [0.30, 0.22]
**And** `KLAI_GAP_DENSE_THRESHOLD` is set to 0.35 (default)
**When** the hook processes the retrieval result
**Then** the hook shall classify the result as `soft_gap` based on dense scores

### AC-1.5: Fire-and-Forget Resilience

**Given** a gap is detected
**And** the portal-api `/internal/v1/gap-events` endpoint is unreachable (connection refused)
**When** the hook attempts to POST the gap event
**Then** the hook shall log a warning
**And** the hook shall return the `data` dict to LiteLLM without error
**And** the chat response shall not be delayed or affected

### AC-1.6: No Gap Event Without User Context

**Given** a chat request where `user_id` is empty or `org_id` is not available
**When** the hook processes the request
**Then** no gap detection or gap event POST shall occur

### AC-1.7: Gate-Bypassed Requests

**Given** a retrieval result where `retrieval_bypassed` is true
**And** `chunks` is empty
**When** the hook processes the result
**Then** the hook shall NOT classify this as a gap (the gate intentionally skipped retrieval)

### AC-1.8: Custom Threshold Override

**Given** `KLAI_GAP_SOFT_THRESHOLD` is set to `0.6` in the environment
**And** retrieval-api returns chunks with reranker scores [0.55, 0.42]
**When** the hook processes the retrieval result
**Then** the hook shall classify the result as `soft_gap` (all below 0.6)

---

## AC-2: Portal Backend (M2)

### AC-2.1: Internal Gap Event Endpoint -- Success

**Given** a valid internal token in the Authorization header
**And** a valid gap event JSON payload with all required fields
**When** `POST /internal/v1/gap-events` is called
**Then** a row shall be inserted into `portal_retrieval_gaps`
**And** the response status shall be 201 Created

### AC-2.2: Internal Gap Event Endpoint -- Authentication

**Given** a request to `POST /internal/v1/gap-events` without a valid internal token
**When** the endpoint processes the request
**Then** the response status shall be 401 Unauthorized
**And** no row shall be inserted

### AC-2.3: Internal Gap Event Endpoint -- Validation

**Given** a valid internal token
**And** a payload with `gap_type: "unknown"` (invalid value)
**When** `POST /internal/v1/gap-events` is called
**Then** the response status shall be 422 Unprocessable Entity

### AC-2.4: Gap List API -- Filtering

**Given** an authenticated org admin
**And** the org has 10 hard gaps and 5 soft gaps in the last 7 days
**And** the org has 20 hard gaps older than 7 days but within 30 days
**When** `GET /api/app/gaps?days=7&gap_type=hard` is called
**Then** the response shall contain exactly 10 gap entries
**And** all entries shall have `gap_type: "hard"`

### AC-2.5: Gap List API -- Authorization

**Given** an authenticated user who is NOT an org admin
**When** `GET /api/app/gaps` is called
**Then** the response status shall be 403 Forbidden

### AC-2.6: Gap Summary API

**Given** an authenticated org admin
**And** the org has gaps in the last 7 days: 5x "How to configure SSO?" (hard), 3x "Reset password procedure" (soft), 2x "What is our leave policy?" (hard)
**When** `GET /api/app/gaps/summary` is called
**Then** `total_7d` shall be 10
**And** `hard_7d` shall be 7
**And** `soft_7d` shall be 3
**And** `top_queries` shall be ordered by count descending: "How to configure SSO?" (5), "Reset password procedure" (3), "What is our leave policy?" (2)

### AC-2.7: KB Stats Extension

**Given** an org with 8 gap events in the last 7 days
**When** the KB stats endpoint is called for any KB in that org
**Then** the response shall include `org_gap_count_7d: 8`

### AC-2.8: Data Retention Boundary

**Given** gap records older than 90 days exist in `portal_retrieval_gaps`
**When** the retention policy is applied (manually or via scheduled task)
**Then** records older than 90 days shall be deleted
**And** records within 90 days shall be preserved

---

## AC-3: Portal Frontend (M3)

### AC-3.1: Gap Dashboard Page Renders

**Given** an authenticated org admin with `knowledge` product entitlement
**When** the user navigates to `/app/gaps`
**Then** the page shall display a heading "Knowledge Gaps" (or translated equivalent)
**And** a table with columns: Query, Type, Top Score, Frequency, Last Occurred
**And** filter controls for period (7d, 14d, 30d) and gap type (all, hard, soft)

### AC-3.2: Gap Type Badges

**Given** the gap dashboard is displayed
**When** the table contains both hard and soft gaps
**Then** hard gaps shall display a badge with destructive styling (using `--color-destructive` token)
**And** soft gaps shall display a badge with muted/warning styling

### AC-3.3: Empty State

**Given** an authenticated org admin
**And** the org has no gap events in the selected period
**When** the gap dashboard loads
**Then** the page shall display the empty state message: "No knowledge gaps detected in this period."

### AC-3.4: Sidebar Navigation

**Given** the user has the `knowledge` product entitlement
**When** the app sidebar renders
**Then** a "Knowledge Gaps" item shall be visible with the `SearchX` icon
**And** clicking it shall navigate to `/app/gaps`

### AC-3.5: Sidebar Navigation -- Hidden Without Entitlement

**Given** the user does NOT have the `knowledge` product entitlement
**When** the app sidebar renders
**Then** the "Knowledge Gaps" navigation item shall NOT be visible

### AC-3.6: KB Overview Gap Tile

**Given** the KB detail page overview tab is displayed
**And** the org has 12 gap events in the last 7 days
**When** the stats load
**Then** a metric tile labeled "Gaps (7d)" (or translated "Leemtes (7d)") shall display the value 12

### AC-3.7: i18n Completeness

**Given** the gap dashboard and KB overview gap tile
**When** the locale is set to NL
**Then** all visible text shall display Dutch translations
**And** when the locale is set to EN, all visible text shall display English translations
**And** no hardcoded strings shall appear

### AC-3.8: UI Component Compliance

**Given** the gap dashboard page
**Then** all form controls shall use components from `components/ui/` (Button, Select, Card)
**And** no raw `<button>`, `<select>`, or `<input>` elements with inline Tailwind shall be present
**And** semantic colors shall use CSS variable tokens, not raw Tailwind color classes

---

## Quality Gate Criteria

| Gate | Requirement |
|---|---|
| Hook unit tests | Gap classification tests pass for all edge cases (hard, soft, success, missing scores, gate bypass) |
| Backend API tests | Internal endpoint auth + validation tests pass; app endpoint auth + filtering tests pass |
| Frontend build | `npm run build` succeeds with no TypeScript errors |
| i18n completeness | All `gaps_*` keys present in both `en.json` and `nl.json` |
| No raw colors | Grep for `text-red-`, `bg-red-`, `text-green-`, `bg-green-` in `gaps.tsx` returns zero matches |

---

## Definition of Done

- [ ] Hook classifies hard gaps, soft gaps, and successes correctly
- [ ] Hook fires gap event POST asynchronously without blocking
- [ ] Hook handles POST failure gracefully (warning log, no crash)
- [ ] `portal_retrieval_gaps` table created via Alembic migration
- [ ] `POST /internal/v1/gap-events` endpoint works with internal token auth
- [ ] `GET /api/app/gaps` returns filtered gap list for org admins
- [ ] `GET /api/app/gaps/summary` returns aggregated gap counts
- [ ] KB stats include `org_gap_count_7d`
- [ ] `/app/gaps` route renders gap dashboard with table and filters
- [ ] Sidebar includes "Knowledge Gaps" item (gated by knowledge product)
- [ ] KB overview tab shows gap count tile
- [ ] All strings use Paraglide i18n (EN + NL)
- [ ] All UI uses `components/ui/` and semantic color tokens
