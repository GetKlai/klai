---
paths: ["klai-portal/frontend/src/**/*.tsx", "klai-portal/frontend/src/**/*.ts"]
---

# Portal admin UI

> Applies to `klai-portal/frontend/src/routes/admin/` and `klai-portal/frontend/src/routes/app/`.
> Common design system (colors, typography, buttons): auto-loaded via `klai-ui-styleguide.md`.

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

- Label color: `var(--color-purple-deep)`, `block text-sm font-medium` (block forces label above the field)
- Field text color: `var(--color-purple-deep)`
- Border: `var(--color-border)`, `rounded-md`
- Focus: `ring-2 ring-[var(--color-ring)]`
- Standalone narrow selects (settings/account): add `className="max-w-xs"` on the `<Select>`

## Add/edit forms

- **Always use a separate route page**, never a modal/dialog or inline expanding card
- Create/invite forms live at e.g. `/admin/users/invite` (TanStack Router file: `routes/admin/users/invite.tsx`)
- On success and cancel: `navigate({ to: '/admin/users' })` to return to the list
- Form grids: `grid grid-cols-2 gap-4` for paired fields (name/name, role/language)
- Wrap the form in a `<Card>` with `<CardContent className="pt-6">`
- **Back/cancel: header only.** Ghost button with `<ArrowLeft>` icon, top-right of the page header. No cancel button in the form footer - two identical cancel controls harm screenreader users (button list navigation shows duplicate labels with no context).
- **Form footer: primary action only.** Just the submit button, **left-aligned**, `pt-2`. Left alignment matches the scan axis of the form fields above it (GOV.UK, GitHub Primer, PatternFly all require this). Right-alignment is for dialogs only - and we don't use those.

## Card sections

- Use `<CardHeader>` + `<CardTitle>` + `<CardDescription>` for every named card section
- Use `<CardContent>` with `space-y-4` for the field group
- Exception: data tables use `<CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">` to flush the table edges

## Tables

- Inside `<Card>` with `<CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">`
- `<thead>` rows: `px-6 py-3 text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide`
- `<tbody>` rows: zebra striping with `var(--color-card)` / `var(--color-secondary)`
- Action controls in table rows use compact size: `px-2 py-1 text-xs`
