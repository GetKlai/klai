# SPEC-INGEST-LOGIN-WALL-DETECT-002 — compact form

Auto-generated extract for /moai run token efficiency. Full context in
spec.md, plan.md, acceptance.md, research.md.

## Requirements

### REQ-01 — Content fingerprint at ingest

Every successfully-ingested page SHALL have a 64-bit SimHash stored in
`knowledge.crawled_pages.content_simhash`. Pre-hash normalisation
(URL → `<URL>`, anchor `[text](url)` → `text`, lowercase, whitespace
collapse, word-boundary tokenisation) ensures per-page URL variation
does not dominate the hash.

### REQ-02 — Cluster-based wall detection

The detector SHALL classify a page as a wall when N or more OTHER pages
in the same `(org_id, kb_slug)` have a SimHash within Hamming distance 3
of the page's own. Default N=5, configurable via
`KLAI_INGEST_TEMPLATE_CLUSTER_MIN`. Hamming threshold of 3 is fixed.

### REQ-03 — Cold-start permissiveness

When fewer than (N+1) pages exist in `(org_id, kb_slug)`, the detector
returns `None` for every page. Single or few-page walls are not flagged.

### REQ-04 — Backfill replay

`backfill_detect_login_walls(org_id, kb_slug)`: compute SimHashes for
NULL rows, evaluate cluster membership, delete Qdrant points + mark
`__login_wall_purged__` for cluster members. Idempotent.

### REQ-05 — Recovery of v1-purged FPs

`recover_purged_pages(org_id, kb_slug)`: re-evaluate placeholder pages;
clear `content_hash` to empty string for those NOT in v2 clusters.
Forces re-ingest at next crawl.

### REQ-06 — Caller signature stability

`detect_anonymous_auth_wall(markdown, fit_markdown=None, url=None,
org_id=None, kb_slug=None, conn=None) -> AuthWallSignal | None`. Fail-safe
on missing DB args (return None + WARN log).

### REQ-07 — Mode handling preserved

Three modes from SPEC-001 (reject / degrade / audit_only) apply
unchanged. v2 detector returns `AuthWallSignal(pattern="template_cluster",
evidence=("cluster_size=N hamming<=3",), confidence=0.9)` instead of
`canonical_phrase_*`.

### REQ-08 — Performance

- SimHash compute p99 < 5 ms on 100 KB markdown.
- Cluster query p99 < 50 ms on 1000-page KB.
- Per-page ingest latency added < 100 ms.

### REQ-09 — Tenant isolation

All SQL filters by `org_id + kb_slug`. Qdrant deletes use
`Filter.must` with `org_id + kb_slug + path`. Semgrep tenant-isolation
rule must continue to pass.

### REQ-10 — Production-data validation gate

`scripts/validate_login_wall_detector.py --org SLUG --kb SLUG`. Reports
clusters + recovery candidates. Merge gate: 0 surprise classifications
on voys + getklai.

## Acceptance summary

- 149 RedCactus walls in voys/support form one cluster (size 149).
- 5 captured production FPs do NOT cluster.
- 4 synthetic CMS walls (Confluence/WordPress/Notion + 3 dupes each)
  cluster correctly.
- Backfill is idempotent. Recovery un-purges /2fa-freedom.
- Reject/degrade/audit_only modes unchanged.
- Tenant isolation enforced via SQL + Qdrant filters.
- p99 budgets met (5 ms compute, 50 ms cluster query).

## Files to modify

- `klai-knowledge-ingest/alembic/versions/XXXX_crawled_pages_simhash.py` (new)
- `klai-knowledge-ingest/knowledge_ingest/utils/content_fingerprint.py` (new)
- `klai-knowledge-ingest/knowledge_ingest/utils/auth_wall_detector.py` (rewrite)
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` (modify)
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py` (modify — `update_crawled_page_simhash`)
- `klai-knowledge-ingest/knowledge_ingest/backfill_tasks.py` (rewrite + add `recover_purged_pages`)
- `klai-knowledge-ingest/tests/test_content_fingerprint.py` (new)
- `klai-knowledge-ingest/tests/test_auth_wall_detector.py` (rewrite)
- `klai-knowledge-ingest/tests/test_crawler_template_detection.py` (new)
- `klai-knowledge-ingest/tests/test_backfill_login_walls.py` (rewrite)
- `klai-knowledge-ingest/tests/fixtures/auth_walls/` (replace fixtures with cluster-corpus fixtures)
- `klai-knowledge-ingest/tests/fixtures/clean_pages/` (keep 5 production FPs as anti-fixtures)
- `scripts/validate_login_wall_detector.py` (new)
- `.moai/specs/SPEC-INGEST-LOGIN-WALL-DETECT-001/spec.md` (status: superseded)

## Exclusions (What NOT to Build)

- ML-based classifier (soft404 pip library) — see research.md §3.3.
- Per-page deterministic multi-feature scorer (link density, brevity,
  redirect density) — see research.md §3.2.
- Authenticated re-crawl with cookies — separate effort under
  `klai-libs/connector-credentials`.
- LSH banding infrastructure — deferred until brute-force scan
  measurably under-performs (none expected at klai's scale).
- Cross-KB or cross-tenant cluster detection — tenant-isolation hazard.
- Trafilatura integration — duplicate of crawl4ai's fit_markdown.
- Modifications to retrieval-side `quality_floor` filter — unchanged
  from v1 Phase E.
- Modifications to mode flags or rollout operational model — unchanged
  from v1 Phase B.
