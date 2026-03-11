# Portal UI Components

> Component reference for `frontend/src/components/ui/`.
> These are owned, copy-paste components - not a black-box library.
> Modify the source directly when you need to change default styling.

---

## Input

`components/ui/input.tsx`

Standard text input. Defaults to `w-full`. Pass `className` to override width.

```tsx
import { Input } from '@/components/ui/input'

<Input
  id="email"
  type="email"
  required
  value={email}
  onChange={(e) => setEmail(e.target.value)}
  placeholder="jan@example.com"
/>
```

Always pair with a `<Label>` and matching `id`/`htmlFor`.

---

## Label

`components/ui/label.tsx`

Form field label. Uses `var(--color-purple-deep)`, `text-sm font-medium`.

```tsx
import { Label } from '@/components/ui/label'

<Label htmlFor="email">E-mailadres</Label>
```

---

## Select

`components/ui/select.tsx`

Native `<select>`. Defaults to `w-full`. Pass `className="max-w-xs"` for standalone selects (settings, account pages).

```tsx
import { Select } from '@/components/ui/select'

// In a form grid - full width
<Select id="role" value={role} onChange={(e) => setRole(e.target.value)}>
  <option value="member">Lid</option>
  <option value="admin">Beheerder</option>
</Select>

// Standalone (settings/account) - constrain width
<Select id="language" value={lang} onChange={...} className="max-w-xs">
  <option value="nl">Nederlands</option>
  <option value="en">English</option>
</Select>

// Compact table row variant
<Select value={user.role} onChange={...} className="w-auto px-2 py-1 text-xs">
```

---

## Dialog

`components/ui/dialog.tsx`

Modal overlay. Closes on Escape and backdrop click.

**Sub-components:** `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogBody`, `DialogFooter`

```tsx
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogBody,
  DialogFooter,
} from '@/components/ui/dialog'

<Dialog open={showDialog} onClose={handleClose}>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>Gebruiker uitnodigen</DialogTitle>
    </DialogHeader>
    <DialogBody>
      <form id="invite-form" onSubmit={handleSubmit} className="space-y-4">
        {/* fields */}
      </form>
    </DialogBody>
    <DialogFooter>
      <Button variant="outline" onClick={handleClose}>
        Annuleren
      </Button>
      <Button type="submit" form="invite-form" disabled={isPending}>
        Uitnodiging versturen
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

**Note:** The submit button uses `form="form-id"` to link to the form in `<DialogBody>` without nesting a button inside the form.

Default width: `max-w-md`. Override on `<DialogContent className="max-w-lg">` if needed.

---

## Standard patterns

### Field (label + input/select)

```tsx
<div className="space-y-1.5">
  <Label htmlFor="field-id">Label text</Label>
  <Input id="field-id" type="text" value={value} onChange={...} />
</div>
```

### Two-column field grid

```tsx
<div className="grid grid-cols-2 gap-4">
  <div className="space-y-1.5">
    <Label htmlFor="first-name">Voornaam</Label>
    <Input id="first-name" ... />
  </div>
  <div className="space-y-1.5">
    <Label htmlFor="last-name">Achternaam</Label>
    <Input id="last-name" ... />
  </div>
</div>
```

### Card section with header

```tsx
<Card>
  <CardHeader>
    <CardTitle>Taal</CardTitle>
    <CardDescription>Standaardtaal voor nieuwe gebruikers.</CardDescription>
  </CardHeader>
  <CardContent className="space-y-4">
    {/* fields, save button */}
  </CardContent>
</Card>
```

### Data table card

```tsx
<Card>
  <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-[var(--color-border)]">
          <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
            Naam
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr
            key={row.id}
            className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
          >
            <td className="px-6 py-3 text-[var(--color-purple-deep)]">{row.name}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </CardContent>
</Card>
```

---

## Rules

- Never write inline Tailwind field classes on `<input>` or `<select>` elements in pages - always use `<Input>` / `<Select>` from `components/ui/`
- Add/edit forms belong in `<Dialog>`, not in inline expanding cards
- `<Label>` always has `htmlFor` matching the field `id`
- See `klai-claude/docs/patterns/frontend.md` for the full portal-ui-components pattern
