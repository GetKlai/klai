---
paths: ["klai-website/src/**/*.astro", "klai-website/src/**/*.tsx", "klai-portal/frontend/src/**/*.tsx", "klai-portal/frontend/src/**/*.ts"]
---

# Klai UI Styleguide

> Authoritative visual and interaction reference for all Klai products (website, portal, future apps).
> Full reference: `klai-claude/docs/styleguide.md`
> Source of truth: `klai-website/src/styles/global.css` and `klai-portal/frontend/src/index.css`.

---

## Design philosophy

Klai should feel calm, confident, and warm. Not a startup shouting for attention. Not an enterprise wall.

- **Calm over chaos.** Lots of sand/off-white space. Few elements per screen. One point per section.
- **Typography does the work.** Serif headings + sans-serif body = well-written magazine, not SaaS template.
- **Movement with restraint.** No animations for their own sake. Subtle fades and hover responses only.
- **Show the product.** Screenshots, code blocks, real interfaces. No stock photos of people behind laptops.
- **Two contexts.** USE side (warmer, more sand, human language) vs. BUILD side (darker, more purple, technical).

---

## Colors

### CSS variables

| Variable | Hex | Usage |
|---|---|---|
| `--color-purple-primary` | `#2D1B69` | Brand color. Borders, sidebar accents, logo tint. |
| `--color-purple-deep` | `#1A0F40` | Dark backgrounds. Hero, sidebar, prose headings. |
| `--color-purple-accent` | `#7C6AFF` | Buttons, highlights, active nav, large UI elements. |
| `--color-purple-muted` | `#4A3A8A` | Hover states, secondary buttons, text links on light backgrounds. |
| `--color-sand-light` | `#F5F0E8` | Warm, paper-like background for sections and cards. |
| `--color-sand-mid` | `#EAE3D5` | Slightly darker sand for section contrast. |
| `--color-off-white` | `#FAFAF8` | Default body background. Cooler than sand, warmer than pure white. |

### Text colors

| Token | Hex | Usage |
|---|---|---|
| Body text | `#1A1A1A` | Near-black. Default `color` on `body`. |
| Prose headings | `#1A0F40` | Same as `--color-purple-deep`. H1/H2 in articles, docs, company pages. |
| Muted text | `#1A1A1A` at 50% opacity | Taglines, secondary info, captions. |

### How colors are applied

- **Hero and dark sections:** `#1A0F40` background, white text.
- **Accent glow effects:** `#7C6AFF` at 10-30% opacity as radial blur backgrounds.
- **Borders and dividers:** `#2D1B69` at 6-15% opacity (`rgba(45,27,105,0.1)`).
- **Active/hover states:** `#7C6AFF` for links and nav, `#4A3A8A` for secondary buttons.
- **Section backgrounds:** `#F5F0E8` and `#EAE3D5` for alternating light sections.

### Accessibility (WCAG)

`#7C6AFF` on `#F5F0E8` fails WCAG AA at small text sizes.

| Combination | Result |
|---|---|
| `#1A1A1A` on `#F5F0E8` | Excellent contrast |
| `#F5F0E8` on `#1A0F40` | Excellent contrast |
| `#7C6AFF` on `#1A0F40` | Fine for UI use |
| `#7C6AFF` on `#F5F0E8` (small text) | **Fails AA - use `#4A3A8A` instead** |

---

## Typography

Three fonts. Each has a strict role. Do not mix roles.

### Font stack

| Variable | Font | Tailwind class | Fallback |
|---|---|---|---|
| `--font-serif` | Libre Baskerville | `font-serif` | Georgia, serif |
| `--font-display` | Manrope | `font-display` | system-ui, sans-serif |
| `--font-sans` | Inter | `font-sans` | system-ui, sans-serif |

### Libre Baskerville (serif) - editorial weight

Used for: H1/H2 in prose (blog, docs, company pages), hero H1, blockquotes, italic emphasis (`<em>`).

**Never use for:** nav labels, buttons, form inputs, or any functional UI chrome.

### Manrope (display) - numbers and CTA labels

Used for: prices and billing numbers, CTA button labels, large stats (NumberTicker), comparison table column headers.

**Never use for:** body paragraphs or prose.

### Inter (sans) - everything else

Used for: body paragraphs, nav labels, sidebar links, secondary text, captions, metadata, form inputs.

Safe default: if in doubt, use Inter.

---

## Buttons

### Primary button (ShimmerButton - variant `primary`)

```
Background:   #7C6AFF
Hover bg:     #6B5AEE
Hover shadow: 0 0 20px rgba(124, 106, 255, 0.3)
Padding:      px-6 py-3  (24px / 12px)
Radius:       rounded-lg (0.75rem / 12px)
Font:         font-display font-semibold text-sm tracking-wide
Text color:   white
```

### Ghost button (ShimmerButton - variant `ghost`)

```
Border:     border border-white/20
Text:       text-white/80
Hover:      text-white, border-white/40
Padding:    same as primary
```

### Inline text link (light background)

```
Color:  #4A3A8A  (not #7C6AFF - fails contrast)
Hover:  #2D1B69
```

---

## Rules and constraints

1. **Never** put `font-serif` (Baskerville) on nav labels, buttons, or UI chrome. It is editorial, not functional.
2. **Never** use `#7C6AFF` for small text on sand backgrounds - use `#4A3A8A` instead.
3. **Never** add new colors without updating `global.css`/`index.css` and `klai-claude/docs/styleguide.md`.
4. **Never** use `font-display` (Manrope) for body text.
5. **Never** use animations for decoration. Every animation must serve a purpose.
6. **USE side** (user-facing tools): lean warm, more sand, larger type, human language.
7. **BUILD side** (API/developer): lean dark, more purple, technical details, code snippets.

---

## Anti-patterns

| What | Why not |
|---|---|
| Neon glow gradients | Not Klai - that's Jasper/Copy.ai energy |
| Cold enterprise gray | Loses warmth |
| Stock photos of people | Says nothing about the product |
| Auto-playing video | Breaks calm |
| Over-designed Framer animations | Substance over style |
| Many elements per screen | Dilutes the message |
| `display:none/block` for content switching | Use proper state management |
| Em dashes (--) in content | House rule: use regular dashes or rewrite |

---

---

## Portal admin UI

> Applies to `klai-portal/frontend/src/routes/admin/` and `klai-portal/frontend/src/routes/app/`.

### Form fields

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

### Add/edit forms

- **Always use `<Dialog>`** from `components/ui/dialog.tsx`, never an inline expanding card
- Dialog max-width: `max-w-md` (default)
- Form grids: `grid grid-cols-2 gap-4` for paired fields (name/name, role/language)
- Submit button goes in `<DialogFooter>`, linked via `form="form-id"`

### Card sections

- Use `<CardHeader>` + `<CardTitle>` + `<CardDescription>` for every named card section
- Use `<CardContent>` with `space-y-4` for the field group
- Exception: data tables use `<CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">` to flush the table edges

### Tables

- Inside `<Card>` with `<CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">`
- `<thead>` rows: `px-6 py-3 text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide`
- `<tbody>` rows: zebra striping with `var(--color-card)` / `var(--color-secondary)`
- Action controls in table rows use compact size: `px-2 py-1 text-xs`

---

## See Also

- Full reference with spacing, layout, border radius, shadows, sidebar and portal tokens: `klai-claude/docs/styleguide.md`
- [Brand colors](https://www.getklai.com/company/brand-colors) - canonical color reference
- [rules/gtm/klai-brand-voice.md](../gtm/klai-brand-voice.md) - tone and writing style
- [patterns/frontend.md](../../docs/patterns/frontend.md) - technical frontend patterns (i18n, UI components)
