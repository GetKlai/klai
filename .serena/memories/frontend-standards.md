# Frontend Standards

## Portal Frontend (klai-portal/frontend)
- **Stack:** React + TypeScript + Vite + TanStack Router + TanStack Query
- **UI Components:** shadcn-style components in `src/components/ui/`
- **i18n:** Paraglide (`import * as m from '@/paraglide/messages'`)
- **Languages:** nl + en

### Hard Rules (from klai-portal/CLAUDE.md)
- ALL UI strings through Paraglide — never hardcode text
- ONLY use `components/ui/` components: `<Button>`, `<Input>`, `<Label>`, `<Select>`, `<Card>`
- NEVER raw `<button>`, `<input>` etc. with inline Tailwind in pages
- Form pages: `max-w-lg`, header `flex items-center justify-between mb-6`
- Error text: `text-[var(--color-destructive)]` NOT `text-red-600`
- Reference implementation: `frontend/src/routes/admin/users/invite.tsx`

### Route Structure
```
routes/
  __root.tsx         # Root layout
  index.tsx          # Landing/redirect
  login.tsx
  signup.tsx
  callback.tsx       # OIDC callback
  verify.tsx
  provisioning.tsx
  logged-out.tsx
  app/               # Authenticated area
    index.tsx
    chat.tsx
    scribe.tsx
    account.tsx
    meetings/
    knowledge/
    docs/
    focus/
    transcribe/
  admin/             # Admin area
  setup/
  password/
  $locale/           # i18n locale prefix
```

### Deploy
- GitHub Action `Build and deploy portal-frontend` runs on push to main
- Builds Vite, rsyncs to core-01
- Always run `gh run watch --exit-status` after push — NEVER declare deployed without this

## Website (klai-website)
- **Stack:** Astro 5, TypeScript strict, Tailwind CSS v4
- **CMS:** Keystatic (git-based, no database)
- **Components:** Magic UI (free/MIT)
- **i18n:** Astro built-in routing (/nl/... and /en/...), fallback nl→en
- **Brand colors:** `--purple-primary: #2D1B69`, `--purple-accent: #7C6AFF`, `--sand-light: #F5F0E8`
- **Fonts:** Libre Baskerville (headings), Manrope (display), Inter (body)
- **Deploy:** Coolify on public-01 (Hetzner CX42)
- **DNS:** Cloud86 (ns1/ns2.cloud86.nl, ns3.cloud86.eu)

### Website Rules
- Minimal changes only
- No em dashes (--)
- Fixed-width containers (prevent layout shifts)
- Never display:none/block for content switching
- Test with Playwright
