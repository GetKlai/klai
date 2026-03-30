---
paths: "**/*.tsx,**/*.ts,**/*.css,**/*.astro"
---
# Frontend Patterns

> Copy-paste solutions for frontend projects (portal, website).

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use |
|---|---|
| [i18n-paraglide](#i18n-paraglide) | Setting up or adding translations in React/Vite |
| [portal-ui-components](#portal-ui-components) | Using `<Button>`, `<Input>`, `<Select>`, `<Card>` components |
| [separation-of-concerns](#separation-of-concerns) | Deciding where styling, logic, data, and components belong |
| [logging](#logging) | Setting up structured logging with consola + Sentry |
| [playwright-mcp](#playwright-mcp) | Browser automation for E2E spot-checks via MCP |

---

## i18n-paraglide

**Scope:** klai-portal (and future frontend projects)

**Decision:** We use [Paraglide JS](https://inlang.com/m/gerre34r/library-inlang-paraglideJs) (`@inlang/paraglide-js`) for internationalization.

**Why Paraglide over react-i18next:**
- Compiler-based: translations become tree-shakable functions — only used strings ship
- Full TypeScript type safety: wrong key or missing parameter = build error, not `undefined` in production
- Official TanStack Router support with dedicated examples
- Native Vite plugin, zero runtime overhead
- ~2KB vs ~40KB bundle footprint for i18n

**Current language coverage:**
- `klai-portal` signup: NL (default) + EN. Will expand to DE and others.
- `klai-portal` admin: EN first, other languages later.

---

### Setup (React + Vite + TanStack Router)

**1. Install**
```bash
npm install @inlang/paraglide-js
```

**2. `project.inlang/settings.json`** (at frontend root, next to `vite.config.ts`)
```json
{
  "$schema": "https://inlang.com/schema/project-settings",
  "sourceLanguageTag": "en",
  "languageTags": ["en", "nl"],
  "modules": [
    "https://cdn.jsdelivr.net/npm/@inlang/plugin-message-format@latest/dist/index.js",
    "https://cdn.jsdelivr.net/npm/@inlang/plugin-m-function-matcher@latest/dist/index.js"
  ],
  "plugin.inlang.messageFormat": {
    "pathPattern": "./messages/{languageTag}.json"
  }
}
```

**3. `vite.config.ts`** — plugin must be **first** in the plugins array
```ts
import { paraglideVitePlugin } from '@inlang/paraglide-js'

paraglideVitePlugin({
  project: './project.inlang',
  outdir: './src/paraglide',
  emitTsDeclarations: true,  // required: project uses strict TS without allowJs
}),
```

**4. `package.json`** — compile before dev/build to ensure files exist before Vite resolves imports
```json
"i18n:compile": "paraglide-js compile --project ./project.inlang --outdir ./src/paraglide",
"dev": "npm run i18n:compile && vite",
"build": "npm run i18n:compile && tsc -b && vite build",
```

**5. `.gitignore`** — generated output, do not commit
```
src/paraglide/
```

**5. Message files** — `messages/{lang}.json`
```json
{
  "my_key": "Hello {name}!",
  "simple_key": "Static text"
}
```
Source language is `en`. All other languages must have the same keys.

**6. Usage in components**
```tsx
import * as m from '@/paraglide/messages'
import { setLocale } from '@/paraglide/runtime'

// Set default locale at module level (before component)
setLocale('nl')

// In component
m.my_key({ name: 'World' })  // typed — TS error if param missing
m.simple_key()
```

**7. Language switcher pattern** (local state drives re-render)
```tsx
type Locale = 'nl' | 'en'
const [locale, setLocaleState] = useState<Locale>('nl')

function switchLocale(l: Locale) {
  setLocale(l)       // updates Paraglide runtime
  setLocaleState(l)  // triggers React re-render
}
```

---

### Adding a new language

1. Add the language tag to `project.inlang/settings.json` → `languageTags`
2. Create `messages/{lang}.json` with all existing keys translated
3. Add the locale to the `Locale` type in affected components
4. Add the toggle button to the language switcher

### Naming conventions for message keys

- Prefix with page/feature: `signup_`, `admin_`, `billing_`
- Snake case throughout
- Descriptive, not positional: `signup_field_email` not `signup_label_3`

### Key constraints

- No dynamic key construction: `m['key_' + variable]` does not work (compiler needs static analysis)
- Parameters in messages use `{param}` syntax and are always typed as `string`

---

## portal-ui-components

**Scope:** klai-portal (`frontend/src/components/ui/`)

**Decision:** Portal uses owned, copy-paste shadcn-style components. No Radix UI dependency. All form primitives are wrapped so styling is defined in one place.

**Available components:**

| Component | File | Use for |
|---|---|---|
| `Input` | `components/ui/input.tsx` | Text, email, password fields |
| `Label` | `components/ui/label.tsx` | Field labels (always pair with `htmlFor`) |
| `Select` | `components/ui/select.tsx` | Native `<select>` dropdowns |
| `Button` | `components/ui/button.tsx` | Actions |
| `Card` + sub-components | `components/ui/card.tsx` | Content sections |

**Button variants** (`variant` prop — defined in `button.tsx`):

| Variant | When to use | Example |
|---|---|---|
| `default` (primary) | Primary action on a page (submit, save, invite) | `<Button>Opslaan</Button>` |
| `outline` | Secondary action alongside a primary | `<Button variant="outline">Bewerken</Button>` |
| `ghost` | Low-priority action, nav items, icon-only buttons | `<Button variant="ghost"><ArrowLeft /></Button>` |
| `destructive` | Confirmed destructive action (e.g., AlertDialog confirm) | `<Button variant="destructive">Verwijderen</Button>` |
| `secondary` | Neutral alternative to outline (less border-heavy) | `<Button variant="secondary">Annuleren</Button>` |
| `link` | Inline text link styled as button | `<Button variant="link">Meer info</Button>` |

**Button sizes** (`size` prop):

| Size | Height | Use for |
|---|---|---|
| `default` | 40px | Standard form actions |
| `sm` | 32px | Table rows, compact UI, inline confirm |
| `lg` | 48px | Hero CTAs, onboarding |
| `icon` | 40×40px | Icon-only buttons (always add `aria-label`) |

**Overriding for semantic states** — when a variant's visual doesn't match the semantic meaning, override with `className`:
```tsx
{/* Destructive confirm button in AlertDialog */}
<AlertDialogAction className="bg-[var(--color-destructive)] text-white hover:opacity-90">
  Verwijderen
</AlertDialogAction>

{/* Positive confirm button */}
<Button className="bg-[var(--color-success)] text-white hover:opacity-90">
  Bevestigen
</Button>
```

**Field pattern** (label + input/select):
```tsx
<div className="space-y-1.5">
  <Label htmlFor="field-id">Label text</Label>
  <Input id="field-id" type="text" value={value} onChange={...} />
</div>
```

**Standalone select** (settings/account - constrain width explicitly):
```tsx
<div className="space-y-1.5">
  <Label htmlFor="language">Taal</Label>
  <Select id="language" value={lang} onChange={...} className="max-w-xs">
    <option value="nl">Nederlands</option>
    <option value="en">English</option>
  </Select>
</div>
```

**Add/invite form** (always a separate route page, never a modal):

List page — navigate to the form route on button click:
```tsx
<Button onClick={() => navigate({ to: '/admin/users/invite' })}>
  {m.admin_users_invite_button()}
</Button>
```

Form page (`routes/admin/users/invite.tsx`) — page header with title left and ghost cancel button right; Card has no CardHeader:
```tsx
export const Route = createFileRoute('/admin/users/invite')({
  component: InviteUserPage,
})

function InviteUserPage() {
  const navigate = useNavigate()

  const inviteMutation = useMutation({
    mutationFn: async (data) => { /* POST /api/admin/users/invite */ },
    onSuccess: () => navigate({ to: '/admin/users' }),
  })

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          Gebruiker uitnodigen
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={() => navigate({ to: '/admin/users' })}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          Annuleren
        </Button>
      </div>
      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* fields */}
            {inviteMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {inviteMutation.error.message}
              </p>
            )}
            <div className="pt-2">
              <Button type="submit" disabled={inviteMutation.isPending}>
                {inviteMutation.isPending ? 'Bezig...' : 'Uitnodigen'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
```

Key layout rules:
- Page header: `flex items-center justify-between mb-6` — title left, ghost cancel/back button right with ArrowLeft icon
- Card: `<Card><CardContent className="pt-6">` — no CardHeader/CardTitle inside the card (the page h1 serves as title)
- Single submit: `<div className="pt-2"><Button>` — left-aligned, cancel is in the page header
- Multiple result buttons: `<div className="flex justify-end gap-3 pt-2">` — secondary (outline) left, primary right
- Error text: `text-sm text-[var(--color-destructive)]` — never `text-red-600`
- Success text / positive feedback icons: `text-[var(--color-success)]` — never `text-green-500`
- Confirm buttons (destructive action): `bg-[var(--color-destructive)] text-white hover:opacity-90`
- Confirm buttons (save/positive action): `bg-[var(--color-success)] text-white hover:opacity-90`

**Semantic color tokens** (defined in `frontend/src/index.css`):

| Token | Value | Use for |
|---|---|---|
| `--color-purple-deep` | `#1A0F40` | Headings, primary text, active icons |
| `--color-muted-foreground` | `#6B6B6B` | Secondary text, placeholder, muted icons |
| `--color-destructive` | `#C0392B` | Error text, delete confirm buttons, destructive actions |
| `--color-success` | `#27AE60` | Save confirm buttons, positive feedback icons |
| `--color-border` | `rgba(45,27,105,0.1)` | Borders, dividers |
| `--color-accent` | `#7C6AFF` | Focus rings, links, accent highlights |

Never use raw Tailwind color classes (`text-red-600`, `bg-green-500`, etc.) for semantic states — always use the token.

**Why route-based over modal:**
- No overlay/focus-trap complexity
- Full page URL is shareable and bookmarkable
- Back button works naturally
- Consistent with how the rest of the admin navigation works

**Icon action buttons in tables:**

Always use raw `<button>` — never `<Button variant="ghost">` for icon-only actions in tables.

Two variants:

*Ghost (default — transparent background):*
```tsx
<button
  onClick={...}
  aria-label="..."
  className="flex h-7 w-7 items-center justify-center text-[var(--color-X)] transition-opacity hover:opacity-70"
>
  <SomeIcon className="h-3.5 w-3.5" />
</button>
```

| Action | Token | Icon |
|---|---|---|
| Edit / rename | `--color-warning` | `Pencil` |
| View / detail / copy | `--color-accent` | `Eye`, `Copy`, `CheckCheck` |
| Delete / remove | `--color-destructive` | `Trash2` |
| Download / positive | `--color-success` | `Download` |
| Overflow menu | `--color-muted-foreground` + `hover:bg-[var(--color-secondary)]` | `MoreHorizontal` |

*Filled confirm (shown after first click on inline tier-1 confirmation):*
```tsx
// Confirm destructive
<button className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-destructive)] text-white transition-colors hover:opacity-90">
  <Check className="h-3.5 w-3.5" />
</button>
// Confirm save
<button className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-success)] text-white transition-colors hover:opacity-90">
  <Check className="h-3.5 w-3.5" />
</button>
// Cancel
<button className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-border)]">
  <X className="h-3.5 w-3.5" />
</button>
```

**Avatar colors** (decorative differentiation — raw Tailwind allowed per "not semantic states" rule):
```tsx
const AVATAR_COLORS = [
  'bg-purple-100 text-purple-700',
  'bg-blue-100 text-blue-700',
  'bg-green-100 text-green-700',
  'bg-amber-100 text-amber-700',
  'bg-rose-100 text-rose-700',
]
function avatarColor(uid: string): string {
  const hash = uid.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0)
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}
```

**Inline selects in tables** (compact role switcher):
```tsx
<Select
  value={user.role}
  onChange={(e) => handleRoleChange(e.target.value)}
  className="w-auto px-2 py-1 text-xs"
>
```

**Delete confirmation patterns:**

Three tiers — pick based on the severity and reversibility of the action:

| Tier | When | Pattern |
|---|---|---|
| **Inline** | Small, reversible (remove row from list, remove group from user) | Red trash icon → click reveals text buttons "Verwijder" + "Annuleren" |
| **AlertDialog** | Irreversible account actions (suspend, offboard) | Modal with description + destructive button; no typing required |
| **Modal + type name** | Permanent data destruction (delete knowledge base, delete org) | Modal requiring the user to type the resource name to unlock confirm |

Inline confirm always uses **text buttons** (not icons) for the confirmation step — text is unambiguous.
The trigger is always a red trash icon: `<Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />`.

Example — inline pattern (table row):
```tsx
const [confirmId, setConfirmId] = useState<number | null>(null)

// In the row:
{confirmId === item.id ? (
  <div className="flex items-center gap-1">
    <Button
      size="sm"
      className="bg-[var(--color-destructive)] text-white hover:opacity-90"
      onClick={() => { deleteMutation.mutate(item.id); setConfirmId(null) }}
    >
      Verwijderen
    </Button>
    <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
      Annuleren
    </Button>
  </div>
) : (
  <Button size="sm" variant="ghost" onClick={() => setConfirmId(item.id)}>
    <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
  </Button>
)}
```

**Rules:**
- Never duplicate field classes across pages - always import from `components/ui/`
- Always use `htmlFor` on `<Label>` paired with `id` on the field
- `max-w-xs` is only for standalone selects (settings, account), not grid-form selects

---

## separation-of-concerns

**Scope:** klai-portal frontend

**Decision:** Four rules covering styling, logic, data fetching, and component size.

### 1. Styling — alleen Tailwind className, nooit inline style

Use Tailwind `className` for all fixed styling. `style={{}}` is only allowed for truly runtime-dynamic values that Tailwind cannot express (e.g. a calculated pixel width from JS state).

```tsx
// WRONG — vaste waarden horen niet in style={{}}
<p style={{ fontSize: '0.75rem', color: 'var(--color-muted-foreground)' }}>

// CORRECT
<p className="text-xs text-[var(--color-muted-foreground)]">
```

Never use raw Tailwind color classes for **semantic states** — always use CSS tokens. Exception: purely decorative colors (e.g. random avatar background colors for visual differentiation) may use raw Tailwind:

```tsx
// WRONG
<p className="text-red-600">Fout</p>
<div className="bg-green-100 text-green-700">Actief</div>

// CORRECT
<p className="text-[var(--color-destructive)]">Fout</p>
```

For status badges with multiple states, define a lookup map with token-based classes:
```tsx
const STATUS_CLASSES: Record<string, string> = {
  pending:   'bg-[var(--color-sand-mid)] text-[var(--color-purple-deep)]',
  active:    'bg-[var(--color-success)]/10 text-[var(--color-success)]',
  failed:    'bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]',
}
```

### 2. Business logica — in hooks, niet in components

Logic unrelated to rendering belongs in custom hooks (`src/hooks/useXxx.ts`). A component's job is: receive data → render UI → forward events.

Extract to a hook when:
- The logic would be useful in more than one component
- The component has >~50 lines of JS alongside its JSX
- The logic involves side effects, timers, or complex state transformations

```
src/hooks/useUserLifecycle.ts  ✓ al aanwezig
src/hooks/useUsers.ts          → user CRUD + membership queries
src/hooks/useGroups.ts         → group queries + mutations
```

Keep in the component when: the logic is a few lines and clearly component-specific.

### 3. Data fetching — inline in queryFn, geen service-laag

`useQuery` en `useMutation` staan direct in de routecomponent of in een custom hook. De `fetch()` call staat inline in `queryFn` met `API_BASE` en de auth-header. Er is geen aparte service-laag.

**Waarom geen service-laag:** de overhead (extra bestanden, extra abstractie-laag) weegt op deze schaal niet op tegen het voordeel. De fetch-calls zijn volledig transparant en de auth-structuur is stabiel.

```tsx
// CORRECT — inline in component
const { data } = useQuery({
  queryKey: ['admin-users', token],
  queryFn: async () => {
    const res = await fetch(`${API_BASE}/api/admin/users`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) throw new Error(`${res.status}`)
    return res.json() as Promise<{ users: User[] }>
  },
  enabled: !!token,
})
```

Extract naar een custom hook alleen als dezelfde query op meerdere pagina's wordt hergebruikt:

```tsx
// src/hooks/useUsers.ts — alleen als >1 component dezelfde query gebruikt
export function useUsers(token: string) {
  return useQuery({
    queryKey: ['admin-users', token],
    queryFn: async () => { ... },
    enabled: !!token,
  })
}
```

### 4. Componentgrootte — één verantwoordelijkheid

A route component owns: data fetching context + page layout. It does not own large blocks of JSX for each visual section.

Split into sub-components when a visual section exceeds ~50 lines of JSX. Sub-components may live in the same file if they are small and not reused elsewhere, or in a `_components/` subfolder next to the route file if larger.

```tsx
// route file stays thin:
function UsersPage() {
  const { data } = useUsers()
  return (
    <div className="p-8">
      <UsersTable users={data?.users ?? []} />
    </div>
  )
}
```

---

## logging

**Scope:** klai-portal frontend

**Decision:** `consola` + `Sentry.createConsolaReporter()` for structured, environment-aware logging.

### Why

- `@sentry/react ^10.43.0` already installed — `createConsolaReporter()` is a native one-liner
- Environment-aware: all levels in dev, warn/error/fatal forwarded to Sentry in prod
- Per-module tagging via `withTag()` — no boilerplate
- No raw `console.log` in application code

### Setup (already done in klai-portal)

`frontend/src/lib/logger.ts` — the single source for all loggers:

```ts
import { createConsola } from 'consola/browser'
import * as Sentry from '@sentry/react'

const logger = createConsola({
  level: import.meta.env.DEV ? 4 : 1, // 4=debug in dev, 1=warn in prod
})

if (!import.meta.env.DEV) {
  logger.addReporter(Sentry.createConsolaReporter())
}

export const authLogger   = logger.withTag('auth')
export const editorLogger = logger.withTag('editor')
export const queryLogger  = logger.withTag('query')
export const treeLogger   = logger.withTag('tree')
```

`frontend/src/main.tsx` — Sentry init includes:
```ts
Sentry.init({
  enableLogs: true,
  integrations: [
    Sentry.consoleLoggingIntegration({ levels: ['warn', 'error'] }),
  ],
})
```

### How to use (for every new file)

```ts
// Import the right tagged logger — NEVER use console.log directly
import { editorLogger } from '@/lib/logger'

editorLogger.debug('Parsing content', { format, length })  // dev only, never reaches Sentry
editorLogger.info('Page saved', { path, ms })              // business-significant action
editorLogger.warn('Page index empty')                       // recoverable issue
editorLogger.error('Save failed', { path, status })        // user-facing failure
```

### Which logger to use where

| Logger | Use for |
|---|---|
| `authLogger` | Token refresh, session expiry, login/logout |
| `editorLogger` | Content load/save, wikilink insert, format detection |
| `queryLogger` | Cache misses, fetch errors, stale data |
| `treeLogger` | DnD events, drop target, tree mutations |

Add a new `logger.withTag('name')` export for new domains — keep it in `lib/logger.ts`.

### Rules

- **Never** use `console.log` in application code — always use the logger
- **Never** export the root `logger` — always use a tagged sub-logger
- `debug` is free to use liberally in dev; it never ships to Sentry
- `warn`/`error` go to Sentry in production — write them with context objects, not string concatenation
- When debugging a bug: add `logger.debug(...)` calls first, reproduce, then fix. Remove noisy debug calls before committing if they add no long-term value.

---

---

## playwright-mcp — Browser Automation via MCP

**Scope:** All klai projects (available in every Claude Code session)

The `playwright` MCP server is configured in `.mcp.json` at the monorepo root. It gives Claude direct browser control via Brave (separate profile, so it never interferes with your active Brave session).

**When to use:**
- Manual E2E spot-checks during development (navigate, click, screenshot)
- Verify a deployed feature looks and works correctly
- Debug UI issues that are hard to reproduce from code alone
- Quick smoke test after a deploy

**How agents use it:**

The MCP exposes tools like `browser_navigate`, `browser_click`, `browser_screenshot`, `browser_type`. Agents invoke these directly — no test file needed.

Example prompt: _"Open https://portal.klai.nl/signup and screenshot the form"_

**Session behavior:**
- Login state **persists** between Claude sessions (dedicated profile at `~/.claude/mcp-brave-profile`)
- Browser opens visibly (headless: false) — you can watch what Claude does
- Each MCP server instance owns one browser; simultaneous Claude sessions each get their own instance

**Not for:**
- Automated regression suites → use Playwright test files (`npm run test:e2e`)
- CI pipelines → no display available by default

**Config location:** [.mcp.json](/.mcp.json) — `playwright` server entry

---

## See Also

- [patterns/testing.md](testing.md) - Playwright browser testing workflow
- [patterns/devops.md](devops.md) - Deployments, Docker
- [patterns/infrastructure.md](infrastructure.md) - Secrets, DNS, SSH
