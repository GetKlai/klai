# SPEC-PORTAL-KB-NEW-CLEANUP-001 — Progress

## Status: done (2026-05-13)

## Implementation

| PR | Description |
|---|---|
| #630 | [codex] Split KB creation wizard |

The 713-line `knowledge/new.tsx` route file was split into per-step
components:

| New file | Role |
|---|---|
| `new._components/-StepName.tsx` | Step 1 — name + description |
| `new._components/-StepAccess.tsx` | Step 2 — visibility / access control |
| `new._components/-StepPermissions.tsx` | Step 3 — role assignment |
| `new._components/-StepConfirm.tsx` | Step 4 — review + submit |
| `new._wizard-hooks.ts` | Mutation hooks for KB creation |
| `__tests__/-new-wizard-hooks.test.ts` | Unit tests for hooks |

Plus type additions in existing `new._types.ts`. `new.tsx` itself
shrank from 633 lines to ~40 (route shell).

## Outstanding

This progress.md is a **post-hoc summary** added during the
2026-05-13 batch cleanup. The implementation PR did not include
its own progress.md. Live verification on Voys was not done by me —
add browser interactive E2E if a future SPEC depends on the wizard
behaviour.

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  parent SPEC.
