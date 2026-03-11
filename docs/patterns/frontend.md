# Frontend Patterns

> Copy-paste solutions for frontend projects (portal, website).

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

Form page (`routes/admin/users/invite.tsx`) — wrap in Card, navigate back on success/cancel:
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
      <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)] mb-6">
        Gebruiker uitnodigen
      </h1>
      <Card>
        <CardHeader>
          <CardTitle>Gebruiker uitnodigen</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* fields */}
            <div className="flex justify-end gap-3 pt-2">
              <Button variant="outline" onClick={() => navigate({ to: '/admin/users' })}>
                Annuleren
              </Button>
              <Button type="submit">Opslaan</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
```

**Why route-based over modal:**
- No overlay/focus-trap complexity
- Full page URL is shareable and bookmarkable
- Back button works naturally
- Consistent with how the rest of the admin navigation works

**Inline selects in tables** (compact role switcher):
```tsx
<Select
  value={user.role}
  onChange={(e) => handleRoleChange(e.target.value)}
  className="w-auto px-2 py-1 text-xs"
>
```

**Rules:**
- Never duplicate field classes across pages - always import from `components/ui/`
- Always use `htmlFor` on `<Label>` paired with `id` on the field
- `max-w-xs` is only for standalone selects (settings, account), not grid-form selects

---

## See Also

- [patterns/devops.md](devops.md) - Deployments, Docker
- [patterns/infrastructure.md](infrastructure.md) - Secrets, DNS, SSH
