---
id: SPEC-PORTAL-BILLING-CLEANUP-001
version: 0.1.1
status: implemented
created: 2026-05-13
updated: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related:
  - SPEC-PORTAL-PRICING-PER-USER-001 (recently active — billing.lazy.tsx is in active development)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-BILLING-CLEANUP-001 — Split admin/billing.lazy.tsx (673 lines, 11 useState, no mutations)

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-13 | 0.1.0 | Initial draft carved out from connector wizard cleanup follow-ups. |
| 2026-05-13 | 0.1.1 | Implemented: split `billing.lazy.tsx` into billing-specific section components and reducer-backed state modules; frontend build/lint/Vitest green. |

## Goal

Reduce `klai-portal/frontend/src/routes/admin/billing.lazy.tsx` from
673 lines to a route shell + per-section components + a state machine
or `useReducer` for the 11-useState complexity. Notable: zero
mutations (all data is via fetches, billing actions go through Moneybird
external flows).

This SPEC is **draft**. Annotation cycle should coordinate with the
active SPEC-PORTAL-PRICING-PER-USER-001 work to avoid concurrent
edits.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 673 |
| useState | 11 |
| useEffect | 2 |
| Inline mutations | 0 |
| Inline queries | (TBD — likely fetched via apiFetch in useEffect) |
| Git churn last 90 days | 22 commits |
| Last touched | 3 hours ago at SPEC creation |
| Production-critical | Yes (admin billing — revenue-impact) |
| Active concurrent work | YES (SPEC-PORTAL-PRICING-PER-USER-001 phases ongoing) |

11 useState with no mutations means it's a complex local state machine.
The `useEffect`s likely orchestrate sequential fetches + state
transitions. Strong candidate for `useReducer` consolidation.

The CONCURRENT WORK signal is critical: do NOT start this SPEC while
PRICING-PER-USER-001 is mid-execution. Coordinate via SPEC dependency.

## Scope (proposed)

### In

- New `admin/_components/BillingBreakdownSection.tsx`,
  `BillingMandateSection.tsx`, etc. — per-section components
- Likely `useReducer` for the state machine (11 cross-coupled useStates
  → consolidated reducer)
- Modify `billing.lazy.tsx`: route shell + section composition

### Out

- Backend billing/Moneybird API changes
- Pricing model changes (those are PRICING-PER-USER-001 SPEC scope)
- New billing functionality

## Approach

DDD methodology. **Coordinate with PRICING-PER-USER-001 phase
schedule** — start this SPEC only after that SPEC's last phase merges,
to avoid ongoing merge conflicts (22 commits / 90 days, mostly from
that SPEC).

ANALYZE phase: map the 11 useStates and identify which cluster
forms the actual state machine. The `useReducer` is likely the right
end state.

## Special note: prior fix (PR #617 + followup)

This file received a 1-line `void Promise.allSettled(...)` hotfix in
PR #617 + proper `adminLogger.error/warn` calls in PR #620 (via
SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 followups). Those fixes are
present and tested. Don't regress them during the cleanup.

## Learnings to apply

- File-organization rule + ESLint rule
- DDD characterization tests, especially for the Promise.allSettled
  + error-state interactions
- `useReducer` is acceptable per project conventions (verify in
  ANALYZE)
- Live verification on production billing page (read-only is safe;
  no mandate-state mutation needed)
- **previous-deploy-failure-blocks-yours** retro pattern: this file
  was the source of that incident — extra caution on lint state
- scale-the-answer: own SPEC
- **CONCURRENT WORK COORDINATION** — verify PRICING-PER-USER-001 is
  fully merged before starting

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `.moai/specs/SPEC-PORTAL-PRICING-PER-USER-001/spec.md` (active —
  do not conflict)
- `.claude/rules/klai/pitfalls/process-rules.md` § "previous-deploy-failure-blocks-yours"
