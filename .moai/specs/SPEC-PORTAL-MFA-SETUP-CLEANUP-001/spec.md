---
id: SPEC-PORTAL-MFA-SETUP-CLEANUP-001
version: 0.1.1
status: done
created: 2026-05-13
completed: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (god-component § Follow-ups, carved out)
related: []
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-MFA-SETUP-CLEANUP-001 — Refactor setup/mfa.lazy.tsx (668 lines, 19 useState — worst structure in repo)

## Goal

Reduce `klai-portal/frontend/src/routes/setup/mfa.lazy.tsx` from
668 lines + 19 useState to a state-machine-driven multi-step setup
flow. **Highest useState count in the entire frontend** — strong
signal that the file needs `useReducer` consolidation, not just
component extraction.

This SPEC is **draft**. Annotation cycle MUST first map the 19
useState slots into a state machine before any refactor begins.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 668 |
| useState | **19 (highest in entire frontend)** |
| useEffect | 3 |
| Inline mutations | 0 |
| Git churn last 90 days | 16 commits (low — file is stable) |
| Last touched | 2 days ago at SPEC creation |
| Production-critical | Yes (MFA setup — security-critical) |

The LOW churn signals that the file is stable but **structurally
worst**. This is "stable pain" — works in production, but every time
someone touches it they hit cognitive load. Worth fixing because the
fix lasts (low rate of re-emerging god-state).

19 useState in one component is almost certainly NOT 19 independent
flags. It's a state machine pretending to be flags. ANALYZE phase
should produce a state-transition diagram; the refactor turns it
into a `useReducer` or XState machine.

## Scope (proposed — annotation cycle MUST decide state-machine pattern first)

### In

- ANALYZE deliverable: state-transition diagram for the 19 useStates
  (which combinations are valid, which are mutually exclusive)
- Refactor to `useReducer` (most likely) or XState (if state-transition
  count justifies the dependency)
- Per-step sub-components in `setup/_components/`:
  - `<MfaIntroStep>`, `<TotpEnrollStep>`, `<RecoveryCodesStep>`,
    `<ConfirmStep>` (exact steps TBD)
- Modify `mfa.lazy.tsx`: route shell + state machine + step
  composition

### Out

- Backend MFA / TOTP API changes
- Adding new MFA factors (WebAuthn, SMS — separate feature SPECs)
- Changing security model

## Approach

DDD methodology. **ANALYZE phase is the most important phase here**:
the 19 useState reflect undocumented business logic. Get the state
machine right BEFORE writing any refactor commit.

PRESERVE phase: characterization tests for every MFA setup path
(happy path, recovery code regeneration, abort mid-flow, etc.). MFA is
security-critical — behavior preservation is non-negotiable.

IMPROVE phase: incremental.

## Special note: security-critical area

MFA setup is in the auth perimeter. Any refactor needs:
- Security review (delegate to expert-security or klai-security-audit
  agent post-refactor)
- Live verification on a NON-PRODUCTION tenant first if possible
- Explicit user acknowledgment in PR description that the refactor is
  behavior-preserving

## Learnings to apply

- File-organization rule + ESLint rule
- DDD methodology with comprehensive characterization tests
  (security-critical = test coverage matters)
- `useReducer` consolidation pattern (annotation cycle decides if
  XState is justified — likely overkill for one form)
- Live verification on production after deploy (test setup flow with
  a real user account)
- **Security review** post-refactor (klai-security-audit agent)
- scale-the-answer: own SPEC
- previous-deploy-failure-blocks-yours: setup/ files are deploy-
  triggers, double-check main CI before pushing

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `.moai/specs/SPEC-AUTH-TOTP-POPORDER-001` (related TOTP work — verify
  no regression of fixes shipped there)
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
- `.claude/rules/klai/projects/portal-security.md` (if exists, for
  auth-perimeter conventions)
