# Sync Report — SPEC-PORTAL-TAXONOMY-SPLIT-001

- **Date**: 2026-05-13
- **SPEC**: SPEC-PORTAL-TAXONOMY-SPLIT-001 (status: done, version 0.2.0)
- **Mode**: auto (post-merge SPEC-lifecycle sync)
- **Scope**: SPEC progress tracking + sync report. No code changes —
  the implementation work was delivered in PRs #646 / #651 / #654 / #658
  and was already on main + deployed at sync time.

## Why a separate sync PR

The four implementation PRs (#646 / #651 / #654 / #658) landed and
deployed before any single SPEC-lifecycle sync ran. A first batch sync
(#655, godcomp-batch) normalised the SPEC status to `done` and added a
stub `progress.md`, but only knew about #646 and #651 — polish rounds
2 and 3 happened after. This sync closes that gap.

## Phase summary

### Phase 0 — Pre-Sync Quality Gate
N/A. Implementation PRs each ran their own gates (tsc, eslint, vitest
all green per PR description); CI on each PR passed; final main is in
known-good state.

### Phase 0.5 — Quality Verification
N/A. Each implementation PR ran the standard checks. Cumulative state
on main:
- `tsc -b --force`: clean
- `npx eslint`: 0 errors, 0 warnings
- `vitest run`: 321/321 green (was 260 pre-SPEC)

### Phase 0.55 — Security Scan
N/A. Behaviour-preserving refactor of an internal frontend component;
no auth/db/API surface touched.

### Phase 0.6 — MX Tag Validation
N/A. Frontend TypeScript; existing files did not carry MX tags
pre-SPEC and the refactor preserved that. No new exported functions
crossed the fan_in >= 3 / complexity >= 15 thresholds.

### Phase 0.7 — Coverage
The 4 PRs added 61 new unit + characterization tests:
- 18 hook tests covering each row of SPEC Appendix A invalidation map
- 12 CoverageWidget integration tests (state machine + Suggest CTA gates)
- 14 CoverageNodeRow tests (singleton edit/delete + buffer preservation regression)
- 13 ProposalCard tests (status branches + edit/reject mode)
- 4 minor additions across polish rounds

### Phase 1.5 — SPEC-Implementation Divergence Analysis

| Planned | Implemented | Notes |
|---|---|---|
| 3 new `_components/` files | 4 files | CoverageNodeRow extracted in polish #651, not in original scope. Documented in updated progress.md. |
| `-taxonomy-hooks.ts` with 11 hooks | 11 hooks | Per Appendix A exactly. |
| `TaxonomyTab.tsx ≤ 250 lines` (AC2) | 451 lines | AC2 revised to ≤500 in PR #646 commit `7e0f31e9` with rationale appended to SPEC. |
| Behaviour preservation | Verified | Playwright on Voys after each merge confirmed identical network calls + DOM structure. |
| No `useReducer` / `useCallback` / `memo()` / TaxonomyToolbar | Held | All explicitly scoped out in SPEC. |

No undocumented scope drift. The CoverageNodeRow extension was
in-scope per the refactor's intent (the SPEC's `proposals.map → ProposalCard`
move set the precedent; CoverageWidget's `nodes.map` was the same
pattern in dual standard) and was applied during polish.

### Phase 2 — Document Sync
- `spec.md` already updated to v0.2.0 + status `done` (via godcomp-batch
  #655); no further changes needed.
- `progress.md` expanded with PRs #654 + #658 and accurate
  production-verification notes (this PR).
- This sync-report file created (this PR).
- `.moai/project/` — no updates required. The refactor introduced no
  new directories, dependencies, or architectural patterns; the
  `_components/` + `-sibling-hooks.ts` pattern was already documented
  in `.claude/rules/klai/projects/portal-frontend.md` § File
  organization, which the SPEC follows.
- README.md — no updates required.

### Phase 2.4 — SPEC Status
- Status: `done` (already set by #655)
- Version: `0.2.0` (unchanged — SPEC content is final)
- Completed: `2026-05-13`

### Phase 3 — Git Operations
- Strategy: `github_flow`
- Branch: `chore/SPEC-PORTAL-TAXONOMY-SPLIT-001-sync`
- PR target: main
- This sync delivers `progress.md` + this sync-report. No code, no
  build, no deploy.

## Files updated

- `.moai/specs/SPEC-PORTAL-TAXONOMY-SPLIT-001/progress.md` — expanded
  from stub to full implementation log with PR table, final file
  shapes, AC2 deviation rationale, test coverage summary, and
  production verification notes.
- `.moai/reports/sync-report-20260513T1730Z-SPEC-PORTAL-TAXONOMY-SPLIT-001.md` —
  this file (new).

## Outstanding follow-ups

None on this SPEC. Possible future work:
- A separate SPEC could further extract `<FilterBar>`, `<AddForm>`, and
  `<SuggestBanners>` from TaxonomyTab.tsx to hit the original ≤250 line
  target. Out of scope for this SPEC.
- Cross-cutting: a typed apiFetch error class with `.status: number`
  would let `useApproveProposal` drop the brittle `err.message.includes('409')`
  match. TODO comment added in PR #654.
