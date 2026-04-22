# Sync Report — 2026-04-22 post-smoke-test polish pass

**Timestamp (UTC):** 2026-04-22T11:04:00Z
**Branch:** `feature/SPEC-CRAWLER-004`
**Sync commit:** `99bccfa7` docs(audit): sync roadmap with post-smoke-test polish pass
**Scope:** Multi-SPEC polish pass — no single SPEC-ID drives this sync.
**Mode:** auto (docs-only, no PR)

---

## Changes synchronized

Two files updated in this sync commit:

| File | Purpose | Lines |
|---|---|---|
| `.moai/audit/99-fix-roadmap.md` | Changelog extension with 7 new entries for today's polish-pass commits + OBS-001 Phase C/D note | +9 / -1 |
| `.moai/audit/00-plan.md` | Header timestamp + implementation-status augmentation (AUTH-008-G flush follow-up, SPEC-AUTH-004 UI completion, polish pass summary) | +4 / -2 |

Both files are documentation — no code, test, migration, or configuration impact.

## Commits referenced in the sync (origin of today's polish pass)

| Commit | Subject |
|---|---|
| `b64d70dc` | fix(meetings): drop post-commit db.refresh — RLS tenant context is gone (AUTH-008-F) |
| `486336a1` | fix(portal-backend): AUTH-008-G — strip post-commit db.refresh on category-D RLS tables |
| `ddb6cbc5` | fix(portal-backend): AUTH-008-G follow-up — restore pre-commit refresh on CREATE endpoints |
| `94025f1b` | fix(portal-backend): AUTH-008-G — add db.flush() before pre-commit refresh in widget/api-key create |
| `0ba174b0` | fix(recording-cleanup): treat upstream 404 as success, stop log spam |
| `a452768a` | refactor(portal): harmonize admin route prefixes under /api/admin/ |
| `d3a892f5` | style(portal-backend): ruff format recording_cleanup.py |
| `c7c936ec` | fix(portal-frontend): parse FastAPI 422 validation errors into human text |
| `08e5a25e` | feat(admin-groups): wire up group-product assignment UI (SPEC-AUTH-004) |

## Quality gates

| Gate | Result | Evidence |
|---|---|---|
| Phase 0 pre-sync quality | ✓ pass | docs-only commit, no lint/format scope |
| Phase 0.1 deployment readiness | ✓ READY | no tests affected, no migrations, no env vars, no breaking changes |
| Phase 0.5 language-specific diagnostics | ✓ N/A | no source files touched |
| Phase 0.55 security scan | ✓ skipped | no security-sensitive patterns in diff |
| Phase 0.6 MX tag validation | ✓ skipped | no code files |
| Phase 0.7 coverage analysis | ✓ skipped | no code files |
| Phase 1.5 SPEC divergence | ✓ N/A | docs catch up to code reality; no divergence |
| Phase 2.3 post-sync quality | ✓ pass | TRUST 5 applicable fields (Readable, Unified, Trackable) all met |
| Remote CI on branch | ✓ both green | Build and push portal-api + SAST — Semgrep |

## SPEC status changes recorded in the sync

The sync references but does not mutate per-SPEC status files. Noted in docs:

- **SPEC-AUTH-004** — admin UI completed (group-product assignment). Backend endpoints had existed since SPEC-AUTH-004 but no frontend caller; today's `08e5a25e` closes §S2.
- **AUTH-008-F** + **AUTH-008-G** — post-commit `db.refresh()` class-of-bug fully swept (24 sites across 5 files + 2-site flush follow-up).
- **SEC-021** — runtime-api socat bridge LIVE on dev, previously documented on 2026-04-22 AM.
- **OBS-001** — Phase C+D LIVE (owned separately, noted but not touched by this sync).

## Context memory

Git commit `99bccfa7` carries structured context in its message body (SPEC references, decision rationale, branch context). Session boundary tag created: `moai/audit-polish-20260422/sync-complete` pointing at `99bccfa7`.

## Deliverability

- Commit is on `origin/feature/SPEC-CRAWLER-004` (already pushed prior to this sync ceremony)
- No PR created — doc changes will land on main when CRAWLER-004 merges, or cherry-pick is trivial if timing warrants
- Rollback path: `git revert 99bccfa7` on the feature branch; docs are self-contained

## Next steps

- (branch owner) CRAWLER-004 Fase A is in progress — sync report will ride along on merge
- (project-wide) No follow-up action required from this sync
