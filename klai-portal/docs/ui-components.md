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

## Tooltip

`components/ui/tooltip.tsx`

Custom hover tooltip. Accepts a `className` prop that is passed to the wrapper `<div>`.

```tsx
import { Tooltip } from '@/components/ui/tooltip'

<Tooltip label="Copy to clipboard">
  <button>...</button>
</Tooltip>
```

**Alignment in table cells:** The wrapper renders as `display:block`. When placed in an `align-top` table cell adjacent to a text column, the SVG icon sits slightly above the text cap height due to font metrics. Fix with `leading-none mt-px`:

```tsx
// Table cell with source icon next to a title text column (Parabole font, text-sm)
<td className="py-4 pr-2 align-top w-6">
  <Tooltip className="leading-none mt-px" label="...">
    <Mic className="h-4 w-4 text-[var(--color-muted-foreground)]" />
  </Tooltip>
</td>
```

- `leading-none` — collapses line-height on the wrapper div, removing baseline offset
- `mt-px` — 1px top margin aligns with Parabole's cap height at `text-sm` (14px)

This offset is font-specific. If the base font changes, re-measure by asking the user to test `margin-top: Xpx` in DevTools.

---

## Color tokens

Use CSS variables for all semantic colors. Never use raw Tailwind color classes for these purposes.

| Token | Use for |
|---|---|
| `var(--color-purple-deep)` | Headings, primary text, active icons |
| `var(--color-muted-foreground)` | Secondary text, placeholder, disabled |
| `var(--color-destructive)` | Error text, delete confirm buttons |
| `var(--color-success)` | Save confirm buttons, positive feedback |
| `var(--color-border)` | Borders, dividers, row hover backgrounds |
| `var(--color-accent)` | Focus rings, links |

```tsx
// Error message
<p className="text-sm text-[var(--color-destructive)]">Verwijderen mislukt</p>

// Delete confirm button
<button className="bg-[var(--color-destructive)] text-white hover:opacity-90">
  <Check />
</button>

// Save confirm button
<button className="bg-[var(--color-success)] text-white hover:opacity-90">
  <Check />
</button>
```

---

## Rules

- Never write inline Tailwind field classes on `<input>` or `<select>` elements in pages - always use `<Input>` / `<Select>` from `components/ui/`
- Add/edit forms belong in a separate route page (e.g. `/admin/users/invite`), not in modals or inline cards
- `<Label>` always has `htmlFor` matching the field `id`
- Never use `text-red-*`, `bg-red-*`, `text-green-*`, `bg-green-*` for semantic states — use `--color-destructive` / `--color-success`
- See `klai-claude/docs/patterns/frontend.md` for the full portal-ui-components pattern

---

## InlineEdit

`components/ui/inline-edit.tsx`

Inline rename field with amber ring and zero layout shift. The view-mode content (`children`) stays in the DOM as an `invisible` spacer when editing, so the input overlays it absolutely — row height never changes.

```tsx
import { InlineEdit } from '@/components/ui/inline-edit'

// State
const [editingId, setEditingId] = useState<string | null>(null)
const [editName, setEditName] = useState('')

// In the cell
<InlineEdit
  isEditing={editingId === item.id}
  value={editName}
  onValueChange={setEditName}
  onSave={() => { save(item.id); setEditingId(null) }}
  onCancel={() => setEditingId(null)}
  isSaving={isSaving}
  inputClassName="font-medium text-sm"
>
  <span className="font-medium text-sm">{item.name}</span>
</InlineEdit>
```

Rules:
- `inputClassName` must match the view-mode text style (font weight, size) — the input renders at the same visual size as the text it replaces
- `children` is the spacer: provide the exact same element(s) shown in view mode
- Save/cancel triggers belong in a separate actions column or below the cell — do NOT put them inside InlineEdit
- Enter → save, Escape → cancel
- The component owns `relative`, `invisible pointer-events-none`, `absolute inset-0`, `ring-1 ring-[var(--color-accent)]`, `rounded-none` — do NOT add these at the call site

**Uses:**
- `routes/app/transcribe/_components/TranscriptionTable.tsx` — rename transcription title

---

## Deletion confirmation patterns

### Standard: `InlineDeleteConfirm` component (table rows)

`components/ui/inline-delete-confirm.tsx`

For any deletion in a table row. Uses the ghost spacer + absolute overlay pattern: action icons stay in the DOM as an invisible spacer (holding column width), while an absolutely-positioned confirm/cancel overlay appears without layout shift.

```tsx
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'

// State
const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

// In the cell renderer
cell: ({ row }) => {
  const isConfirming = confirmDeleteId === row.original.id
  return (
    <InlineDeleteConfirm
      isConfirming={isConfirming}
      isPending={deleteMutation.isPending}
      label={m.some_delete_confirm({ name: row.original.name })}
      cancelLabel={m.cancel()}
      onConfirm={() => { deleteMutation.mutate(row.original.id); setConfirmDeleteId(null) }}
      onCancel={() => setConfirmDeleteId(null)}
    >
      <div className="flex items-center justify-end gap-1">
        <button
          onClick={() => setConfirmDeleteId(row.original.id)}
          className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
        {/* other action icons */}
      </div>
    </InlineDeleteConfirm>
  )
}
```

Rules:
- `label` takes a `ReactNode` — use an i18n string with `{name}` param, never string concatenation
- `children` is the spacer: always provide a flex div with the row's action icons
- The component owns `relative`, `opacity-0 pointer-events-none`, `absolute inset-y-0 right-0`, `[&_svg]:size-2.5`, `whitespace-nowrap` — do NOT add these in the call site

**Uses:**
- `routes/admin/groups/index.tsx` — delete group
- `routes/admin/groups/$groupId/index.tsx` — remove member
- `routes/admin/users/index.tsx` — remove invited user
- `routes/app/focus/index.tsx` — delete notebook
- `routes/app/transcribe/_components/TranscriptionTable.tsx` — delete transcription

### Exception: name-confirmation modal

Use a **modal with name input** only when the deletion is **irreversible and high-impact** — i.e. it destroys a significant amount of data that cannot be recovered (e.g. deleting an entire knowledge base including all its pages).

Rules for name-confirmation modals:
- Explain what will be deleted and that it cannot be undone
- Show the name in **bold** in the explanation text
- Require the user to type the exact name before the confirm button becomes active
- Confirm button uses `var(--color-destructive)` background only when `canDelete === true`
- Cancel is a ghost button, confirm is on the right

```tsx
function DeleteModal({ kb, onCancel, onConfirm, isDeleting }) {
  const [confirmName, setConfirmName] = useState('')
  const canDelete = confirmName === kb.name
  // ...
  <Button
    onClick={onConfirm}
    disabled={!canDelete || isDeleting}
    style={{
      backgroundColor: canDelete ? 'var(--color-destructive)' : undefined,
      color: canDelete ? 'white' : undefined,
    }}
  >
    {isDeleting ? <Loader2 /> : 'Verwijderen'}
  </Button>
}
```

**Current uses of the name-confirmation modal:**
- `routes/app/docs/index.tsx` — delete knowledge base (deletes all pages, irreversible)
