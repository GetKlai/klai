# Audit 2026-05-05 — Won't-fix items + follow-up SPECs

> Audit-driven sweep of 21 PRs from 2026-05-05 session via 4 evaluator-active
> agents (security / infra / GDPR / refactor clusters). Most CRIT and HIGH
> findings were addressed in-session. This document captures the items that
> were **deliberately not fixed** in the same PRs and the follow-up SPECs
> that should land them properly.

---

## Won't-fix in original PRs (cosmetic / non-blocking)

> **Update 2026-05-05 (later in session)**: 5 items previously in this
> section were actually addressed in follow-up commits — moved to the
> "Fixed in-session" table below. The `Why not fixed in PR` column for
> those items would now read "fixed via per-PR amend; see Fixed table".

Items that genuinely will not be addressed in the original PRs:

| Audit ref | Item | Why not fixed in PR | Follow-up |
|---|---|---|---|
| #2 MED | knowledge-ingest + scribe-api push `:sha` tag on every PR build | operational concern (GHCR storage growth); not a security issue | Separate **SPEC-CI-GHCR-RETENTION-001** if storage cost climbs |
| #3 MED 8 | `_FakeSession` regex SQL parsing fragile | DONE 2026-05-05 in #332 commit `d12d8274` — refactored to SQLAlchemy structural inspection (BinaryExpression walk + BindParameter introspection) | Closed |
| #3 MED 10 | G3 idempotency test uses 2 separate mock pools | bypasses real "DELETE on empty set = 0" invariant; works as smoke-test | Live-PG fixture in CI — **SPEC-CI-PG-FIXTURE-001** stub PR #356 (merged) |
| #3 MED 11 | Qdrant klai_focus filter key claim unverified in CI | live-prod-probe done 2026-05-05 (`tenant_id` confirmed); future drift not auto-detected | Same SPEC-CI-PG-FIXTURE-001 — extend with Qdrant integration |
| #4 MED B1 | `RequestContextMiddleware` not E2E-tested in mailer | shared-lib tests cover the unit; mailer integration would duplicate | Bundle into shared-lib-adoption SPEC |
| #4 MED D1 | Dockerfile trailing-slash cosmetic conflict #319 ↔ #335 | DONE 2026-05-05 — additive merge during #335 rebase; combined log-utils + webhook-replay COPY lines | Closed |
| #4 LOW B2 | `test_notify_replay` redundant patch after pop+reimport | works; cosmetic redundancy | Leave |
| #4 LOW C5 | `klai-libs/log-utils` declares `starlette>=0.40` runtime dep | every klai service already depends on FastAPI (transitively pulls Starlette) | Leave — explicit dep is correct hygiene |
| #4 LOW D2 | Stage 1 of mailer Dockerfile installs `git` (vestigial) | no runtime impact; reserved for future VCS-deps | Drop in next mailer Dockerfile pass |

---

## Fixed in-session (audit-pass commits)

For traceability — these items were closed by per-PR amendments during the
2026-05-05 audit-fix sweep:

| Audit ref | Item | PR | Commit/SHA |
|---|---|---|---|
| #1 F1 | connector test tautology | #324 | `25c59f7a` |
| #1 F2 | ingest valid-test bypassed import-time path | #325 | `9d281327` |
| #1 F3 | BFF_SESSION_KEY validator vs `or sso_cookie_key` fallback | #323 | `8830d65c` |
| #1 F4 | conftest BFF == SSO same Fernet key | #323 | `8830d65c` |
| #1 F7 | frontend_url whitespace silent pass | #348 (new) | `9f025e3f` |
| #1 F8 | ast-grep `_cross_org_marker` fragility (noqa removable) | #318 | `a227863d` |
| #2 CRIT | knowledge.parent_chunks missing from baseline + composite PK/FK drift | #337 | `8eaf4194` |
| #2 MED | runbook stamp command needs `--workdir` | #337 | `6d69664b` |
| #2 HIGH | portal-api Pre-deploy env check ungated | #331 | `be9be496` |
| #3 CRIT 1+2 | org_id type mismatch (int → Zitadel string) on 4 wipe steps | #343 | `0d063fba` |
| #3 HIGH 5 | G3 wipe-postgres test missing X-Internal-Secret header assertion | #343 | `0816721b` |
| #3 HIGH 6 | G6 wipe-state didn't purge connector.connectors | #332 | `6226ba03` |
| #3 HIGH 7 | 4xx body not logged before raise_for_status | #343 | `0d063fba` |
| #3 MED 9 | step-number drift in G3 endpoint docstring (13a → 9a) | #336 | `af399785` |
| #4 CRIT C1 | `:latest` deploy gate missing on #319 + #335 (mailer-incident class) | #319 + #335 | `4bee9b60` + `7fc0ae8f` |
| #4 HIGH C3 | klai-libs/log-utils paths trigger dropped from #335 workflow | #335 | `7fc0ae8f` |
| #4 HIGH C4 | rate_limit.py still used redis_asyncio.from_url (pitfall class) | #335 | `7fc0ae8f` |
| #4 HIGH A1 | klai-log-utils missing from #335 [project.dependencies] | #335 | `7fc0ae8f` |
| #4 MED E1 | portal_client.py inline guards not documented vs validator | #326 | `c208c678` |
| #1 F5 | connector test `importlib.reload` not isolation-safe | #324 | `9dd66d09` (later in session) |
| #1 F6 | knowledge-ingest 401 detail-string brittle | #355 | `86ce8924` (documented trade-off + cross-ref) |
| #1 F9 | `test_valid_env_constructs_settings` low marginal value | #323 | `73fd28c9` (added canary-vs-conftest-drift docstring) |
| #1 F10 | ast-grep CI step would skip silently | #356 | `8ee2209f` (new `rules-tests.yml` workflow with uv install) |
| #4 MED A2 | `setup_logging` default param drift wrapper-vs-shared-lib | #319 | `81f30abb` (verified-from-source README in same SPEC) |

---

## Notes for next audit pass

- **CI infrastructure gap**: items #3 MED 8/10/11 and #1 F10 are all symptoms
  of "CI does not have a live Postgres or Qdrant fixture". A single
  SPEC-CI-PG-FIXTURE-001 (or "CI service-container hardening") would close
  all of them. Frame it as a Wave 4 cluster K item alongside the existing
  SPEC-COVERAGE-CRITICAL-MODULES-001.

- **Test-quality cluster**: items #1 F5/F6/F9 + #3 MED 8 are all
  test-mechanics nits. Bundle into SPEC-TEST-QUALITY-001 (new — not yet on
  the roadmap). Low individual value, high collective hygiene value if a
  team reviewer reads them all together.

- **Documentation cluster**: items #4 MED A2 + LOW B2 + #1 F8's docstring
  comment + portal_client guard relationship — all "small documentation
  improvements". Could ride along with the next manager-docs sync pass.

---

## Post-batch additions (2026-05-05 evening session)

Beyond the original audit-pass scope, these landed in the same evening
session and are tracked here for traceability:

| Ref | Item | PR | Notes |
|---|---|---|---|
| BFF-PARITY | `BFF_SESSION_KEY` validator-env-parity miss → 4-min prod outage | #360 (noodklep) → klai-infra#4 (SOPS) → #361 (cleanup) | Pattern: silent in-code fallback (`A or B`) deletion + new validator without SOPS pre-flight. Documented as proposed pitfall in `validator-env-parity` section of process-rules.md (extend in next pass). |
| RLS-A-OWNER | #364 RLS migration crashed portal-api: `ALTER TABLE ENABLE ROW LEVEL SECURITY` requires klai owner | #367 | Migration body emptied; DDL moved to `post_deploy_2f7d1eae1198.sql`. Pitfall `alembic-cannot-drop-non-portal_api-tables` extended to enumerate all owner-required DDL (DROP TABLE, ENABLE/FORCE RLS, CREATE/DROP POLICY, ALTER OWNER). |
| RLS-PIVOT | #318 closed (superseded). Original ast-grep rule + connector adoption replaced by RLS-on-sync_runs SPEC | SPEC-SEC-CONNECTOR-RLS-001 (skeleton) | Analysis showed ast-grep rule is heuristic application-level; RLS at DB level is the right layer. New SPEC implements it via Category D + cross_org_session helper. |
| ALEMBIC-NOOP | knowledge-ingest #337 alembic baseline already stamped on prod | n/a | Verified `knowledge.alembic_version = '0001_baseline'` before merge — entrypoint's `alembic upgrade head` is no-op. |
