# Portal Frontend: UI Standards

**[HARD] Read these rules before editing any `.tsx` or `.ts` file in this directory.**

This applies to ALL agents (main session and subagents). No exceptions.

---

## Page layout standard

Reference implementation: `portal/frontend/src/routes/admin/users/invite.tsx`

Every page:
```tsx
<div className="p-8 max-w-lg">           {/* or max-w-3xl for wide pages */}
  <div className="flex items-center justify-between mb-6">
    <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
      Page Title
    </h1>
    <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
      <ArrowLeft className="h-4 w-4 mr-2" />
      {m.some_back_label()}
    </Button>
  </div>
  {/* content */}
</div>
```

Rules:
- **h1 LEFT, back button RIGHT** — always in one `flex items-center justify-between` row
- Never put the back button above the h1 in a separate block
- Never put the back button bottom-left or in a footer
- `font-serif text-2xl font-bold text-[var(--color-purple-deep)]` on every h1

---

## Components: always use `components/ui/`

```tsx
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
```

Never use raw `<input>`, `<select>`, `<button>` with inline Tailwind in route files.

---

## Color tokens

Never use raw Tailwind color classes (`text-red-500`, `bg-green-*`). Always use CSS variables:

| Token | Use |
|---|---|
| `var(--color-purple-deep)` | Headings, primary text |
| `var(--color-muted-foreground)` | Secondary text, placeholders |
| `var(--color-destructive)` | Errors, delete actions |
| `var(--color-success)` | Save confirm buttons |
| `var(--color-border)` | Borders, dividers |

---

## Full references

- Component details + code examples: `portal/docs/ui-components.md`
- Color system + typography: `.claude/rules/klai/klai-ui-styleguide.md`
- Form patterns + table patterns: `.claude/rules/klai/klai-portal-ui.md`
