# Implementation Plan — SPEC-INGEST-LOGIN-WALL-DETECT-002

Phased rollout that minimises operational risk: schema first, detector
swap second, backfill third, recovery fourth, validation gate fifth. Each
phase is independently committable; the v2 detector becomes active only
when both schema and code are deployed.

Methodology: TDD per project convention. Each phase has failing tests
first, then implementation, then validation against captured production
fixtures.

Worktree: created at `feature/SPEC-INGEST-LOGIN-WALL-DETECT-002` before
any edits (multi-file change, > 5 tool calls).

---

## Phase A — Schema migration

**Files**:
- `klai-knowledge-ingest/alembic/versions/XXXX_crawled_pages_simhash.py` (new)

**Migration**:
```sql
ALTER TABLE knowledge.crawled_pages
ADD COLUMN IF NOT EXISTS content_simhash bigint;

CREATE INDEX IF NOT EXISTS idx_crawled_pages_simhash_org_kb
ON knowledge.crawled_pages (org_id, kb_slug, content_simhash)
WHERE content_simhash IS NOT NULL;
```

The partial index (only non-NULL rows) keeps cold-start cost low: rows
without a SimHash yet (pre-Phase B page ingests) are not indexed.

Idempotent: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`.

Auto-applied: knowledge-ingest's entrypoint runs `alembic upgrade head`.

Exit criteria: migration applies cleanly on a fresh DB and on the
existing prod DB (verified via local pg_dump replay).

---

## Phase B — SimHash module + replacement detector

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/utils/content_fingerprint.py` (new)
  - `compute_simhash(text: str) -> int`
  - `hamming_distance(a: int, b: int) -> int`
  - `_normalise(text: str) -> str` (private)
- `klai-knowledge-ingest/knowledge_ingest/utils/auth_wall_detector.py` (rewrite)
  - `AuthWallSignal` dataclass (kept; pattern field becomes
    `"template_cluster"`)
  - `detect_anonymous_auth_wall(...)` — new implementation using SimHash
    cluster lookup
- `klai-knowledge-ingest/tests/test_content_fingerprint.py` (new)
- `klai-knowledge-ingest/tests/test_auth_wall_detector.py` (rewrite)

**Steps**:
1. Implement SimHash in-tree (~50 LOC). 64-bit hash, weighted feature
   vector based on word tokens. Pre-hash normalisation: URL → `<URL>`,
   anchor `[text](url)` → `text`, lowercase, whitespace collapse,
   word-boundary tokenisation.
2. Implement `hamming_distance` using `(a ^ b).bit_count()` (Python 3.10+).
3. Tests for SimHash determinism + sensitivity:
   - Identical input → identical hash.
   - Two pages differing only in their canonical URL → Hamming ≤ 3
     (validates the URL normalisation).
   - Two unrelated pages → Hamming » 10.
4. Rewrite detector. Function:
   ```python
   async def detect_anonymous_auth_wall(
       markdown: str,
       *,
       fit_markdown: str | None = None,
       url: str | None = None,
       org_id: str | None = None,
       kb_slug: str | None = None,
       conn: asyncpg.Connection | None = None,
   ) -> AuthWallSignal | None
   ```
   - If org_id/kb_slug/conn missing: log WARN, return None (fail-open).
   - Compute SimHash of `fit_markdown or markdown`.
   - Query: `SELECT content_simhash FROM knowledge.crawled_pages WHERE
     org_id = $1 AND kb_slug = $2 AND content_simhash IS NOT NULL`.
   - Count rows with `hamming_distance(this, that) <= 3`.
   - If count >= `KLAI_INGEST_TEMPLATE_CLUSTER_MIN` (default 5): return
     AuthWallSignal(pattern="template_cluster",
                    evidence=(f"cluster_size={count} hamming<=3",),
                    confidence=0.9).
   - Else: return None.
5. Delete v1 detector code + fixtures + tests.

Exit criteria: All new unit tests pass. Cluster simulation tests pass on
synthetic corpora. `_ingest_crawl_result` integration is wired (Phase C).

---

## Phase C — Ingest integration + SimHash storage

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` (modify)
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py` (modify — new helper
  `update_crawled_page_simhash(conn, org_id, kb_slug, url, simhash)`)
- `klai-knowledge-ingest/tests/test_crawler_template_detection.py` (new
  integration test)

**Steps**:
1. Modify `_ingest_crawl_result`:
   - Compute SimHash from `result.fit_markdown or result.raw_markdown`
     (using v2's normalisation).
   - Pass org_id/kb_slug/conn to `detect_anonymous_auth_wall`.
   - Store SimHash via `pg_store.update_crawled_page_simhash` AFTER the
     dedup checks pass (we only store hashes for pages that actually get
     ingested, to avoid hashing skipped duplicates).
2. The detector call now requires DB access — adjust integration test
   mocks to provide a fake connection that returns a configurable list
   of nearby SimHashes.
3. Mode handling (reject / degrade / audit_only) is unchanged from v1.
   Rerun all v1 mode integration tests with the v2 detector mock.

Exit criteria: All integration tests pass. Reject mode raises
`AnonymousAuthWallDetected` when cluster ≥ N. Degrade mode passes through
`quality_score=0.0`. Audit_only logs and continues.

---

## Phase D — Backfill task v2

**Files**:
- `klai-knowledge-ingest/knowledge_ingest/backfill_tasks.py` (rewrite
  `backfill_detect_login_walls`; add `recover_purged_pages`)
- `klai-knowledge-ingest/tests/test_backfill_login_walls.py` (rewrite)

**Steps**:
1. Rewrite `backfill_detect_login_walls`:
   - Pass 1: compute SimHash for any page where `content_simhash IS NULL`.
   - Pass 2: for every page (excluding placeholder-purged), evaluate
     cluster membership.
   - For each flagged page: Qdrant delete (with org_id+kb_slug+path
     filter), `crawled_pages.content_hash = '__login_wall_purged__'`.
2. Implement `recover_purged_pages(org_id, kb_slug)`:
   - Find pages where `content_hash = '__login_wall_purged__'`.
   - Compute v2 cluster membership.
   - For pages NOT in a cluster of size ≥ N: clear content_hash to
     empty string. Next crawl re-ingests.
3. Update CLI:
   ```
   python -m knowledge_ingest.backfill_tasks \
       --org SLUG --kb SLUG [--recover]
   ```
   `--recover` flag triggers `recover_purged_pages` instead of
   `backfill_detect_login_walls`.
4. Tests: idempotency (re-run = noop), cluster boundary cases (4 pages =
   below threshold, all kept; 5 pages = at threshold, all flagged),
   recovery on cluster-shrunk corpus.

Exit criteria: Backfill produces correct counts on fixture corpora.
Recovery un-purges synthetic non-cluster pages.

---

## Phase E — Production validation script

**Files**:
- `scripts/validate_login_wall_detector.py` (new)

**Steps**:
1. Read-only script that connects via the same `tenant_scoped_connection`
   helpers and runs the v2 cluster algorithm against a tenant's KB.
2. Outputs a structured report:
   - Total pages.
   - SimHash clusters discovered (size ≥ N), with sample URLs.
   - Pages currently in `__login_wall_purged__` state whose cluster
     dropped below N (recovery candidates).
3. CI integration: a unit test invokes the script against a stubbed
   corpus to verify exit code and report shape.
4. Operator integration: `python scripts/validate_login_wall_detector.py
   --org voys --kb support` is the manual gate before merging the v2 PR.

Exit criteria: Validation script reports zero surprise classifications
on voys + getklai. Output is reproducible and parsable.

---

## Phase F — Rollout

Sequence (no further code changes; ops only):

1. Merge PR (containing Phase A–E). Image rebuilds + auto-deploys
   knowledge-ingest. Schema migration auto-applies.
2. Wait for `klai-core-knowledge-ingest-1` to report healthy. Verify
   `content_simhash` column exists.
3. Run validation script in `audit_only` mode equivalent: schema is
   ready but the detector also evaluates cluster size. Operator
   inspects the report.
4. Switch tenants to `KLAI_INGEST_LOGIN_WALL_DETECT_MODE=reject` in
   `/opt/klai/.env` (currently set; confirm no override changes
   needed).
5. Run `backfill_detect_login_walls --org voys --kb support` (will
   re-evaluate the 422 pages; idempotent — pages already purged stay
   purged because their cluster still meets threshold).
6. Run `backfill_detect_login_walls --org getklai --kb voys-test`.
7. Run `recover_purged_pages --org getklai --kb voys-test --recover`.
   This un-purges `/2fa-freedom` (cluster size for it under v2 should
   be 0 — no template clusters in getklai).
8. Trigger a re-crawl of `/2fa-freedom` URL to populate the page back
   into Qdrant under v2 logic.
9. Verify the original failing query: voys/redcactus walls remain
   purged; voys/account-recovery and getklai/2fa-freedom return.
10. Update SPEC-INGEST-LOGIN-WALL-DETECT-001 status to `superseded`.

---

## Phase G — Post-merge SPEC bookkeeping

- `SPEC-INGEST-LOGIN-WALL-DETECT-001/spec.md`: change `status: completed`
  to `status: superseded`. Add HISTORY entry pointing to v2.
- `SPEC-INGEST-LOGIN-WALL-DETECT-001/plan.md`: prepend a callout that v2
  has replaced Phase A-E detector logic.
- Add a CHANGELOG entry to `klai-knowledge-ingest`.

---

## Risk register

| Risk | Mitigation |
|---|---|
| SimHash threshold tuning (Hamming ≤ 3) misses real walls | Phase B fixture tests pin behaviour; Phase E validation script reports actual cluster sizes; threshold is a SPEC change, requires re-validation |
| Walls have too much per-page variation, hashes drift apart | Pre-hash normalisation (URL → `<URL>`, anchor text only) isolates the template; if still insufficient, fall back to MinHash + LSH (documented in research.md §4.2 as Phase D contingency) |
| Cluster scan O(N²) becomes slow on huge KBs | Performance budget (REQ-08): 50 ms p99 for N ≤ 1000. At higher N, LSH banding becomes warranted; deferred to follow-up SPEC |
| `/2fa-freedom` recovery re-crawl fails (URL no longer accessible, e.g.) | Manual verification: operator inspects re-crawl outcome and re-triggers if needed; this is a one-shot recovery, not ongoing |
| Schema migration race with running ingests | Migration is additive (new NULL-able column + index). Existing ingests continue inserting rows with NULL simhash; backfill catches them up |

---

## Decisions resolved during draft

| Decision | Resolution | Rationale |
|---|---|---|
| Hash algorithm | SimHash | Single 64-bit fingerprint; SQL-friendly; sufficient at klai's scale; MinHash deferred as Phase D contingency |
| Hamming threshold | 3 (fixed) | Standard for "≥ 95% content overlap"; calibrated against fixtures |
| Cluster threshold | 5 (configurable via env) | Catches RedCactus's 149-page cluster easily; protects single-page walls under cold-start permissiveness |
| Where to fingerprint (raw_markdown vs fit_markdown) | `fit_markdown or raw_markdown` | Prefer crawl4ai's extracted main content; fall back when fit absent |
| Pre-hash normalisation | URL strip + anchor text + lowercase + whitespace collapse | Validated against fixtures: per-URL variation drops below Hamming 3 after normalisation |
| In-tree vs library SimHash | In-tree (~50 LOC) | Avoid dependency churn; algorithm is well-known; team has full visibility |
| Backwards-compat: detector signature | Keep `(markdown, fit_markdown=None)` positional/kw-only; add optional `org_id, kb_slug, conn` for cluster query | Minimises caller diffs in `_ingest_crawl_result` and backfill |
| Audit_only / degrade / reject modes | Unchanged from v1 | Operational surface stays familiar; mode semantics map identically to v2 detector outputs |

---

## Open questions for plan-phase annotation

None remaining. Ready for /moai run.
