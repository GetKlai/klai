# Audit 2026-05-05 — Won't-fix items + follow-up SPECs

> Audit-driven sweep of 21 PRs from 2026-05-05 session via 4 evaluator-active
> agents (security / infra / GDPR / refactor clusters). Most CRIT and HIGH
> findings were addressed in-session. This document captures the items that
> were **deliberately not fixed** in the same PRs and the follow-up SPECs
> that should land them properly.

---

## Won't-fix in original PRs (cosmetic / non-blocking)

| Audit ref | Item | Why not fixed in PR | Follow-up |
|---|---|---|---|
| #1 F5 | connector test `importlib.reload` not isolation-safe | xdist-style nit; tests are not run in parallel mode in CI today | Bundle into a future "test-isolation hardening" SPEC |
| #1 F6 | knowledge-ingest `taxonomy_auto_categorise` test asserts on error-message text | brittle if InternalSecretMiddleware error-text reformats; LOW value to fix in isolation | Bundle |
| #1 F9 | `test_valid_env_constructs_settings` is low-marginal-value | adds defence-in-depth without proving any specific validator | Leave; remove only if test count budget tightens |
| #1 F10 | ast-grep CI step skips silently if `uvx` is absent | works on developer machines; CI containers have `uvx` available | Add `uvx` install to ast-grep CI step in a CI-hardening PR |
| #2 MED | knowledge-ingest + scribe-api push `:sha` tag on every PR build | operational concern (GHCR storage growth); not a security issue | Separate **SPEC-CI-GHCR-RETENTION-001** if storage cost climbs |
| #3 MED 8 | `_FakeSession` regex SQL parsing fragile | SQLAlchemy compile-format dialect-sensitive; could silently mismatch | Migrate to direct `Mock(execute)` counter pattern in test-quality SPEC |
| #3 MED 10 | G3 idempotency test uses 2 separate mock pools | bypasses real "DELETE on empty set = 0" invariant; works as smoke-test | Live-PG fixture in CI (separate **SPEC-CI-PG-FIXTURE-001**) |
| #3 MED 11 | Qdrant klai_focus filter key claim unverified in CI | live-prod-probe done 2026-05-05 (`tenant_id` confirmed); future drift not auto-detected | Same SPEC-CI-PG-FIXTURE-001 — extend with Qdrant integration |
| #4 MED A2 | `setup_logging` default param drift between mailer wrapper and shared lib | wrapper makes mailer-side ergonomic; drift only confuses future adopters | Document explicitly in `klai-libs/log-utils/README.md` |
| #4 MED B1 | `RequestContextMiddleware` not E2E-tested in mailer | shared-lib tests cover the unit; mailer integration would duplicate | Bundle into shared-lib-adoption SPEC |
| #4 MED D1 | Dockerfile trailing-slash cosmetic conflict #319 ↔ #335 | merge resolution decides; both forms work | Resolve at merge time (whoever lands second) |
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
