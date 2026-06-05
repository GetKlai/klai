# Portal UI Components

> Component reference for `frontend/src/components/ui/`.
> These are owned, copy-paste components - not a black-box library.
> Modify the source directly when you need to change default styling.
>
> This file is component-level guidance. The current portal UX/layout source of
> truth is `klai-portal/frontend/docs/ui-standards.md`; when these disagree,
> update this file to match the portal standards and current implementation.

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

Form field label. Uses `text-sm font-medium` and the current portal foreground
color. Always pair it with a matching field `id`.

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

### Section with header

For tab/detail pages and compact settings pages, prefer an unframed section.
This is the current pattern for detail tabs such as API keys/widgets and for
account settings.

```tsx
<div>
  <h2 className="text-sm font-medium text-gray-900 mb-2">Taal</h2>
  <p className="text-sm text-gray-400 mb-6">
    Standaardtaal voor nieuwe gebruikers.
  </p>
  <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
    {/* fields, save button */}
  </div>
</div>
```

### Card section with header

Use a card only where the surrounding page already uses card sections or where
the content benefits from being a framed standalone block. Do not wrap ordinary
tab/detail settings in a large rounded bordered card by default.

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

### Data table

```tsx
<table className="w-full text-sm border-t border-b border-gray-200">
  <thead>
    <tr className="border-b border-gray-200">
      <th className="px-6 py-3 text-left text-xs font-medium text-gray-400">
        Naam
      </th>
    </tr>
  </thead>
  <tbody>
    {rows.map((row) => (
      <tr key={row.id} className="border-b border-gray-200 klai-hover cursor-pointer">
        <td className="px-6 py-3 text-gray-900">{row.name}</td>
      </tr>
    ))}
  </tbody>
</table>
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

## Alert

`components/ui/alert.tsx`

Inline semantic callout — the standardized version of the "icon + message in a
soft tinted rounded box" pattern. Use it for wizard/form feedback and inline
warnings. It is **not** a toast (`sonner`) and **not** a modal (`dialog`).

```tsx
import { Alert } from '@/components/ui/alert'

<Alert variant="warning">
  <span>{m.app_meetings_teams_warning()}</span>
</Alert>

// Compact, for dense wizard-step feedback
<Alert variant="success" size="sm">
  <span>Selector matches real article content.</span>
</Alert>
```

- `variant`: `info` | `success` | `warning` | `destructive`. Derives from the
  same primary tokens as the row-action tones / `Badge` (soft `/5` tint
  background, `/30` tint border, solid token icon + text).
- `size`: `default` (text-sm, `h-4` icon) or `sm` (text-xs, `h-3.5` icon).
- The leading icon is automatic per variant. Override with `icon={SomeIcon}`,
  or hide it with `icon={null}`.
- Renders `role="alert"`.

Do not hand-roll callouts with raw `amber-*`/`red-*`/`green-*` Tailwind — use
this component so every semantic callout shares one hue, tint, and icon system.

---

## Color tokens

Use Tailwind grayscale literals for prose, borders, subtle backgrounds, and
hover layers. Use CSS variables for semantic or themeable states.

| Color | Use for |
|---|---|
| `text-gray-900` | Headings, primary prose text, names |
| `text-gray-400` | Muted descriptions, metadata, placeholder-like text |
| `border-gray-200` / `divide-gray-200` | Borders and dividers |
| `klai-hover` | Interactive row/list/sidebar hover |
| `var(--color-muted-foreground)` | Secondary text, placeholder, disabled |
| `var(--color-destructive)` | Error text, delete confirm buttons |
| `var(--color-success)` | Save confirm buttons, positive feedback |
| `var(--color-ring)` | Focus rings |

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

## Detail tabs

Use the owned `Tabs` component. The canonical pattern (active underline, icon
and count rules, router-navigation exception) lives in the **Tabs** section of
`klai-portal/frontend/docs/ui-standards.md` — this file does not redefine it.

```tsx
import { Tabs, type TabItem } from '@/components/ui/tabs'

const tabs: TabItem<TabId>[] = [
  { id: 'settings', label: m.account_tab_settings(), icon: Settings },
  { id: 'danger', label: m.admin_shared_tab_danger(), icon: AlertTriangle },
]

<Tabs tabs={tabs} value={activeTab} onValueChange={setTab} />
```

Danger tabs are unframed sections:

```tsx
<div>
  <h2 className="text-sm font-medium text-[var(--color-destructive)] mb-2">
    Verwijderen
  </h2>
  <p className="text-sm text-gray-400 mb-4">
    Deze actie kan niet ongedaan worden gemaakt.
  </p>
  <Button variant="destructive" size="sm">
    <Trash2 className="h-4 w-4 mr-2" />
    Verwijderen
  </Button>
</div>
```

---

## Rules

- Never write inline Tailwind field classes on `<input>` or `<select>` elements in pages - always use `<Input>` / `<Select>` from `components/ui/`
- Add/edit forms belong in a separate route page (e.g. `/admin/users/invite`), not in modals or inline cards
- `<Label>` always has `htmlFor` matching the field `id`
- Do not use uppercase labels or tracking utilities in the portal; use sentence case
- Never use `text-red-*`, `bg-red-*`, `text-green-*`, `bg-green-*` for semantic states — use `--color-destructive` / `--color-success`
- Use `.claude/rules/klai/design/portal-patterns.md` for page-level layout and visual language

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
