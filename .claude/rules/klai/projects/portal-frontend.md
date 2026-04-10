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
| `--color-background` | `#fffef2` | Page background (warm ivory) |
| `--color-foreground` | `#191918` | Default text |
| `--color-card` | `#f3f2e7` | Card backgrounds (cream) |
| `--color-card-foreground` | `#191918` | Text on cards |
| `--color-primary` | `#fcaa2d` | shadcn primary (amber accent) |
| `--color-primary-foreground` | `#191918` | Text on primary |
| `--color-secondary` | `#f3f2e7` | shadcn secondary (cream) |
| `--color-secondary-foreground` | `#191918` | Text on secondary |
| `--color-muted` | `#f3f2e7` | Muted backgrounds |
| `--color-muted-foreground` | `#19191899` | Muted text (60% opacity dark) |
| `--color-accent` | `#fcaa2d` | Accent (amber) |
| `--color-accent-foreground` | `#191918` | Text on accent |
| `--color-destructive` | `#C0392B` | Error / destructive actions |
| `--color-success` | `#27AE60` | Save confirm buttons, positive feedback icons |
| `--color-border` | `#e3e2d8` | Default border (warm) |
| `--color-input` | `#f3f2e7` | Input field background |
| `--color-ring` | `#fcaa2d` | Focus ring (amber) |

### Brand color tokens

| Token | Value | Usage |
|---|---|---|
| `--color-rl-bg` | `#fffef2` | Page background |
| `--color-rl-dark` | `#191918` | Primary text |
| `--color-rl-dark-60` | `#19191899` | Body text |
| `--color-rl-dark-30` | `#1919184d` | Placeholders |
| `--color-rl-dark-10` | `#1919181a` | Ghost borders |
| `--color-rl-accent` | `#fcaa2d` | Amber CTA, highlights |
| `--color-rl-accent-dark` | `#a36404` | Text links on light bg |
| `--color-rl-accent-hover` | `#e89a1f` | Button hover |
| `--color-rl-cream` | `#f3f2e7` | Card backgrounds |
| `--color-rl-border` | `#e3e2d8` | Borders, dividers |
| `--color-rl-muted` | `#bab9b0` | Label text, icons |

---

## Sidebar (light organic)

```
Background:         #f3f2e7  (--color-sidebar)
Text:               #191918  (--color-sidebar-foreground)
Border:             #e3e2d8  (right border + internal dividers)
Active/hover item:  #fcaa2d1a (10% amber tint)
Muted text:         #19191899 (60% opacity dark)
```

Logo: uses `/klai-logo.svg` (dark logo on light sidebar).

---

## Typography

### Parabole (sans) - portal defaults

| Context | Size | Weight |
|---|---|---|
| Base body | 14px | 400 (Parabole Regular) |
| Lead/intro text | `text-lg` (18px) | 400 |
| Nav links | `text-sm` | 400 |
| Captions / metadata | `text-xs` (10-12px) | 400 |
| Labels in UI | `text-sm` | 500 (`font-medium`) |
| Page headings | `text-2xl font-bold` | 700 |

Font stack: `--font-sans: "Parabole Regular"`. Display: `--font-display: "Parabole Medium"`.
Mono labels: `--font-mono: "Decima Mono"`.

Line height: 1.6. Anti-aliasing: `-webkit-font-smoothing: antialiased`.

---

## Buttons

Pill-shaped, 12px uppercase with 0.04em tracking. Two variants:

| Variant | Style |
|---|---|
| `default` | Amber bg (`rl-accent`), dark text, hover `rl-accent-hover` |
| `ghost` | Transparent, border `rl-dark-10`, hover border darkens |
| `secondary` | Cream bg, dark text |
| `outline` | Border only, hover cream bg |
| `destructive` | Red bg, white text |
| `link` | `rl-accent-dark` text, underline on hover, normal case |

### Shadcn Button svg size override (MED)

Shadcn Button applies `[&_svg]:size-4` globally via CVA variants. For compact buttons
(`h-6 text-[10px]`), the default 16px icon is too large. Override at the usage site:

```tsx
<Button size="sm" className="h-6 text-[10px] [&_svg]:size-2.5">
  <Check /> Confirm
</Button>
```

**Rule:** Compact confirm/cancel buttons in table rows need `[&_svg]:size-2.5` to keep icons at 10px.

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

- Label color: `var(--color-foreground)`, `block text-sm font-medium`
- Field text color: `var(--color-foreground)`
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

### Inline delete confirmation

Use `InlineDeleteConfirm` from `components/ui/inline-delete-confirm`. Never use a modal, popover, or button swap that changes cell content (causes layout shift).

```tsx
<InlineDeleteConfirm
  isConfirming={confirmDeleteId === row.original.id}
  isPending={deleteMutation.isPending}
  label={m.delete_confirm({ name: row.original.name })}
  cancelLabel={m.cancel()}
  onConfirm={() => { deleteMutation.mutate(row.original.id); setConfirmDeleteId(null) }}
  onCancel={() => setConfirmDeleteId(null)}
>
  <div className="flex items-center justify-end gap-1">
    <button onClick={() => setConfirmDeleteId(row.original.id)} ...><Trash2 /></button>
  </div>
</InlineDeleteConfirm>
```

- `label` uses i18n `{name}` param — never string concatenation
- The component owns the `relative` wrapper, ghost spacer logic, and overlay styling
- Full docs + pattern explanation: `klai-portal/docs/ui-components.md` → Deletion confirmation patterns

### Confirmation hierarchy

Three tiers for destructive actions — pick by severity:

| Tier | Component | When to use |
|---|---|---|
| 1 | `AlertDialog` | Irreversible / offboarding actions (e.g. delete organization) |
| 2 | `DeleteModal` with name input | High-stakes KB deletion where typing the name adds friction |
| 3 | `InlineDeleteConfirm` | Table row deletions — the default case |

Never use a modal for table row actions. Never swap buttons in a way that changes cell width.

### Extract components at 3+ repetitions

When the same UI pattern (e.g. inline delete) is copy-pasted into a third table implementation,
extract it immediately — not after the fourth or fifth. Extracting after the fact requires hunting
down all instances and risking divergence. The `InlineDeleteConfirm` component was extracted at
this threshold.

**Rule:** Three files with the same JSX pattern = extract a shared component before continuing.

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
