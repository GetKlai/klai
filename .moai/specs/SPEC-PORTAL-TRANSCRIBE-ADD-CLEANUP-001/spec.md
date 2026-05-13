---
id: SPEC-PORTAL-TRANSCRIBE-ADD-CLEANUP-001
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

# SPEC-PORTAL-TRANSCRIBE-ADD-CLEANUP-001 — Split transcribe/add.tsx (530 lines, 12 useState, 4 useEffect)

## Goal

Reduce `klai-portal/frontend/src/routes/app/transcribe/add.tsx` from
530 lines to a route shell + extracted upload-form components + hooks.
Mid-tier god-component — high useState count + multiple useEffects
suggest a multi-mode form (record / upload / paste).

This SPEC is **draft**. Annotation cycle confirms exact sub-form
boundaries.

## Motivation metrics

| Metric | Value |
|---|---|
| File line count | 530 |
| useState | 12 |
| useEffect | 4 |
| Inline mutations | 3 |
| Git churn last 90 days | 22 commits |
| Last touched | 2 days ago at SPEC creation |
| Production-critical | Yes (Scribe upload entry point) |
| Existing colocation | `transcribe/_components/` already exists (e.g. TranscriptionTable.tsx) |

The `_components/` directory in this area is already used (precedent
in TranscriptionTable.tsx). Extension is the obvious next step.

## Scope (proposed)

### In

- New components in existing `transcribe/_components/`:
  - `<RecordingForm>`, `<FileUploadForm>`, `<PasteUrlForm>` (or
    whatever the actual upload modes are — verify in ANALYZE)
- New `transcribe/-add-hooks.ts`: 3 mutation hooks
- Modify `add.tsx`: route shell + mode-switcher + form composition

### Out

- Backend transcribe API changes
- Adding new upload modes

## Approach

DDD methodology. ANALYZE first: which 4 `useEffect`s do what?
Often `useEffect` count > 2 suggests state coordination that should
either be a reducer or moved into sub-components.

## Learnings to apply

- File-organization rule + ESLint rule
- `transcribe/_components/` already established — extend, don't
  proliferate
- DDD characterization tests cover each upload mode's happy path +
  error states
- Triplicate check vs `transcribe/_components/` exports
- Live verification on Voys (Scribe is per-user)
- scale-the-answer: own SPEC

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md`
- `klai-portal/frontend/src/routes/app/transcribe/_components/TranscriptionTable.tsx` —
  existing colocation precedent.
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers"
