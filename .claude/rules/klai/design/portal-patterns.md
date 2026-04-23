---
paths:
  - "klai-portal/frontend/**"
---

# Portal Patterns (v1 spine)

> Portal-specific design patterns. Reflects the actual state of the code in `klai-portal/frontend/` during the v1 chat-first redesign (`SPEC-PORTAL-REDESIGN-002`).
>
> **Relation to `styleguide.md`:** Brand DNA lives in `styleguide.md`. This file overrides it for the portal where v1 deliberately departs from brand DNA. Each override is documented below with a "polish-seam" — the place where `SPEC-PORTAL-POLISH-001` will later restore or evolve the brand target.
>
> **Source of truth:** the code. This file is a reference, not a spec. If code and this file disagree, update whichever one is wrong — but do it in one change and document why.
>
> **Last updated:** 2026-04-23 (v1 spine from SPEC-PORTAL-REDESIGN-002).

---

## v1-spine overrides versus brand DNA

| Concern | Brand DNA (`styleguide.md`) | Portal v1 (this file) | Polish seam |
|---|---|---|---|
| Body font | "One family does everything" — Parabole in three weights for headings, body, UI | system-ui for body and UI; Parabole only in page headings + collection names | POLISH-1 |
| Primary CTA | Amber (`--color-rl-accent`) pill button | Gray-on-white (`bg-gray-900 text-white`) round-full button | POLISH-1 (amber returns) |
| Content background | Warm ivory (`--color-rl-bg` #fffef2) | Flat `bg-white` (LibreChat continuity) | POLISH-1 |
| Case style | Not specified | **Sentence-case everywhere. No `uppercase` class anywhere.** | Permanent |

All four overrides are reversible via `index.css` + this file edit only (no per-file refactor needed).

---

## Typography

### Font family

Portal v1 uses **system-ui** for body and UI text. Parabole is reserved for two contexts:

| Context | Font | Weight | Size | Class |
|---|---|---|---|---|
| Body text | system-ui | 400 | 14px (inherited) | (no override) |
| Sidebar items | system-ui | 600 | 14px | `text-[14px] font-semibold` |
| Page headings (h1) | Parabole Bold | 700 | 26px | `text-[26px] font-display-bold` |
| Collection names | Parabole Medium | 500 | 15px | `text-[15px] font-display` |
| Muted/meta text | system-ui | 400 | 13-14px | `text-sm text-gray-400` |
| Mono labels | Decima Mono | 400 | 11-12px | `font-mono text-xs` |

**Rule:** Parabole appears in page headings and collection names only. Do not use `font-display*` classes in other contexts in v1. Polish-1 decides whether Parabole expands to more contexts.

**Implementation note:** `--font-sans` in `index.css` has Parabole as its primary family for backward compatibility with places that still reference it. The `<main>` wrapper and UI components use Tailwind's default sans (system-ui) via the `font-sans` utility or by not setting `font-family` at all.

**Rule:** Never add `style={{ fontFamily: ... }}` to a component. If you need Parabole, use `font-display` or `font-display-bold`.

### Case

No `uppercase` class anywhere in the portal. No `text-transform: uppercase` in CSS. No `tracking-wider` or `tracking-[0.04em]` on prose (they're remnants of the previous uppercase-button era).

Tab headers, labels, meta-text: sentence-case.

---

## Page Layout

| Page type | Container classes | Width |
|---|---|---|
| List / overview | `mx-auto max-w-3xl px-6 py-10` | 768px |
| Form / edit | `mx-auto max-w-lg px-6 py-10` | 512px |
| Full-width (chat) | No max-width, full viewport | 100% |

**Rule:** All content pages are horizontally centered with `mx-auto`. No exceptions. No `p-6` without `mx-auto`.

---

## Color Tokens

### Principle

- **Grayscale (borders, hover bg, muted text)** → Tailwind literals (`gray-50`, `gray-200`, `gray-400`, `gray-900`)
- **Semantic / themeable** (sidebar, destructive, success, warning, focus-ring) → CSS tokens (`var(--color-sidebar)`, `var(--color-destructive)`, ...)
- **Content background** → `bg-white`
- **Layering in dash** → `bg-black/[0.06]` for active/selected, `bg-black/5` for hover

The `#191918` token (`--color-foreground`) and Tailwind's `gray-900` (`#111827`) are not identical. Both are acceptable in v1:
- Use `--color-foreground` when theming component internals (surface, popover, card)
- Use `gray-900` for prose text utilities

Polish-1 may reconcile these. Until then, do not change existing code to "fix" the mismatch — it's deliberate for v1.

### Semantic tokens (use these for theme-able colors)

| Token | Value | Usage |
|---|---|---|
| `--color-foreground` | `#191918` | Component-level primary (card surface text, popover text) |
| `--color-muted-foreground` | `#19191899` | Component-level secondary text (60% opacity) |
| `--color-background` | `#faf9f6` | (legacy, for components that still reference it — do not apply to page containers in v1; use `bg-white`) |
| `--color-muted` | `#f5f4ef` | Input backgrounds, hover states |
| `--color-destructive` | `#C0392B` | Error text and destructive actions |
| `--color-success` | `#27AE60` | Success states |
| `--color-success-bg` | `#D1FAE5` | Success badge backgrounds |
| `--color-success-text` | `#065F46` | Success badge text |
| `--color-warning` | `#D97706` | Warning states |
| `--color-ring` | `#fcaa2d` | Focus rings (amber) |

### Sidebar tokens

| Token | Value | Usage |
|---|---|---|
| `--color-sidebar` | `#f5f4ef` | Sidebar background (warm cream, retained from brand DNA) |
| `--color-sidebar-foreground` | `#191918` | Sidebar text (full opacity) |
| `--color-sidebar-border` | `#e3e2d8` | Sidebar border |

### Usage

```tsx
// Text colors
className="text-gray-900"                          // Prose primary text (headings, names)
className="text-gray-400"                          // Muted text (subtitles, counts)
className="text-[var(--color-destructive)]"        // Error text — semantic token

// Backgrounds
className="bg-white"                               // Main content area
className="bg-gray-50"                             // Icon containers, subtle surfaces, hover
className="hover:bg-gray-50"                       // Row hover
className="bg-black/[0.06]"                        // Active/selected layer in dash
className="hover:bg-black/5"                       // Layer hover

// Borders
className="border border-gray-200"                 // All borders
className="divide-y divide-gray-200"               // Row dividers
```

**Rule:** Use `gray-200` for borders. Use `gray-50` for subtle backgrounds. Use `gray-400` for muted text. Use `gray-900` for primary prose text. Use semantic tokens (`var(--color-*)`) for error/success/warning/ring states. Apply `bg-black/[0.06]` for active-layer surfaces and `bg-black/5` for hover wherever rest / layering is needed in the dash — not only in the sidebar.

### Amber reserve

Amber (`#fcaa2d`) is intentionally retained as:
- `--color-ring` (focus rings on inputs and focusable elements)
- Logo usage
- BlockNote editor link color (via `--color-rl-accent-dark`)

Amber is NOT applied to buttons, accents, pills, badges, or active states in v1. Polish-1 will reintroduce amber on primary CTAs.

---

## Border Radius

| Element | Radius | Class |
|---|---|---|
| Buttons | 9999px | `rounded-full` |
| Badges | 9999px | `rounded-full` |
| Inputs | 8px | `rounded-lg` |
| Search bar | 8px | `rounded-lg` |
| Icon containers | 8px | `rounded-lg` |
| Cards | 12px | `rounded-xl` |
| Sidebar items | 6px | `rounded-md` |

**Rule:** Buttons and badges are pill-shaped (`rounded-full`). Inputs and search are `rounded-lg`. Cards are `rounded-xl`. Sidebar items are `rounded-md`. The hierarchy is: bigger surface → bigger radius. Smaller interactive → more pill-like.

---

## Components

### Button (`components/ui/button.tsx`)

| Variant | Style | Use for |
|---|---|---|
| `default` | `bg-gray-900 text-white rounded-full` | Primary actions |
| `secondary` | `bg-white text-gray-900 border border-gray-200 rounded-full` | Secondary actions |
| `ghost` | Transparent, `border border-gray-200` | Cancel, back navigation (polish-1 may merge with `outline`) |
| `outline` | Transparent, `border border-gray-200` | Same as ghost in v1 (see polish-seam below) |
| `destructive` | `bg-[var(--color-destructive)] text-white rounded-full` | Delete actions |
| `link` | `text-gray-700 underline-offset-4 hover:underline` | Inline navigation (polish-1 may converge with inline muted `text-gray-400` back-links) |

```tsx
// Primary action
<Button>Opslaan</Button>

// Secondary / cancel
<Button variant="ghost" size="sm">
  <ArrowLeft className="h-4 w-4 mr-2" />
  Annuleren
</Button>

// Inline page buttons (not using Button component — but same visual language)
<button className="flex items-center gap-1.5 rounded-full bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors">
  <Plus className="h-4 w-4" />
  Bron toevoegen
</button>
```

**Rule:** All button variants use `rounded-full` in v1. All text is sentence-case, no `uppercase`.

**Polish seam:** `ghost` vs `outline` are visually identical in v1. Polish-1 decides whether they have distinct semantic meaning or merge to one variant.

### Badge (`components/ui/badge.tsx`)

Always `rounded-full`. Key variants:

| Variant | Style | Use for |
|---|---|---|
| `success` | Green bg/text | "Gesynchroniseerd" status |
| `destructive` | Red bg/text | Error status |
| `secondary` | Gray bg | Neutral labels |
| `outline` | Border only | Counts, metadata |

### Input (`components/ui/input.tsx`)

```tsx
<Input
  placeholder="Zoek..."
  className="rounded-lg border border-gray-200 text-sm"
/>
```

Standard: `rounded-lg`, `border-gray-200`, `text-sm`, focus ring uses `--color-ring` (amber).

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

`rounded-xl`, `border-gray-200`, transparent background.

### StepIndicator (`components/ui/step-indicator.tsx`)

Pill-style wizard progress bar.

- Active: `bg-gray-900 text-white` pill
- Completed: `bg-gray-100 text-gray-700` pill with check icon, clickable
- Future: `bg-gray-50 text-gray-400` pill, disabled
- Connectors: `h-px w-6` lines between pills

---

## Sidebar

```
Background:    var(--color-sidebar)            #f5f4ef  (warm cream, retained)
Border:        var(--color-sidebar-border)     #e3e2d8
Text:          var(--color-sidebar-foreground) #191918  (full opacity)
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

## Layering pattern (`bg-black/[0.06]` / `bg-black/5`)

Use black-alpha layering wherever rest or subtle hierarchy is needed in the dash — not only in the sidebar. This is a brand-level decision for v1 to keep the UI calm.

| Context | Class |
|---|---|
| Active / selected surface | `bg-black/[0.06]` |
| Hover surface | `hover:bg-black/5` |
| Subtle tray / bottom area | `bg-black/[0.03]` (use sparingly) |

Do NOT mix black-alpha layering with gray-literal backgrounds on the same surface. Pick one per surface.

---

## Tables

Section-style, no card wrapper:

```tsx
<table className="w-full text-sm table-fixed border-t border-b border-gray-200">
  <thead>
    <tr className="border-b border-gray-200">
      <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
        Naam
      </th>
    </tr>
  </thead>
  <tbody>
    <tr className="border-b border-gray-200 last:border-b-0">
      <td className="py-4 pr-4 align-top">...</td>
    </tr>
  </tbody>
</table>
```

**Rule:** Table borders use `border-gray-200` (Tailwind literal). Table headers are NOT uppercase — they use `text-xs font-medium text-gray-400 tracking-wide`.

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
  <div className="space-y-1.5">...</div>
  <div className="space-y-1.5">...</div>

  {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

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
  <h1 className="page-title text-[26px] font-display-bold text-gray-900">Pagina titel</h1>
  <div className="flex items-center gap-3">
    <button className="... rounded-full border border-gray-200 ...">Secundair</button>
    <button className="... rounded-full bg-gray-900 text-white ...">Primair</button>
  </div>
</div>
<p className="text-sm text-gray-400 mb-6">Subtitel beschrijving.</p>
```

**Rule:** Page titles use `page-title` utility (2px ascender trim for Parabole) + `font-display-bold`.

---

## Empty States

```tsx
<div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
  <IconComponent className="h-10 w-10 text-gray-300 mx-auto mb-3" />
  <p className="text-base font-medium text-gray-900">Nog geen items</p>
  <p className="text-sm text-gray-400 mt-1">Beschrijving wat te doen.</p>
  <button className="mt-4 ... rounded-full bg-gray-900 ... text-white">
    <Plus className="h-4 w-4" />
    Eerste item aanmaken
  </button>
</div>
```

**Polish seam:** The Rules page uses a different empty-state variant (explanation cards adjacent to the empty-state). Polish-1 decides whether both patterns coexist or converge.

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

Third-party icons: `@icons-pack/react-simple-icons` for brand logos (GitHub, Notion, Google Drive, etc.).

---

## Spacing Reference

| Spacing | Value | Usage |
|---|---|---|
| Page padding | `px-6 py-10` | All page containers |
| Section gap | `space-y-6` or `space-y-8` | Between page sections (polish-1 normalizes the rule) |
| Field gap | `space-y-4` | Between form fields |
| Label-input gap | `space-y-1.5` | Label to input |
| Button gap | `gap-3` | Between buttons |
| Row padding | `py-3.5 px-2` | Collection list rows |
| Header bottom | `mb-2` (title) + `mb-6` (subtitle) | Page headers |

**Polish seam:** `space-y-6` versus `space-y-8` between sections is inconsistent in v1. Polish-1 picks one canonical rule.

---

## Anti-Patterns (Do Not)

| Do not | Instead |
|---|---|
| `style={{ fontFamily: ... }}` | Inherited or `font-display*` |
| `rounded-lg` on buttons | `rounded-full` |
| `p-6 max-w-*` without `mx-auto` | `mx-auto max-w-3xl px-6 py-10` |
| `text-red-600` for errors | `text-[var(--color-destructive)]` |
| Raw `<button>` or `<input>` with inline Tailwind | Components from `components/ui/` (see klai-portal CLAUDE.md) |
| `bg-amber-*` or `bg-yellow-*` on buttons | `bg-gray-900` in v1 (amber returns in polish-1) |
| Different font weights per section | Same `font-semibold` everywhere in sidebar |
| Per-page container overrides | Shared pattern from this doc |
| `uppercase` or `tracking-wider` / `tracking-[0.04em]` on prose | Sentence-case, no tracking adjustment |
| `border-[var(--color-border)]` for table/list borders | `border-gray-200` (Tailwind literal) |
| Gray-literal + black-alpha on the same surface | Pick one layering mechanism per surface |

---

## Polish-1 seams (tracked by SPEC-PORTAL-POLISH-001)

Items intentionally left unresolved in v1:

1. Amber reintroduction on primary buttons
2. Button typography: "amber pill, medium/bold black capitalized text"
3. Empty-state pattern normalization (dashed vs rules-style)
4. `space-y-6` vs `space-y-8` section rhythm
5. `ghost` vs `outline` button semantic difference
6. Back/cancel link styling convergence
7. `text-gray-900` vs `--color-foreground` (#191918) alignment
8. Parabole weight scale beyond 500/700
9. Parabole scope expansion beyond headings + collection names

Do not pre-commit Jantine's direction by implementing any of these in v1.
