# Changelog

## [Unreleased] — 2026-03-27

### Added — SPEC-KB-014: Knowledge Gap Detection & UI

- **LiteLLM hook**: Gap detection in `klai_knowledge.py` — classifies retrieval results as `hard_gap` (zero chunks), `soft_gap` (all chunk scores below threshold), or `success`. Fire-and-forget async reporting via `asyncio.create_task()`.
- **Internal API**: `POST /internal/v1/gap-events` endpoint for service-to-service gap event ingestion (authenticated via `PORTAL_INTERNAL_SECRET`).
- **Database**: New `portal_retrieval_gaps` table with FK to `portal_orgs`, composite indexes on `(org_id, occurred_at)` and `(org_id, query_text)`. 90-day retention policy.
- **Gap API**: `GET /api/app/gaps` (list with filters: days, gap_type, limit) and `GET /api/app/gaps/summary` (aggregated counts: total_7d, hard_7d, soft_7d) — admin-only.
- **KB stats**: Extended `GET /api/app/knowledge-bases/{slug}/stats` to include `org_gap_count_7d`.
- **Gap dashboard** (`/app/gaps`): Table of unanswered questions grouped by query text, with type badge (hard/soft), nearest KB, frequency, and action buttons (navigate to KB or knowledge index). Product-gated to `knowledge` entitlement.
- **Knowledge index card**: Admins see a "Knowledge Gaps" card on `/app/knowledge` with the 7-day gap count and a link to the dashboard.
- **KB detail tile**: "Gaps (7d)" metric tile added to the KB overview tab alongside existing stats.
- **i18n**: 18 new `gaps_*` keys in EN and NL.

### Configuration — SPEC-KB-014

New optional environment variables for the LiteLLM container (safe defaults built in):
- `KLAI_GAP_SOFT_THRESHOLD` (default: `0.4`) — reranker score below which all chunks are classified as low-confidence
- `KLAI_GAP_DENSE_THRESHOLD` (default: `0.35`) — dense score fallback threshold

**Migration required:** Run `alembic upgrade head` on portal-api before restart to create the `portal_retrieval_gaps` table.
