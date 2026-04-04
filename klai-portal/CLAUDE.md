# klai-portal

## Deploy workflow

After every commit to klai-portal:

1. `git push`
2. `gh run watch --exit-status` — wait for the GitHub Action to complete
3. Verify server rollout — check bundle timestamp or container age on core-01

Never claim something is deployed before both CI is green AND the new code is confirmed on the server. Do not run `portal-deploy.sh` manually — the GitHub Action handles it.

## Key rules

- All UI components must come from `components/ui/` — never use raw `<button>`, `<input>`, `<select>` with inline Tailwind
- Form pages: `max-w-lg`, header `flex items-center justify-between mb-6` (title left, ghost cancel button right)
- Error text: `text-[var(--color-destructive)]` not `text-red-600`
- i18n: all UI strings via Paraglide (`import * as m from '@/paraglide/messages'`)
- Reference implementation: `frontend/src/routes/admin/users/invite.tsx`
