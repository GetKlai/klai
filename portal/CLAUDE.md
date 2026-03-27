# klai-portal: Project Instructions

Project-specific instructions for the klai-portal monorepo (FastAPI backend + React/Vite frontend).

## Before writing any frontend code

Read these two documents first. They contain the component rules, form page patterns, and button alignment standards for klai-portal:

@../klai-claude/docs/patterns/frontend.md
@docs/ui-components.md

The reference implementation is `frontend/src/routes/admin/users/invite.tsx`.

## Deploy workflow

After every commit to klai-portal:

1. `git push`
2. `gh run watch --exit-status` — wait for the GitHub Action to complete
3. Verify server rollout — check bundle timestamp or container age on core-01

The Action `Build and deploy portal-frontend` runs automatically on every push to main, builds the Vite frontend, and rsyncs it to core-01. Never claim something is deployed before both CI is green AND the new code is confirmed on the server.

Full verification protocol (CI + server health check, cross-platform): `klai-claude/rules/klai/ci-verify-after-push.md`

Do not run `portal-deploy.sh` manually — the GitHub Action handles it.

## Stack

- **Backend:** FastAPI (Python), SQLAlchemy, Alembic, PostgreSQL
- **Frontend:** React + Vite + TanStack Router + TanStack Query, Paraglide i18n, shadcn-style `components/ui/`

## Key rules

- All UI components must come from `components/ui/` (`<Button>`, `<Input>`, `<Label>`, `<Select>`, `<Card>`)
- Never use raw `<button>`, `<input>`, `<label>`, `<select>` with inline Tailwind in pages
- Form pages: `max-w-lg`, header `flex items-center justify-between mb-6` (title left, ghost cancel button right)
- Error text: `text-[var(--color-destructive)]` not `text-red-600`
- i18n: all UI strings go through Paraglide (`import * as m from '@/paraglide/messages'`)
