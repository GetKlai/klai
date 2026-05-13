---
id: SPEC-INGEST-LOGIN-WALL-DETECT-002
version: "1.0"
status: draft
created: 2026-05-06
updated: 2026-05-06
author: Mark Vletter
priority: high
issue_number: TBD
supersedes:
  - SPEC-INGEST-LOGIN-WALL-DETECT-001 (Phase A detector logic; retains
    Phase B mode flags, Phase C BFS handling, Phase D backfill task
    skeleton, Phase E retrieval-side floor)
related_specs:
  - SPEC-CRAWLER-004 (authenticated cookie-based AuthWallDetected guard;
    complementary, untouched by v2)
  - SPEC-KB-014 (gap detection on retrieval; complementary)
  - SPEC-KB-015 (quality_boost feedback loop; complementary)
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-06 | Mark Vletter | Redesign after 2026-05-06 production canary on voys/support exposed v1's substring-matching design as fundamentally wrong. v1 detected the lexical side-effect of templating; v2 detects the structural cause via near-duplicate clustering. See research.md for the full design journey and rejected alternatives. |

---

# SPEC-INGEST-LOGIN-WALL-DETECT-002: Template-stub detection via near-duplicate clustering

## Context

A login wall is a page where the source CMS serves a templated stub to
anonymous visitors INSTEAD of the article's real content. The
distinguishing structural feature is that the SAME content is served
across many URLs.

`SPEC-INGEST-LOGIN-WALL-DETECT-001` v1 attempted to detect walls via
substring matching of "canonical phrases". This was wrong by abstraction:
phrases are a side-effect of templating, not the cause. Production
validation (2026-05-06 canary on voys/support, 422 pages) found 5 false
positives at 2.6% rate, all on legitimate Dutch and English instructional
content that happens to mention authentication.

**v2 changes the detection mechanism**: instead of matching phrases, the
detector identifies pages whose normalised content fingerprint is
near-identical to N or more other pages in the same `(org_id, kb_slug)`.
This targets the templating CAUSE rather than the phrasing SYMPTOM, is
language-agnostic, and generalises to any CMS.

For full design rationale, alternatives considered, and rejected approaches,
see `research.md`. That document is mandatory reading before modifying
this SPEC.

### What v2 keeps from v1

- `AuthWallSignal` return type (caller-compatible).
- `detect_anonymous_auth_wall(markdown, fit_markdown=None)` function
  signature (caller-compatible).
- `KLAI_INGEST_LOGIN_WALL_DETECT_ENABLED` and
  `KLAI_INGEST_LOGIN_WALL_DETECT_MODE` env flags (reject / degrade /
  audit_only modes unchanged).
- `_ingest_crawl_result` integration point (no caller changes needed).
- BFS continuity in `run_crawl_job` (`AnonymousAuthWallDetected`
  exception, `auth_wall_pages` list, `error_summary` JSONB, `failed_partial`
  status — all unchanged).
- Backfill task `backfill_detect_login_walls(org_id, kb_slug)` and CLI
  entry-point (re-implemented internally to use v2 detector).
- Retrieval-side `quality_floor` filter (untouched, complementary).
- Three-mode rollout (`reject`/`degrade`/`audit_only`).

### What v2 replaces

- The substring matching detector logic (`_match_canonical`,
  `_OBLIGATION_EN`, `_OBLIGATION_NL`).
- Conditions B/C/D (`_match_redirect_density`, `_match_login_link_repetition`,
  `_match_content_login_ratio`) and the `_fp_guard_vetoes` machinery.
- All v1 phrase fixtures in `tests/fixtures/auth_walls/` and the bare-phrase
  test parametrisations.

The v1 module file is replaced wholesale; the public function name is
preserved for caller compatibility.

---

## Scope

### In scope

1. Schema migration: add `content_simhash bigint NULL` column to
   `knowledge.crawled_pages`, with a covering index for
   `(org_id, kb_slug, content_simhash)`.
2. In-tree SimHash implementation in
   `knowledge_ingest/utils/content_fingerprint.py`. ~50 LOC, no external
   dependency. Includes pre-hash text normalisation (URL stripping, anchor
   text extraction, whitespace collapse) so that per-page URL variation
   does not dominate the hash.
3. Replace `knowledge_ingest/utils/auth_wall_detector.py`. New
   implementation:
   - Takes `(markdown, fit_markdown, org_id, kb_slug, conn)` — same
     signature plus DB connection for cluster lookup.
   - Computes the page's SimHash from normalised content.
   - Queries the cluster size: count of pages in same `(org_id, kb_slug)`
     with Hamming distance ≤ 3.
   - Returns `AuthWallSignal` if cluster size ≥ threshold, else `None`.
4. Integration in `_ingest_crawl_result`:
   - Compute SimHash, store in `crawled_pages`, then call detector with
     the connection.
   - Existing mode handling (reject / degrade / audit_only) unchanged.
5. Backfill task v2: re-implement `backfill_detect_login_walls` to:
   - Compute SimHash for any page missing one.
   - For each page, evaluate cluster membership.
   - Delete Qdrant points + mark `__login_wall_purged__` placeholder for
     pages in clusters of size ≥ threshold.
   - Idempotent (placeholder pages are not re-evaluated).
6. Recovery task: `recover_purged_pages(org_id, kb_slug)`. Re-evaluates
   any page with placeholder content_hash under v2 logic. If a page is no
   longer a cluster member (e.g., the whole cluster has been purged or
   v2 disagrees), clear the placeholder so the next crawl re-ingests.
7. Production-data validation script:
   `scripts/validate_login_wall_detector.py --org SLUG --kb SLUG`. Read-only.
   Reports: pages flagged, cluster sizes, samples of flagged URLs. Required
   to run before merge.
8. Test suite refactor:
   - Unit tests for SimHash determinism and Hamming distance behaviour.
   - Cluster-detection tests with synthetic page corpora.
   - Regression fixtures: 5 production FPs (must NOT cluster) + 2
     production walls (must cluster) + 3 synthetic CMS wall corpora.
   - Production-data integration test gated behind a `--prod` flag.
9. Recovery for `/2fa-freedom` (1 production FP page already purged by
   v1): clear its placeholder hash so the next crawl re-ingests it.
10. Update SPEC-001 status to `superseded-by SPEC-INGEST-LOGIN-WALL-DETECT-002`.

### Out of scope (What NOT to Build)

- ML-based classifier (e.g., `soft404` pip library). See research.md §3.3
  for rationale.
- Per-page deterministic multi-feature scorer (link density, brevity,
  redirect density). See research.md §3.2.
- Authenticated re-crawl of `wiki.redcactus.cloud` with cookies. Separate
  effort, tracked under `klai-libs/connector-credentials`.
- LSH banding / locality-sensitive hashing infrastructure. Brute-force
  pairwise scan within a single KB is sub-second at klai's scale (low
  thousands of pages per KB). Banding is a Phase D contingency only if
  the brute-force approach measurably under-performs.
- Cross-KB or cross-tenant cluster detection. Walls cluster within a
  source CMS; clustering across KBs would conflate unrelated tenants
  and is a tenant-isolation hazard.
- Trafilatura integration. crawl4ai's `fit_markdown` already provides
  rule-based content extraction.
- Modifying the `quality_floor` retrieval-side filter (Phase E of v1).
  It remains as a defence-in-depth layer, untouched.
- Modifying the `audit_only` / `degrade` / `reject` rollout flags. The
  three-mode operational model carries over unchanged.

---

## Requirements (EARS)

### REQ-INGEST-LOGIN-WALL-DETECT-002-01 — Content fingerprint at ingest

The system SHALL compute a 64-bit SimHash of every successfully-ingested
page's normalised content and store it in
`knowledge.crawled_pages.content_simhash`.

Normalisation steps before hashing (in order):
1. Replace every URL (`https?://[^\s)]+`) with the literal token `<URL>`.
2. For every markdown anchor `[text](url)`, replace with the bare anchor
   text (drop the URL).
3. Lowercase the result.
4. Collapse runs of whitespace to a single space.
5. Tokenise on word boundaries (`\b[\w]+\b`).

The fingerprint MUST be deterministic: identical normalised input
produces identical hashes across runs.

Hash storage: `bigint` column. SimHash values fitting in 64-bit signed
range MUST be stored as-is (not converted to unsigned).

### REQ-INGEST-LOGIN-WALL-DETECT-002-02 — Cluster-based wall detection

The system SHALL classify a page as a login-wall stub when N or more
OTHER pages in the same `(org_id, kb_slug)` have a SimHash within Hamming
distance 3 of the page's own SimHash, where N is the configured cluster
threshold.

Default N is 5. The threshold is configurable via env var
`KLAI_INGEST_TEMPLATE_CLUSTER_MIN`.

Hamming distance threshold of 3 is fixed in v2 and not user-configurable
(adjusting it requires re-validation against fixtures and is a SPEC
revision, not an env tweak).

### REQ-INGEST-LOGIN-WALL-DETECT-002-03 — Cold-start permissiveness

When fewer than `(N + 1)` pages exist in `(org_id, kb_slug)`, the
detector SHALL classify all pages as not-walls (return `None`).

Rationale: single or few-page walls do not pollute retrieval. Premature
classification at cold-start risks suppressing legitimate sparse content.

### REQ-INGEST-LOGIN-WALL-DETECT-002-04 — Backfill replay

The Procrastinate task `backfill_detect_login_walls(org_id, kb_slug)`
SHALL:

1. For every page in `(org_id, kb_slug)` whose `content_simhash` is NULL,
   compute and store the SimHash.
2. For every page (excluding those whose `content_hash` already equals
   `__login_wall_purged__`), evaluate cluster membership under
   REQ-INGEST-LOGIN-WALL-DETECT-002-02.
3. For every page classified as a wall:
   a. Delete Qdrant points filtered by
      `org_id + kb_slug + path` (REQ-INGEST-LOGIN-WALL-DETECT-002-09
      tenant isolation).
   b. Set the page's `content_hash` to `__login_wall_purged__`.
4. Return `{"processed": N, "flagged": M, "qdrant_deleted": K}`.

The task MUST be idempotent: running it twice on the same tenant yields
the same final database state.

### REQ-INGEST-LOGIN-WALL-DETECT-002-05 — Recovery of v1-purged FPs

A new operator-triggered task `recover_purged_pages(org_id, kb_slug)`
SHALL:

1. Find all pages where `content_hash = '__login_wall_purged__'` AND a
   non-NULL `content_simhash` exists.
2. Re-evaluate each under v2 cluster logic.
3. For pages NOT classified as walls under v2, clear `content_hash` to
   the empty string (forces re-ingest at next crawl).
4. Return `{"processed": N, "recovered": M}`.

Designed for one-shot operator use immediately after v2 deploys, to undo
v1 FPs.

### REQ-INGEST-LOGIN-WALL-DETECT-002-06 — Caller signature stability

The public function in
`klai-knowledge-ingest/knowledge_ingest/utils/auth_wall_detector.py`
SHALL retain the signature:

```python
def detect_anonymous_auth_wall(
    markdown: str,
    *,
    fit_markdown: str | None = None,
    url: str | None = None,
) -> AuthWallSignal | None
```

Callers SHALL NOT need modification. The v2 implementation extends with
keyword-only DB-access parameters (`org_id`, `kb_slug`, `conn`) for
cluster lookup, defaulting to None for backwards-compat. When DB access
parameters are absent, the detector returns `None` — fail-open with a
single WARNING log line.

### REQ-INGEST-LOGIN-WALL-DETECT-002-07 — Mode handling preserved

The three operating modes from
`SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-05` (reject / degrade / audit_only)
SHALL apply unchanged in v2. The detector's return value semantics are
identical (`AuthWallSignal | None`); modes determine post-detection
behaviour (raise exception / continue with `quality_score=0` / log only).

The `pattern` field on `AuthWallSignal` SHALL be `"template_cluster"` in
v2 (was `"canonical_phrase_en/nl"` in v1). The `evidence` tuple SHALL
contain a single string of the form
`"cluster_size={N} hamming_threshold=3"`.

### REQ-INGEST-LOGIN-WALL-DETECT-002-08 — Performance

SimHash compute on a 100 KB markdown input SHALL complete in p99 < 5 ms.

Cluster query within a single KB of N pages SHALL complete in p99 < 50 ms
for N ≤ 1000 (which covers all current klai tenants by orders of
magnitude). Implementation may use brute-force scan; LSH banding is not
required at this scale and is deferred to a follow-up SPEC if scale
demands it.

### REQ-INGEST-LOGIN-WALL-DETECT-002-09 — Tenant isolation

All v2 database operations SHALL respect tenant scope:

- SimHash store/retrieve: SQL filtered by `org_id` AND `kb_slug`.
- Cluster query: SQL filtered by `org_id` AND `kb_slug`. Cross-tenant
  cluster lookup is FORBIDDEN.
- Qdrant deletes (in backfill / recovery): `Filter.must` MUST include
  `org_id`, `kb_slug`, and `path` field conditions, identical to v1's
  REQ-09. The semgrep rule in
  `.github/workflows/tenant-isolation-review.yml` MUST continue to pass.

### REQ-INGEST-LOGIN-WALL-DETECT-002-10 — Production-data validation gate

A read-only validation script
`scripts/validate_login_wall_detector.py --org SLUG --kb SLUG` SHALL
exist and SHALL be runnable against any production tenant.

The script reports:
- Total pages scanned.
- SimHash clusters discovered (size ≥ N).
- Sample URLs from each cluster (up to 10 per cluster).
- Any page currently flagged as wall whose cluster size dropped below N
  under v2 (potential v1 FP that should be recovered).

Merge gate: the script MUST report 0 unexpected wall classifications on
voys + getklai before v2 ships to production.

---

## Non-functional requirements

### Performance

- SimHash compute p99 < 5 ms on 100 KB markdown.
- Cluster query p99 < 50 ms within a 1000-page KB.
- Backfill throughput: ≥ 50 pages/second (limited by Qdrant delete API
  for flagged pages).
- Detector adds ≤ 100 ms to per-page ingest latency.

### Security

- Tenant isolation per REQ-09 (no cross-tenant data flow).
- No new authentication paths introduced.

### Reliability

- Detector failure (e.g., DB connection error during cluster lookup) MUST
  fail-safe: log a warning, return `None`, allow ingest to proceed
  unflagged. A detector outage MUST NOT block ingest.
- Backfill task MUST be re-runnable. Partial failure MUST leave the
  remaining pages for the next run.
- Schema migration MUST be additive (new column NULL-able, indexed; no
  destructive changes).

### Observability

- Prometheus counter `klai_ingest_login_wall_detected_total{org_id, kb_slug, mode}`
  inherited from v1 Phase F SHALL increment under v2 logic.
- A new gauge `klai_ingest_template_cluster_size{org_id, kb_slug}`
  SHALL track the largest cluster per KB for operator visibility.
- Detector log entries SHALL include the cluster size in their `evidence`
  field for diagnostic traceability.

---

## Success criteria

1. v2 detector classifies all 149 voys/support RedCactus walls correctly
   (cluster size ≥ 5 under SimHash + Hamming ≤ 3).
2. v2 detector classifies all 5 captured production FP fixtures correctly
   as NOT walls (`/2fa-freedom`, account-toegang, IFTTT, zoom_setup,
   auth_documentation_tutorial).
3. Production validation script reports 0 surprise classifications on
   voys + getklai.
4. SPEC-INGEST-LOGIN-WALL-DETECT-001 status updated to `superseded`.
5. PR #432 (NL phrase tightening) reverted in spirit by v2 deletion of
   the phrase list. The fix in PR #432 remains in main as a no-op (no
   v2 code path uses phrases) — no need for a revert PR.
6. Recovery task run on getklai/voys-test successfully un-purges
   `/2fa-freedom`. After the next scheduled crawl, the page returns to
   the KB and is correctly NOT classified as a wall.
7. The original failing chat query *"Hoe stel ik RedCactus in met
   HubSpot?"* in voys's chat continues to honestly say "I cannot find
   this in the knowledge base" (because the redcactus walls remain
   purged) AND the recovered tutorial content (`/2fa-freedom`,
   `/account-toegang`) returns to retrieval results.

---

## Migration plan summary

Detailed phasing in `plan.md`. Operationally, v2 ships in a single PR:

1. Schema migration applied at container restart (knowledge-ingest
   auto-migrates).
2. New detector code replaces old in same file.
3. Backfill task v2 invocation: `python -m
   knowledge_ingest.backfill_tasks --org voys --kb support` recomputes
   under v2.
4. Recovery task invocation: `python -m
   knowledge_ingest.backfill_tasks --org getklai --kb voys-test
   --recover` un-purges `/2fa-freedom`.
5. Production validation script confirms zero surprises.
6. Mark SPEC-001 as superseded.

No customer-facing API changes. No portal-frontend changes. No portal-api
changes. No retrieval-api changes (the quality_floor remains unchanged).
