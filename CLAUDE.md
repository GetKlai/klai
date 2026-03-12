# klai-portal: Project Instructions

Project-specific instructions for the klai-portal monorepo (FastAPI backend + React/Vite frontend).

## Before writing any frontend code

Read these two documents first. They contain the component rules, form page patterns, and button alignment standards for klai-portal:

@klai-claude/docs/patterns/frontend.md
@klai-portal/docs/ui-components.md

The reference implementation is `klai-portal/frontend/src/routes/admin/users/invite.tsx`.

## Stack

- **Backend:** FastAPI (Python), SQLAlchemy, Alembic, PostgreSQL
- **Frontend:** React + Vite + TanStack Router + TanStack Query, Paraglide i18n, shadcn-style `components/ui/`

## Key rules

- All UI components must come from `components/ui/` (`<Button>`, `<Input>`, `<Label>`, `<Select>`, `<Card>`)
- Never use raw `<button>`, `<input>`, `<label>`, `<select>` with inline Tailwind in pages
- Form pages: `max-w-lg`, header `flex items-center justify-between mb-6` (title left, ghost cancel button right)
- Error text: `text-[var(--color-destructive)]` not `text-red-600`
- i18n: all UI strings go through Paraglide (`import * as m from '@/paraglide/messages'`)
