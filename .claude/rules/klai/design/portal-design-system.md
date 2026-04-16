---
paths:
  - "klai-portal/frontend/**"
---

# Portal Design System (Extracted)

> Living reference of the actual design patterns in the Klai portal.
> Last extracted: 2026-04-16. Source of truth: the code, not this document.

---

## Typography

The portal uses **system-ui** for all UI text. Parabole (brand font) is loaded but reserved for the logo and website.

| Context | Font | Weight | Size | Class |
|---|---|---|---|---|
| Body text | system-ui | 400 | 14px (default) | (inherited from `<main>`) |
| Sidebar items | system-ui | 600 | 14px | `text-[14px] font-semibold` |
| Page headings | Parabole Bold | 700 | 26px | `text-[26px] font-display-bold` |
| Collection names | Parabole Medium | 500 | 15px | `text-[15px] font-display` |
| Muted/meta text | system-ui | 400 | 13-14px | `text-sm text-gray-400` |
| Mono labels | Decima Mono | 400 | 11-12px | `font-mono text-xs` |

**Rule:** Never add `style={{ fontFamily: ... }}` to any component. The `<main>` element in `app/route.tsx` sets `system-ui` for the entire app. No overrides needed.

---

## Page Layout

Every page in the app uses one of two container patterns:

| Page type | Container classes | Width |
|---|---|---|
| List / overview | `mx-auto max-w-3xl px-6 py-10` | 768px |
| Form / edit | `mx-auto max-w-lg px-6 py-10` | 512px |
| Full-width (chat) | No max-width, full viewport | 100% |

**Rule:** All content pages are horizontally centered with `mx-auto`. No exceptions. No `p-6` without `mx-auto`.

---

## Color Tokens

### Semantic (use these)

| Token | Value | Usage |
|---|---|---|
| `--color-foreground` | `#191918` | Primary text, headings |
| `--color-muted-foreground` | `#19191899` | Secondary text (60% opacity) |
| `--color-border` | `#e8e6de` | All borders and dividers |
| `--color-background` | `#faf9f6` | Page background (warm white) |
| `--color-muted` | `#f5f4ef` | Input backgrounds, hover states |
| `--color-destructive` | `#C0392B` | Error text and destructive actions |
| `--color-success` | `#27AE60` | Success states |
| `--color-success-bg` | `#D1FAE5` | Success badge backgrounds |
| `--color-success-text` | `#065F46` | Success badge text |
| `--color-warning` | `#D97706` | Warning states |
| `--color-ring` | `#fcaa2d` | Focus rings (amber) |

### Sidebar

| Token | Value | Usage |
|---|---|---|
| `--color-sidebar` | `#f5f4ef` | Sidebar background (warm cream) |
| `--color-sidebar-foreground` | `#191918` | Sidebar text (full opacity) |
| `--color-sidebar-border` | `#e3e2d8` | Sidebar border (50% transparency via class) |

### Usage in code

```tsx
// Text colors
className="text-gray-900"              // Primary text (headings, names)
className="text-gray-400"              // Muted text (subtitles, counts)
className="text-[var(--color-destructive)]"  // Error text

// Backgrounds
className="bg-white"                   // Main content area
className="bg-gray-50"                 // Icon backgrounds, hover states
className="hover:bg-gray-50"           // Row hover

// Borders
className="border border-gray-200"     // All borders
className="divide-y divide-gray-200"   // Row dividers
```

**Rule:** Use `gray-200` for borders (not `gray-300` or CSS tokens). Use `gray-50` for subtle backgrounds. Use `gray-400` for muted text. Use `gray-900` for primary text.

---

## Border Radius

| Element | Radius | Class |
|---|---|---|
| Buttons | 8px | `rounded-lg` |
| Inputs | 8px | `rounded-lg` |
| Cards | 12px | `rounded-xl` |
| Badges | 9999px | `rounded-full` |
| Sidebar items | 6px | `rounded-md` |
| Icon containers | 8px | `rounded-lg` |
| Search bar | 8px | `rounded-lg` |

**Rule:** `rounded-lg` is the default for all interactive elements. Badges are the only `rounded-full` element. No `rounded-full` on buttons.

---

## Components

### Button (`components/ui/button.tsx`)

| Variant | Style | Use for |
|---|---|---|
| `default` | Dark bg (`bg-gray-900`), white text | Primary actions |
| `secondary` | White bg, gray border | Secondary actions |
| `ghost` | Transparent, gray border | Cancel, back navigation |
| `outline` | Transparent, gray border | Same as ghost |
| `destructive` | Red bg, white text | Delete actions |

```tsx
// Primary action
<Button>Opslaan</Button>

// Secondary / cancel
<Button variant="ghost" size="sm">
  <ArrowLeft className="h-4 w-4 mr-2" />
  Annuleren
</Button>

// Inline page buttons (not using Button component)
<button className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors">
  <Plus className="h-4 w-4" />
  Bron toevoegen
</button>

// Outline button
<button className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors">
  <Plus className="h-4 w-4" />
  Nieuwe collectie
</button>
```

### Badge (`components/ui/badge.tsx`)

Always `rounded-full`. Key variants:

| Variant | Style | Use for |
|---|---|---|
| `success` | Green bg/text | "Gesynchroniseerd" status |
| `destructive` | Red bg/text | Error status |
| `secondary` | Gray bg | Neutral labels |
| `outline` | Border only | Counts, metadata |

```tsx
<Badge variant="success">Gesynchroniseerd</Badge>
<Badge variant="destructive">Mislukt</Badge>
```

### Input (`components/ui/input.tsx`)

```tsx
<Input
  placeholder="Zoek..."
  className="rounded-lg border border-gray-200 text-sm"
/>
```

Standard: `rounded-lg`, `border-gray-200`, `text-sm`, focus ring `ring-gray-400`.

### Card (`components/ui/card.tsx`)

```tsx
<Card>
  <CardHeader>
    <CardTitle>Titel</CardTitle>
    <CardDescription>Beschrijving</CardDescription>
  </CardHeader>
  <CardContent>...</CardContent>
</Card>
```

`rounded-xl`, `border-[var(--color-border)]`, transparent background.

### StepIndicator (`components/ui/step-indicator.tsx`)

Pill-style wizard progress bar.

```tsx
const steps: StepItem[] = [
  { label: 'Collectie', onClick: () => goToStep(0) },
  { label: 'Brontype', onClick: () => goToStep(1) },
  { label: 'Configureren' },
]
<StepIndicator steps={steps} currentIndex={1} />
```

- Active: `bg-gray-900 text-white` pill
- Completed: `bg-gray-100 text-gray-700` pill with check icon, clickable
- Future: `bg-gray-50 text-gray-400` pill, disabled
- Connectors: `h-px w-6` lines between pills

---

## Sidebar

```
Background:    var(--color-sidebar)     #f5f4ef
Border:        var(--color-sidebar-border)/50
Text:          var(--color-sidebar-foreground)  #191918  (full opacity)
Active item:   bg-black/[0.06]
Hover:         hover:bg-black/5
Font:          system-ui, 14px, font-semibold (600)
Icons:         size={18} strokeWidth={2}
Width:         w-60 (expanded) / w-14 (collapsed)
```

All sidebar items (nav, admin switcher, account, logout) use the same shared classes:

```tsx
const ITEM_BASE = 'flex items-center rounded-md py-2 mx-3 text-[14px] font-semibold transition-colors text-[var(--color-sidebar-foreground)] hover:bg-black/5'
const ITEM_ACTIVE = 'bg-black/[0.06]'
const ICON_PROPS = { size: 18, strokeWidth: 2 } as const
```

**Rule:** Every clickable sidebar item MUST use `ITEM_BASE`. No exceptions, no per-item overrides.

---

## Tables

Section-style, no card wrapper:

```tsx
<table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
  <thead>
    <tr className="border-b border-[var(--color-border)]">
      <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
        Naam
      </th>
    </tr>
  </thead>
  <tbody>
    <tr className="border-b border-[var(--color-border)] last:border-b-0">
      <td className="py-4 pr-4 align-top">...</td>
    </tr>
  </tbody>
</table>
```

### Collection List (Superdock-style)

Flat divider-separated rows with expand/collapse:

```tsx
<div className="divide-y divide-gray-200 border-t border-b border-gray-200">
  {collections.map(kb => (
    <CollectionRow key={kb.id} kb={kb} />
  ))}
</div>
```

Each row: chevron toggle, icon, name, source count, sync badge, + Add button, delete button.

---

## Form Patterns

### Field layout

```tsx
<div className="space-y-1.5">
  <Label htmlFor="field-id">Label tekst</Label>
  <Input id="field-id" placeholder="..." />
</div>
```

### Form structure

```tsx
<form onSubmit={handleSubmit} className="space-y-4">
  {/* Fields */}
  <div className="space-y-1.5">...</div>
  <div className="space-y-1.5">...</div>

  {/* Error */}
  {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

  {/* Actions — left-aligned */}
  <div className="flex items-center gap-3 pt-2">
    <Button type="submit" disabled={isPending}>Opslaan</Button>
    <button type="button" onClick={goBack} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
      Terug
    </button>
  </div>
</form>
```

### Page header

```tsx
<div className="flex items-center justify-between mb-2">
  <h1 className="text-[26px] font-display-bold text-gray-900">Pagina titel</h1>
  <div className="flex items-center gap-3">
    <button className="... rounded-lg border border-gray-200 ...">Secundair</button>
    <button className="... rounded-lg bg-gray-900 text-white ...">Primair</button>
  </div>
</div>
<p className="text-sm text-gray-400 mb-6">Subtitel beschrijving.</p>
```

---

## Empty States

```tsx
<div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
  <IconComponent className="h-10 w-10 text-gray-300 mx-auto mb-3" />
  <p className="text-base font-medium text-gray-900">Nog geen items</p>
  <p className="text-sm text-gray-400 mt-1">Beschrijving wat te doen.</p>
  <button className="mt-4 ... rounded-lg bg-gray-900 ... text-white">
    <Plus className="h-4 w-4" />
    Eerste item aanmaken
  </button>
</div>
```

---

## Loading States

```tsx
// Skeleton rows
<div className="space-y-3">
  {[1, 2, 3].map((i) => (
    <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
  ))}
</div>

// Button loading
<Button disabled={isPending}>
  {isPending ? 'Laden...' : 'Opslaan'}
</Button>
```

---

## Icons

All icons use Lucide React with consistent props:

| Context | Size | StrokeWidth |
|---|---|---|
| Sidebar nav | 18px | 2 |
| Inline buttons | 16px (h-4 w-4) | default (2) |
| Small actions | 14px (h-3.5 w-3.5) | default |
| Empty state hero | 40px (h-10 w-10) | default |

Third-party icons: `@icons-pack/react-simple-icons` for brand logos (GitHub, Notion, Google Drive, etc.)

---

## Spacing Reference

| Spacing | Value | Usage |
|---|---|---|
| Page padding | `px-6 py-10` | All page containers |
| Section gap | `space-y-6` or `space-y-8` | Between page sections |
| Field gap | `space-y-4` | Between form fields |
| Label-input gap | `space-y-1.5` | Label to input |
| Button gap | `gap-3` | Between buttons |
| Row padding | `py-3.5 px-2` | Collection list rows |
| Header bottom | `mb-2` (title) + `mb-6` (subtitle) | Page headers |

---

## Anti-Patterns (Do Not)

| Do not | Instead |
|---|---|
| `style={{ fontFamily: ... }}` | Inherited from `<main>` |
| `rounded-full` on buttons | `rounded-lg` |
| `p-6 max-w-*` without `mx-auto` | `mx-auto max-w-3xl px-6 py-10` |
| `text-red-600` for errors | `text-[var(--color-destructive)]` |
| Raw `<button>` or `<input>` | Components from `components/ui/` |
| `bg-amber-*` or `bg-yellow-*` | `bg-gray-900` for buttons |
| Different font weights per section | Same `font-semibold` everywhere in sidebar |
| Per-page container overrides | Shared pattern from this doc |
