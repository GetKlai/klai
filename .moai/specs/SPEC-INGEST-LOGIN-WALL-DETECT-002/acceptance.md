# Acceptance Criteria — SPEC-INGEST-LOGIN-WALL-DETECT-002

Gherkin Given/When/Then scenarios per requirement. Baseline configuration:
voys tenant (zitadel_org_id `368884765035593759`), KB `support`, unless
noted otherwise. Production fixtures live in
`klai-knowledge-ingest/tests/fixtures/auth_walls/` (true positives) and
`tests/fixtures/clean_pages/` (true negatives, including the 5 captured
production FPs).

---

## REQ-01 — Content fingerprint at ingest

### AC-01.1: Deterministic hash

```gherkin
Given a markdown string M
When compute_simhash(M) is called twice
Then the two return values are identical 64-bit integers
```

### AC-01.2: URL-only variation produces near-identical hash

```gherkin
Given two markdown strings A and B that differ ONLY in their canonical
       URL embedded in the page chrome
  And both strings are otherwise byte-identical
When compute_simhash(A) and compute_simhash(B) are computed
Then hamming_distance(hash_a, hash_b) <= 3
  And this validates that pre-hash URL normalisation is in effect
```

### AC-01.3: Unrelated pages produce divergent hashes

```gherkin
Given two markdown strings drawn from the production captured fixtures
       redcactus_hubspot.md (wall) and redcactus_ifttt.md (clean tutorial)
When their SimHashes are computed
Then hamming_distance > 10
  And the fingerprints are clearly distinguishable as different content
```

### AC-01.4: SimHash stored at ingest

```gherkin
Given a successful crawl of a new page in voys/support
When _ingest_crawl_result completes
Then a row exists in knowledge.crawled_pages for that URL
  And content_simhash is non-NULL
  And the stored hash equals compute_simhash(fit_markdown or raw_markdown)
```

### AC-01.5: Storage tenant scope

```gherkin
Given two tenants voys and getklai
  And both have ingested a page that happens to share content with each
      other (e.g., both ingested the same Voys help article)
When their SimHashes are computed
Then the hashes are stored under each tenant's own org_id+kb_slug rows
  And no cross-tenant row write occurs
```

---

## REQ-02 — Cluster-based wall detection

### AC-02.1: 149 RedCactus walls form a single cluster

```gherkin
Given the 149 voys/support pages whose URL is under wiki.redcactus.cloud
       AND whose raw_markdown contains the RedCactus boilerplate template
When detect_anonymous_auth_wall is called for any one of them with
     org_id=voys, kb_slug=support, conn=connection
Then the returned AuthWallSignal has pattern="template_cluster"
  And evidence is "cluster_size=148 hamming<=3" (148 = 149 minus the page itself)
  And confidence >= 0.9
```

### AC-02.2: Production FPs do NOT cluster

```gherkin
Given the 5 captured production FP URLs:
       https://help.voys.nl/2fa-freedom
       https://help.voys.nl/account-toegang
       https://wiki.redcactus.cloud/nl/phone-software/zoom
       https://wiki.redcactus.cloud/nl/phone-software/zoom-embedded
       https://wiki.redcactus.cloud/nl/crm-software/IFTTT
  And the SimHash of each is computed
When detect_anonymous_auth_wall is called for each in turn
Then each returns None
  And the cluster size for each is below 5
```

### AC-02.3: Synthetic CMS fixtures

```gherkin
Given the synthetic Confluence wall fixture confluence_login_required.md
  And 4 other duplicate copies (same content, different URLs) seeded into
      a test corpus
When detect_anonymous_auth_wall is called with the test corpus connection
Then the returned signal has pattern="template_cluster"
  And cluster_size is at least 4
```

### AC-02.4: Cluster boundary at threshold

```gherkin
Given a corpus with N pages all sharing identical content
  And the configured KLAI_INGEST_TEMPLATE_CLUSTER_MIN is 5
When detect_anonymous_auth_wall is called
Then if N == 5: ALL pages return signal (cluster_size = 4 OTHERS, total = 5
     — interpretation: the page itself + 4 others meets threshold)

# Implementation note: the actual SQL query is "count of OTHERS within
# Hamming <= 3" -> threshold is actually count >= (N - 1) for the page
# itself to be included. Spec language uses "N or more OTHER pages" and
# the test pins this exact boundary.
```

### AC-02.5: Hamming threshold sensitivity

```gherkin
Given a SimHash A and a SimHash B at hamming_distance == 4
When the cluster query runs (Hamming <= 3 strict)
Then B is NOT counted as a member of A's cluster
  And the threshold is documented as a SPEC-revision change, not env-tunable
```

---

## REQ-03 — Cold-start permissiveness

### AC-03.1: Sparse tenant — single page

```gherkin
Given a freshly-onboarded tenant new_tenant with kb_slug "support"
  And exactly 1 page exists in (new_tenant, "support")
When detect_anonymous_auth_wall is called for that page
Then the result is None
  And the cluster size is 0 (page itself excluded; no other pages exist)
```

### AC-03.2: Sparse tenant — at threshold edge

```gherkin
Given a tenant with exactly 5 pages in (org_id, kb_slug), all template
       duplicates of each other
When detect_anonymous_auth_wall is called for any one of them
Then the result is a non-None signal
  And cluster_size = 4 (the 4 OTHERS)
  And the threshold "N or more OTHER pages" is interpreted as count >= 4
      (where N = configured min - 1)

# Editorial note: tighten this in implementation to match REQ-02's
# "N or more OTHER pages" exactly with N=5, ie strictly >= 5 OTHERS.
# In that case AC-03.2 returns None.
```

### AC-03.3: Sparse tenant — just under threshold

```gherkin
Given a tenant with 4 pages all template duplicates
When detect_anonymous_auth_wall is called for any one
Then the result is None
  And the operator-visible log line confirms cold-start permissiveness
```

---

## REQ-04 — Backfill replay

### AC-04.1: Re-running on a fresh tenant computes hashes

```gherkin
Given a tenant whose content_simhash is NULL on every page
When backfill_detect_login_walls(org_id, kb_slug) runs
Then every page in (org_id, kb_slug) has a non-NULL content_simhash after
  And the function returns a "processed" count equal to the page count
```

### AC-04.2: Backfill purges template clusters

```gherkin
Given voys/support before backfill (template-stub pages + non-stub pages)
  And no SimHashes are populated yet
When backfill_detect_login_walls runs
Then the result reports the cluster members as flagged
  And each flagged page has content_hash = '__login_wall_purged__'
  And none of the 5 known FP URLs is flagged
```

**Production result (2026-05-06 rollout, voys/support):**

The SQL pre-filters rows already at ``__login_wall_purged__`` (= 150
v1-purged rows from PR #419). The remaining 272 active rows ran through
v2 clustering. 199 of those 272 hit the cluster threshold and were
flagged + Qdrant-deleted + placeholder-set:

```
{"processed": 272, "flagged": 199, "qdrant_deleted": 199}
```

The validation script (read-only, sees ALL rows including v1-purged)
reports the larger union — 4 connected components of sizes
``[214, 74, 25, 3]`` totalling **316 wall pages**, all under
``wiki.redcactus.cloud``. The 117 difference (316 − 199) is the v1-
purged subset already at placeholder; backfill leaves them alone.

**v2 vs v1 effectiveness:** v1's phrase-substring detector found 150;
v2's clustering finds 316. The 166 extra pages have the same boilerplate
template as v1's 150 but their phrasing varies enough that no canonical
phrase matched. v2's structural detection generalises across phrasing
variants. The SPEC's drafted "149 walls" estimate was pulled from the
v1 phrase-distribution count, not the v2 cluster count.

The exact ``flagged`` number depends on how many pages are already at
the placeholder hash on the day backfill runs; tests pin behaviour for
a synthetic 6-page cluster (see ``tests/test_backfill_login_walls.py``)
rather than a fixed production count.

### AC-04.3: Idempotency

```gherkin
Given backfill_detect_login_walls already ran successfully for voys/support
  And no new pages were ingested since
When backfill_detect_login_walls runs again
Then the result reports {"processed": 273, "flagged": 0, "qdrant_deleted": 0}
       (placeholder pages are excluded from re-evaluation)
  And no Qdrant deletes are issued
  And no rows are mutated
```

### AC-04.4: Tenant isolation in Qdrant deletes

```gherkin
Given the backfill task is implementing point deletion for a wall
When the developer attempts to delete points using only path or kb_slug
Then the semgrep rule from .github/workflows/tenant-isolation-review.yml
     fails the PR
  And the rule message references "FieldCondition(key='org_id', ...)"
```

---

## REQ-05 — Recovery of v1-purged FPs

### AC-05.1: Single-FP recovery on getklai/voys-test

```gherkin
Given /2fa-freedom is in getklai/voys-test with content_hash =
       '__login_wall_purged__'
  And under v2 cluster logic, /2fa-freedom is NOT in any cluster
When recover_purged_pages(getklai_org_id, "voys-test") runs
Then the result reports {"processed": 1, "recovered": 1}
  And /2fa-freedom's content_hash is now empty string
  And the next scheduled crawl re-ingests it normally
```

**Recovery semantics — re-purge after re-crawl (operational note):**

A "recovered" placeholder does NOT mean the page is permanently a
not-wall. It means the page's STORED ``raw_markdown`` (frozen from the
v1-purge era) is no longer in a cluster under v2 — the cluster of
matching pages either shrank (other members were purged or evolved) or
the stored markdown predates the current template.

When the next scheduled crawl re-fetches the URL, crawl4ai returns the
CURRENT page content. If the source CMS still serves a templated stub
to anonymous visitors, the new ``raw_markdown`` will fingerprint into
the active wall cluster and v2's ingest-time detector will re-flag the
page (mode=reject by default). Net effect: the page transitions from
purged-stale → temporarily un-purged → re-purged-with-current-data.

**Production observation (2026-05-06 rollout):** voys/support recovery
returned ``{"processed": 349, "recovered": 33}``. Of those 33 recovery
candidates, 32 are ``wiki.redcactus.cloud/...`` URLs whose stored
markdown predates the current v2-detected wall cluster — they will
re-purge after re-crawl. 1 is ``https://help.voys.nl/2fa-freedom``, a
real Voys tutorial that v1 mistakenly phrase-matched and v2 correctly
exonerates; this one will stay un-purged after re-crawl.

Operators should expect the apparent recovered-count to shrink toward
the "true FPs" count (roughly 1-of-33 here) as scheduled crawls
re-evaluate the candidates. This is correct, self-healing behaviour;
NOT a regression in v2.

### AC-05.2: No spurious recovery

```gherkin
Given a tenant whose all purged pages still belong to v2 clusters
When recover_purged_pages runs
Then the result reports {"processed": N, "recovered": 0}
  And no content_hash is mutated
```

### AC-05.3: Cross-org isolation

```gherkin
Given recover_purged_pages is invoked for getklai
When the SQL query runs
Then no voys rows are touched
  And tenant_scoped_connection sets app.current_org_id correctly
```

---

## REQ-06 — Caller signature stability

### AC-06.1: Function signature unchanged for v1 callers

```gherkin
Given existing callers of detect_anonymous_auth_wall in
       _ingest_crawl_result and backfill_tasks
When v2 ships
Then no caller signature change is required (positional and keyword
     args remain compatible)
  And new optional parameters (org_id, kb_slug, conn) default to None
```

### AC-06.2: Fail-safe when DB unavailable

```gherkin
Given the new detector is called WITHOUT org_id, kb_slug, or conn
When detect_anonymous_auth_wall returns
Then the result is None (no flag)
  And exactly one WARN log line is emitted with
      event="auth_wall_detector_db_missing"
  And the ingest pipeline is NOT blocked
```

---

## REQ-07 — Mode handling preserved

### AC-07.1: Reject mode raises typed exception

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="reject"
  And a page that is in a cluster of size 5+ under v2
When _ingest_crawl_result runs for that page
Then AnonymousAuthWallDetected is raised
  And the exception's signal.pattern == "template_cluster"
```

### AC-07.2: Degrade mode applies quality_score=0

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="degrade"
  And a page that v2 classifies as a wall
When _ingest_crawl_result runs
Then no exception is raised
  And the resulting Qdrant points have quality_score == 0.0
  And the ingest_warning metadata is set
```

### AC-07.3: Audit_only logs and continues

```gherkin
Given KLAI_INGEST_LOGIN_WALL_DETECT_MODE="audit_only"
  And a page that v2 classifies as a wall
When _ingest_crawl_result runs
Then the page ingests with default quality_score=0.5
  And one WARN log line is emitted with event="login_wall_detected"
      and pattern="template_cluster"
```

---

## REQ-08 — Performance

### AC-08.1: SimHash compute p99 budget

```gherkin
Given a 100 KB markdown string sampled from a real RedCactus walled page
When compute_simhash is called 1000 times
Then the 99th percentile execution time is below 5 ms
  And no measurement exceeds 50 ms (no pathological tail)
```

### AC-08.2: Cluster query p99 budget

```gherkin
Given a synthetic 1000-page corpus in test_db
  And every page has a content_simhash populated
When the cluster query runs for one of the pages
Then the 99th percentile query latency is below 50 ms
  And the result is correctly counted (verifiable by independent scan)
```

### AC-08.3: Detector adds bounded ingest latency

```gherkin
Given a fresh page ingest under v2
When _ingest_crawl_result completes for that page
Then the additional latency vs v1 (measured via ingest_decision_record)
     is below 100 ms
```

---

## REQ-09 — Tenant isolation

### AC-09.1: Cluster query never crosses tenants

```gherkin
Given two tenants voys and getklai with overlapping content
When the cluster query runs for a voys page
Then no getklai rows are scanned (verifiable in PostgreSQL EXPLAIN)
  And the WHERE clause includes BOTH org_id AND kb_slug filters
```

### AC-09.2: Connection-level RLS context

```gherkin
Given the backfill task processes voys
When the implementing code is reviewed
Then every SELECT/UPDATE on knowledge.* runs through
     tenant_scoped_connection(org_id)
  And no raw pool acquire bypasses the GUC
```

### AC-09.3: Qdrant filter contains org_id+kb_slug+path

```gherkin
Given the backfill task is deleting Qdrant points for a flagged page
When the Filter object is constructed
Then Filter.must contains exactly three FieldConditions:
       FieldCondition(key="org_id", match=MatchValue(value=org_id)),
       FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
       FieldCondition(key="path", match=MatchValue(value=url))
  And removing any one fails the semgrep tenant-isolation rule
```

---

## REQ-10 — Production-data validation gate

### AC-10.1: Validation script runs on voys/support

```gherkin
Given the v2 code is deployed to staging
  And `python scripts/validate_login_wall_detector.py --org voys --kb support`
      is invoked
When the script completes
Then exit code is 0
  And stdout reports:
       Total pages: 422
       Wall clusters: 1
         cluster_size: 149
         sample_urls: [up to 10 redcactus.cloud URLs]
       v1-purged but no longer clustering: 0
  And the report is JSON-parsable
```

### AC-10.2: Validation script flags getklai recovery candidate

```gherkin
Given the v2 code is deployed
  And `python scripts/validate_login_wall_detector.py --org getklai --kb voys-test`
      runs
Then the report includes /2fa-freedom in
     "v1-purged but no longer clustering"
  And the operator runs recover_purged_pages to act on the report
```

---

## End-to-end Phase F validation (operator-driven)

### AC-F.1: Original failing query honoured post-rollout

```gherkin
Given v2 has deployed and the backfill ran on voys/support
  And the recovery ran on getklai/voys-test
  And /2fa-freedom has been re-crawled
When a user asks "Hoe stel ik RedCactus in met HubSpot?" in voys's chat
Then retrieval-api returns either zero chunks (gap_type=hard) or chunks
     from non-walled sources
  And the chat does not surface RedCactus walled stub content
  And the chat does NOT lie — it admits it cannot find the answer when
     no relevant sources exist
```

### AC-F.2: Recovered tutorial returns to retrieval

```gherkin
Given /2fa-freedom has been re-crawled under v2
When a user in voys asks "How do I set up 2FA in Freedom?"
Then retrieval surfaces /2fa-freedom in the top results
  And the chat answers the question correctly using that source
```
