---
paths: ["klai-portal/frontend/src/**/*.tsx", "klai-portal/frontend/src/**/*.ts"]
---

# Portal Patterns

> Portal-specific design tokens and component patterns.
> Shared brand DNA (colors, typography roles, rules): auto-loaded via `design/styleguide.md`.

---

## Semantic tokens (shadcn)

| Token | Value | Notes |
|---|---|---|
| `--color-background` | `#FAFAF8` | Page background |
| `--color-foreground` | `#1A1A1A` | Default text |
| `--color-card` | `#FFFFFF` | Card backgrounds |
| `--color-primary` | `#2D1B69` | shadcn primary (purple-primary) |
| `--color-primary-foreground` | `#FAFAF8` | Text on primary |
| `--color-secondary` | `#F5F0E8` | shadcn secondary (sand-light) |
| `--color-secondary-foreground` | `#2D1B69` | Text on secondary |
| `--color-muted` | `#EAE3D5` | Muted backgrounds |
| `--color-muted-foreground` | `#6B6B6B` | Muted text |
| `--color-accent` | `#7C6AFF` | Accent (purple-accent) |
| `--color-accent-foreground` | `#FAFAF8` | Text on accent |
| `--color-destructive` | `#C0392B` | Error / destructive actions |
| `--color-success` | `#27AE60` | Save confirm buttons, positive feedback icons |
| `--color-border` | `rgba(45,27,105,0.1)` | Default border |
| `--color-input` | `rgba(45,27,105,0.08)` | Input field background |
| `--color-ring` | `#7C6AFF` | Focus ring |

---

## Sidebar (dark variant)

```
Background:         #1A0F40  (--color-sidebar)
Text:               #F5F0E8  (--color-sidebar-foreground)
Border:             rgba(124, 106, 255, 0.15)
Active/hover item:  rgba(124, 106, 255, 0.15) background
Muted text:         rgba(245, 240, 232, 0.55)
```

---

## Typography sizes

### Inter (sans) - portal defaults

| Context | Size | Weight |
|---|---|---|
| Base body | 14px (`--text-base: 0.875rem`) | 400 |
| Lead/intro text | `text-lg` (18px) | 400 |
| Nav links | `text-sm` | 400 |
| Captions / metadata | `text-xs` (10-12px) | 400 |
| Labels in UI | `text-sm` | 500 (`font-medium`) |

Line height: `leading-relaxed` (1.625). Anti-aliasing: `-webkit-font-smoothing: antialiased`.

### Manrope (display) - prices and stats

| Context | Size | Weight |
|---|---|---|
| Price display | `text-3xl` (30px) | 700 |
| Large stats | `text-4xl`+ | 700 or 800 |

---

## Form fields

Always use the shared primitives from `components/ui/`:

```tsx
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
```

Field pattern:
```tsx
<div className="space-y-1.5">
  <Label htmlFor="field-id">Label text</Label>
  <Input id="field-id" type="text" ... />
</div>
```

- Label color: `var(--color-purple-deep)`, `block text-sm font-medium`
- Field text color: `var(--color-purple-deep)`
- Border: `var(--color-border)`, `rounded-md`
- Focus: `ring-2 ring-[var(--color-ring)]`
- Standalone narrow selects (settings/account): add `className="max-w-xs"`

## Add/edit forms

- **Always use a separate route page**, never a modal/dialog or inline expanding card
- Create/invite forms live at e.g. `/admin/users/invite`
- On success and cancel: `navigate({ to: '/admin/users' })` to return to the list
- Form grids: `grid grid-cols-2 gap-4` for paired fields
- Wrap the form in a `<Card>` with `<CardContent className="pt-6">`
- **Back/cancel: header only.** Ghost button with `<ArrowLeft>` icon, top-right of the page header. No cancel button in the form footer.
- **Form footer: primary action only.** Submit button, **left-aligned**, `pt-2`.

## Card sections

- Use `<CardHeader>` + `<CardTitle>` + `<CardDescription>` for every named card section
- Use `<CardContent>` with `space-y-4` for the field group
- Exception: data tables use `<CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">`

## Tables

- Inside `<Card>` with `<CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">`
- `<thead>` rows: `px-6 py-3 text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide`
- `<tbody>` rows: zebra striping with `var(--color-card)` / `var(--color-secondary)`
- Action controls in table rows: `px-2 py-1 text-xs`

---

## Multi-step wizard password fields (MED)

Any wizard step containing a `type="password"` or secret input must be wrapped in a
`<form>` element, even if the step has no traditional submit button. Without it,
browsers emit a warning and autofill/password managers behave incorrectly.

```tsx
<form onSubmit={(e) => { e.preventDefault(); setStep('next') }}>
  <Input type="password" ... />
  <Button type="submit">Continue</Button>
</form>
```

**Rule:** Every wizard step with a secret field needs a `<form>` wrapper with `onSubmit` advancing to the next step.

---

## See Also

- Shared brand DNA: `design/styleguide.md` (auto-loads for portal files)
