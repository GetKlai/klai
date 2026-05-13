---
id: SPEC-TEST-QUALITY-001
version: "0.1.0"
status: in-progress
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: low
related:
  - audit-2026-05-05-followups.md (cluster: test-mechanics nits)
---

# SPEC-TEST-QUALITY-001: close 5 test-mechanics nits from 2026-05-05 audit

## Summary

The 2026-05-05 audit identified 5 small test-mechanics issues across
3 services. Each fix is independently low-value (~5-15 lines) but the
cluster has high collective hygiene value when reviewed together.

This SPEC tracks the 5 items + their landing PRs. Implementation was
done per-PR-branch where the affected file lived (rather than a meta-
merge branch) to avoid cross-PR conflicts.

## Scope + status

| Audit ref | Item | Implemented in | Status |
|---|---|---|---|
| #1 F5 | connector test `importlib.reload` not isolation-safe | PR #324 commit `9dd66d09` | DONE — switched to `sys.modules.pop + importlib.import_module` |
| #1 F6 | knowledge-ingest 401-detail string brittle | PR #355 (this SPEC's main PR) | DONE — added explicit trade-off comment retaining smoke-check + cross-reference |
| #1 F8 | ast-grep `_cross_org_marker = ... # noqa` fragility | PR #318 commit `a227863d` (earlier audit pass) | DONE — replaced with `_ = SyncRun.org_id` (no noqa needed) |
| #1 F9 | `test_valid_env_constructs_settings` low marginal value | PR #323 commit `73fd28c9` | DONE — added explicit canary-vs-conftest-drift docstring rationale |
| #1 F10 | ast-grep CI step silently skips when uvx absent | SPEC-CI-PG-FIXTURE-001 | DEFERRED to sibling SPEC |
| #3 MED 8 | G6 `_FakeSession` regex SQL parsing fragile | (not yet) | DEFERRED — refactor scope > audit-pass; needs SQLAlchemy statement-inspection rewrite, separate PR |

## Acceptance criteria

1. Each per-PR commit listed above merges with its parent PR
2. The deferred items (F10, MED 8) appear explicitly in
   `audit-2026-05-05-followups.md` with the SPEC pointer that owns them

## References

- audit-2026-05-05-followups.md — full audit context
- klai-portal/backend/tests/test_config_fail_closed.py — F9 site
- klai-knowledge-ingest/tests/test_auth_middleware.py — F6 site
- klai-connector/tests/test_portal_caller_secret_validator.py — F5 site
- klai-connector/app/services/sync_runs.py — F8 site (already merged via earlier pass)
