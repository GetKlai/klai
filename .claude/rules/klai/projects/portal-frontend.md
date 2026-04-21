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

**Standard: section-style, no card wrapper.** Reference implementation: `routes/app/transcribe/_components/TranscriptionTable.tsx`.

```tsx
<table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
  <thead>
    <tr className="border-b border-[var(--color-border)]">
      <th className="py-3 pr-2 w-6" />  {/* icon column */}
      <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]">
        Label
      </th>
      <th className="py-3 pr-2 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-28">
        Date
      </th>
      <th className="py-3 text-right w-36" />  {/* actions column */}
    </tr>
  </thead>
  <tbody>
    {items.map((item) => (
      <tr key={item.id} className="border-b border-[var(--color-border)] last:border-b-0">
        <td className="py-4 pr-2 align-top w-6">...</td>
        <td className="py-4 pr-4 align-top">...</td>
        <td className="py-4 pr-2 align-top whitespace-nowrap w-28">
          <span className="text-sm tabular-nums">{formatDate(item.created_at)}</span>
        </td>
        <td className="py-4 align-top text-right w-36">...</td>
      </tr>
    ))}
  </tbody>
</table>
```

Rules:
- `table-fixed` with explicit `w-*` on all fixed columns; fluid column gets no width
- `align-top` on every `<td>` — rows with multi-line content stay top-aligned
- `border-t border-b` on the table, `border-b last:border-b-0` on rows — no card wrapper
- Header: `text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]`
- Date: `whitespace-nowrap tabular-nums` + `w-28`
- Actions: `text-right w-36`

### Action icon buttons

Wrap each icon in a `<Tooltip>`. The flex container sits in `align-top` so use `items-start mt-px`:

```tsx
<div className="flex items-start justify-end gap-2 mt-px">
  <Tooltip label={m.action_label()}>
    <button
      onClick={...}
      aria-label={m.action_label()}
      className="inline-flex items-center justify-center text-[var(--color-xxx)] transition-opacity hover:opacity-70"
    >
      <Icon className="h-4 w-4" />
    </button>
  </Tooltip>
</div>
```

### Inline rename (InlineEdit)

Use `InlineEdit` from `components/ui/inline-edit`. Wrap only the title element — not metadata below it. Put save/cancel in the actions column as the same `h-6 text-[10px]` Button pattern as delete confirmation.

```tsx
{/* Title cell */}
<td className="py-4 pr-4 align-top">
  <InlineEdit
    isEditing={editingId === item.id}
    value={editName}
    onValueChange={setEditName}
    onSave={() => onRename(item.id, editName.trim() || null)}
    onCancel={cancelEdit}
    isSaving={isRenaming && renamingId === item.id}
    inputClassName="font-medium text-sm"
  >
    <div>
      <span className="font-medium">{item.name}</span>
      {/* inline badges stay inside the spacer */}
    </div>
  </InlineEdit>
  <div className="mt-1"><MetaText /></div>  {/* always visible */}
</td>

{/* Actions cell */}
<td className="py-4 align-top text-right w-36">
  <div className="relative">
    <div className={isEditing ? 'opacity-0 pointer-events-none' : undefined}>
      <InlineDeleteConfirm ...>
        <div className="flex items-start justify-end gap-2 mt-px">
          {/* action icons */}
        </div>
      </InlineDeleteConfirm>
    </div>
    {isEditing && (
      <div className="absolute inset-y-0 right-0 z-10 flex items-center gap-1 whitespace-nowrap">
        <Button size="sm" className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5 bg-[var(--color-success)] text-white hover:opacity-70"
          disabled={isSaving} onClick={() => onRename(item.id, editName.trim() || null)}>
          {isSaving ? <Loader2 className="animate-spin" /> : <Check />}
          {m.save()}
        </Button>
        <Button size="sm" variant="ghost" className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5" onClick={cancelEdit}>
          <X />{m.cancel()}
        </Button>
      </div>
    )}
  </div>
</td>
```

Close edit on mutation complete via `useRef` — never `setEditingId(null)` inside `saveEdit`:

```tsx
const wasRenaming = useRef(false)
useEffect(() => {
  if (wasRenaming.current && !isRenaming) { setEditingId(null); setEditName('') }
  wasRenaming.current = isRenaming
}, [isRenaming])
```

### Inline delete confirmation

Use `InlineDeleteConfirm` from `components/ui/inline-delete-confirm`. Never use a modal or button swap that changes cell width.

```tsx
<InlineDeleteConfirm
  isConfirming={confirmingDeleteId === item.id}
  isPending={isDeleting}
  label={m.delete_confirm({ name: item.name })}
  cancelLabel={m.cancel()}
  onConfirm={() => { handleDelete(item); setConfirmingDeleteId(null) }}
  onCancel={() => setConfirmingDeleteId(null)}
>
  <div className="flex items-start justify-end gap-2 mt-px">
    <button onClick={() => setConfirmingDeleteId(item.id)} ...><Trash2 /></button>
  </div>
</InlineDeleteConfirm>
```

- `label` uses i18n `{name}` param — never string concatenation
- Full docs: `klai-portal/docs/ui-components.md` → Deletion confirmation patterns

### Confirmation hierarchy

| Tier | Component | When to use |
|---|---|---|
| 1 | `AlertDialog` | Irreversible / offboarding (e.g. delete organization) |
| 2 | `DeleteModal` with name input | High-stakes KB deletion |
| 3 | `InlineDeleteConfirm` | Table row deletions — the default |

Never use a modal for table row actions.

### Extract components at 3+ repetitions

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

## ID format change breaks length-guard redirect (HIGH)

When a URL scheme changes ID length or format (e.g. 8-char prefix → full 36-char UUID),
any existing `pid.length === 8` guard in redirect logic will silently stop firing.
The auto-redirect that upgrades old-format URLs to new-format URLs never triggers,
leaving users on broken or stale URLs with no visible error.

**Why:** The guard was written for the old format and was never updated when the ID scheme changed.

**Prevention:** When changing an ID length or format, search the entire codebase for all
`length === N`, `length < N`, or `startsWith(id)` checks that reference the old length or
prefix logic. Update or remove every guard before shipping.

---

## Verify exported handle interface before calling methods (MED)

When calling methods on a React `forwardRef` handle (e.g. `BlockPageEditorHandle`),
verify the exported interface in the source file before use. TypeScript will catch
mismatches in CI, but only if `tsc` is run — the dev server does not run type-checking.

**Why:** A handle ref exposes `getContent()`, but a caller written `getMarkdown()` — a method
that does not exist. The dev server hot-reloaded fine; CI's `tsc` caught it.

**Prevention:** Before pushing code that calls methods on a custom ref handle type, open the
source file and confirm the exact exported method names.

---

## TanStack Router routeTree.gen.ts must be committed (HIGH)

Adding a new file-based route (e.g. `src/routes/$locale/signup/social.tsx`) without regenerating and committing `routeTree.gen.ts` causes TypeScript errors in CI. CI uses the committed version — the local dev server auto-regenerates on save, so the problem is invisible locally.

**Why:** TanStack Router's file-based routing generates `routeTree.gen.ts` from the file tree. Devs see a working app; CI sees the old generated file and fails type-checking.

**Prevention:** After adding any new route file, run `npx @tanstack/router-cli generate` and commit `routeTree.gen.ts` before pushing.

---

## See Also

- Shared brand DNA: `design/styleguide.md` (auto-loads for portal files)
