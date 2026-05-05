---
id: SPEC-CI-PG-FIXTURE-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: medium
related:
  - audit-2026-05-05-followups.md (cluster: CI-infra fixture hardening)
  - SPEC-INGEST-ALEMBIC-001 (provides the baseline used in CI bootstrap)
  - SPEC-INFRA-TENANT-DELETE-002 (tests that currently @skipif POSTGRES_DSN)
---

# SPEC-CI-PG-FIXTURE-001: live Postgres + Qdrant fixtures in GitHub Actions CI

## Summary

Several regression-guard tests across knowledge-ingest, klai-portal, and the
deprovisioning orchestrator are currently wrapped in
`@pytest.mark.skipif(not _pg_available)` or similar guards that silently skip
in CI. The 2026-05-05 audit identified 4 distinct gaps where the skip means
"the regression-guard never actually runs", masking the very class of drift
the test was written to catch.

This SPEC adds a `services:` block to the relevant GHA workflows so live
Postgres (and where applicable Qdrant) is available, removes the skip-guards,
and restructures the affected tests to assert against real infra.

## Motivation

| Audit ref | Test | Currently | After SPEC |
|---|---|---|---|
| #1 F10 | ast-grep CI step (`rules/tests/test_no_untenanted_syncrun_lint.py`) | `pytest.skip("ast-grep CLI not available")` if `sg`/`uvx` absent | uvx installed via `astral-sh/setup-uv@v7` so the test never silently skips |
| #3 MED 8 | G3 idempotency test (`test_wipe_postgres_endpoint.py`) | uses two mock pools to fake idempotency — bypasses real DELETE-on-empty | runs against a live Postgres service container |
| #3 MED 10 | G3 schema-regression-guard (`TestWipePostgresSchemaGuard`) | `@pytest.mark.skipif(not _pg_available)` — never runs in CI | runs in CI with `POSTGRES_DSN` set, asserts every `knowledge.*` table with `org_id` is in the wipe list |
| #3 MED 11 | Qdrant klai_focus filter-key drift | NO test exists at all — only a 2026-05-05 live-prod-probe verified the `tenant_id` payload key | new integration test against a Qdrant service container |

## Scope

### In scope

1. **`.github/workflows/knowledge-ingest.yml`**:
   - Add `services: postgres:` block (postgres:16 image, healthcheck)
   - Bootstrap the `knowledge` schema BEFORE pytest runs — either via
     `alembic upgrade head` against the test DB OR a hand-coded minimal CREATE
     for tables under test (the latter is faster but couples to test scope)
   - Set `POSTGRES_DSN: postgresql://postgres:testpw@localhost:5432/testdb`

2. **`.github/workflows/portal-api.yml`** (or a dedicated `qdrant-integration.yml`):
   - Add `services: qdrant:` block (qdrant/qdrant:latest, port 6333)
   - Set `QDRANT_URL: http://localhost:6333`

3. **`klai-knowledge-ingest/tests/test_wipe_postgres_endpoint.py`**:
   - `TestWipePostgresSchemaGuard`: drop the skipif, assert against live PG
   - G3 idempotency: replace mock-pool fake with real seeded rows + DELETE roundtrip

4. **`klai-portal/backend/tests/test_qdrant_payload_keys.py`** (NEW):
   - Creates `klai_focus` + `klai_knowledge` collections in the Qdrant container
   - Inserts a single point in each with the canonical payload shape
     (`tenant_id` for klai_focus, `org_id` for klai_knowledge)
   - Imports `_delete_qdrant_points` from `deprovisioning_steps`, calls with a
     stub state, asserts the right point is deleted from the right collection
   - Skips elegantly when `QDRANT_URL` is not set so local pytest still passes

5. **`.github/workflows/<ast-grep-runner>.yml`**:
   - Add `astral-sh/setup-uv@v7` step before any pytest step that runs
     `rules/tests/test_no_untenanted_syncrun_lint.py` — so the `uvx` fallback
     in the test's `_ast_grep_cli()` chain is reachable

### Out of scope

- Live FalkorDB fixture (FalkorDB is more involved; covered by separate SPEC if ever needed)
- Live Redis fixture (already exists for portal-api tests via the existing redis service-container)
- Migrating other `@skipif` tests not on the audit list — separate sweep

## Acceptance criteria

1. After this SPEC merges, `gh pr checks <pr>` shows **zero** SKIPPED test
   classes for the 4 audit items above. The schema-regression-guard runs and
   passes against the live PG.
2. CI runtime increases by no more than ~90s (postgres + qdrant container
   startup + bootstrap). If it climbs above that, the bootstrap step uses
   hand-coded minimal CREATEs instead of full alembic upgrade.
3. A future schema change adding a new `knowledge.*` table with `org_id`
   that is NOT added to the wipe list FAILS CI on this PR's regression-guard.
4. The Qdrant payload-key drift test passes against a freshly-created
   `klai_focus` collection with `tenant_id` payload key, AND fails fast if
   the collection ever uses `org_id` instead.
5. ast-grep CI step never logs `pytest.skip("ast-grep CLI not available")`.

## Risks

| Risk | Mitigation |
|---|---|
| Service-container startup flake (postgres / qdrant slow to be healthy) | Use `--health-cmd` + `--health-retries 5`, allow 30s startup window |
| Alembic-bootstrap of the test DB takes too long (several seconds per CI run) | Hand-coded minimal CREATE for just the tested tables; fall back to alembic only if the regression-guard scope grows |
| Qdrant integration tests are flaky if container isn't isolated | Each test creates and deletes its own collection by name — no shared state |
| Adding a `services:` block triggers a re-run on every PR build (not just main) | Acceptable — the test job already runs on PRs; only the deploy job needs main-gating |

## References

- audit-2026-05-05-followups.md — won't-fix items resolved by this SPEC
- klai-knowledge-ingest/tests/test_wipe_postgres_endpoint.py
- klai-portal/backend/app/services/provisioning/deprovisioning_steps.py
- klai-libs/image-storage/klai_image_storage/url_guard.py — pattern for
  service-container based integration test (similar in scope)
- rules/tests/test_no_untenanted_syncrun_lint.py — `_ast_grep_cli` fallback chain
