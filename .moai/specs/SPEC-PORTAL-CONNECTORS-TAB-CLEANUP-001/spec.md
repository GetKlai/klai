---
id: SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001
version: 0.1.0
status: draft
created: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related:
  - SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (this is the SAME area — connectors tab is the list view, the wizard pages are add/edit)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-CONNECTORS-TAB-CLEANUP-001 — Borderline cleanup of $kbSlug/connectors.tsx (425 lines)

## Goal

Reduce `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/connectors.tsx`
from 425 lines to a route shell + extracted row-component(s) + hooks.
This SPEC's status is **borderline** — 425 lines is close to the
threshold where extraction becomes overkill. High git churn (29
commits / 90 days) is the main argument FOR doing it.

This SPEC is **draft**. Annotation cycle should reconsider whether to
proceed at all (acceptable to mark as "won't fix" if the file's
internal structure turns out to be reasonable for its size).

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 425 |
| useState | 8 |
| useEffect | 2 |
| Inline mutations | 5 |
| Inline queries | (TBD) |
| Git churn last 90 days | 29 commits (#3 across all candidates) |
| Last touched | 11 hours ago at SPEC creation |
| Production-critical | Yes (per-KB connector management) |

The high churn is the strongest signal. Many edits = many cognitive
hits on the file's structure. But 425 lines is on the lower end of the
god-component spectrum — if internal structure is already reasonable
(separate concerns within the file), this SPEC may not pay off.

## Scope (proposed — annotation cycle decides whether to proceed)

### In (proposed)

- New `_components/ConnectorRow.tsx`: per-row affordances if currently
  inlined in the table render
- New `-connectors-tab-hooks.ts`: 5 mutation hooks (sync, delete,
  reauth, etc.)
- Modify `connectors.tsx`: route shell + table assembly

### Out

- Backend connector changes
- Wizard pages (those are SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001
  territory — already done)
- New functionality

## Approach

DDD methodology. Annotation cycle MUST first ANALYZE whether the file
is actually god-shaped or just "long but OK". Recommended ANALYZE
output:
- Is each useState used in one place or scattered?
- Are mutations grouped by concern or interleaved?
- Is the JSX render path cohesive or branching wildly?

If ANALYZE shows the file is internally cohesive → mark this SPEC as
**won't fix** and update the parent SPEC's § Follow-ups.
If ANALYZE shows real god-component patterns → proceed with extraction
phases mirroring SPEC-PORTAL-TAXONOMY-EXTRACT-001 / -SPLIT-001.

## Learnings to apply

Same as SPEC-PORTAL-ADMIN-USERS-CLEANUP-001:
- File-organization rule + ESLint rule already in place
- DDD methodology with characterization tests
- Triplicate check vs `-kb-helpers.tsx` and `-kb-types.ts` (this file
  IS in the same dir — high overlap probability with SyncStatusBadge,
  ConnectorSummary, etc.)
- Live verification on Voys
- scale-the-answer: own SPEC, no bundling

## Special note: cross-reference with existing -kb-helpers

`connectors.tsx` already imports `SyncStatusBadge` from `-kb-helpers.tsx`
and `ConnectorSummary` from `-kb-types.ts` (verified during
SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 audit). Any new extraction
must check whether the new symbols also belong in these existing
shared files OR a new connector-tab-specific `-`-prefixed file.

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
