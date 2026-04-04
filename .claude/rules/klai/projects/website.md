---
paths: ["klai-website/**"]
---

# Website Patterns

> Website-specific design patterns (USE side: warm, editorial, sand).
> Shared brand DNA (colors, typography roles, rules): auto-loaded via `design/styleguide.md`.

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

### Toggle / segmented button (e.g., pricing toggle)

```
Container:     bg-[#F5F0E8] rounded-lg p-1
Active item:   bg-white shadow-sm text-[#1A0F40], rounded-md, px-5 py-2 text-sm font-medium
Inactive item: bg-transparent text-[#1A1A1A]/40, same padding/radius/font
```

---

## Typography sizes

### Libre Baskerville (serif)

| Context | Size | Weight |
|---|---|---|
| Hero H1 | `text-4xl` to `text-6xl` (36px-60px at 16px base) | 400 or 700 |
| Section H1/H2 prose | `text-2xl` to `text-xl` (24px-20px) | 400 or 700 |
| Blockquotes | Italic, same size as body | 400 italic |

Line height for headings: `leading-[1.1]` (tight). Tracking: `tracking-tight`.

### Manrope (display)

| Context | Size | Weight |
|---|---|---|
| Price display | `text-3xl` (30px) | 700 |
| CTA button label | `text-sm` (12px at 14px base) | 600 (`font-semibold`) |
| Large stats | `text-4xl`+ | 700 or 800 |

### Inter (sans)

| Context | Size | Weight |
|---|---|---|
| Base body | 14px (`--text-base: 0.875rem`) | 400 |
| Lead/intro text | `text-lg` (18px) | 400 |
| Nav links | `text-sm` | 400 |
| Captions / metadata | `text-xs` (10-12px) | 400 |

Line height: `leading-relaxed` (1.625). Anti-aliasing: `-webkit-font-smoothing: antialiased`.

---

## Spacing and layout

### Container

```
Max width:         max-w-7xl  (1280px)
Horizontal padding: px-6      (24px each side)
```

### Navigation bar

```
Height:         h-16   (64px)
Background:     transparent (scrolled: rgba(26,15,64,0.95) with backdrop-filter blur(12px))
Scrolled border: 1px solid rgba(255,255,255,0.06)
```

### Vertical section spacing

| Section type | Padding |
|---|---|
| Hero | `pt-32 pb-20` (128px / 80px) |
| Standard sections | `py-24` to `py-32` (96-128px) |
| Content within sections | `mb-6` to `mb-12` between blocks |

### Grid gaps

| Context | Gap |
|---|---|
| Pricing cards | `gap-6` (24px) |
| Navigation links | `gap-8` (32px) |
| Hero grid columns | `gap-12` (48px) |
| Card content items | `gap-2` to `gap-4` (8-16px) |

---

## Shadows and elevation

| Context | Value |
|---|---|
| Highlighted pricing card | `shadow-xl shadow-[#2D1B69]/25` |
| Primary button hover | `box-shadow: 0 0 20px rgba(124,106,255,0.3)` |
| Toggle active item | `shadow-sm` |
| Radial glow (hero) | `bg-[#7C6AFF]/10 rounded-full blur-[120px]` (ambient light) |

---

## Animations and motion

| Animation | Duration | Used for |
|---|---|---|
| `fade-up` | 0.6s ease | Content fading in on scroll (BlurFade component) |
| `shimmer` | 2.5s linear infinite | Shimmer effect on primary button background |
| `marquee` | 30s linear infinite | Scrolling trust/feature strip |
| `meteor` | 5s linear infinite | Decorative meteor lines in hero |
| `beam` | Stroke-dash animation | SVG beam effects |

**Transition defaults:**
- Interactive elements: `transition-all duration-200`
- Color-only changes: `transition-colors`

---

## GTM agents require klai-website as working directory (HIGH)

`gtm-blog-writer`, `gtm-blog-seo`, and `gtm-voice-editor` live in `klai-website/.claude/agents/gtm/` and are invisible from the monorepo root — agent invocations fail silently.

**Prevention:** Start Claude from inside the website: `cd klai-website && claude`

---

## klai-website is a separate git repo (MED)

`klai-website/` has its own git history and is ignored by the root `.gitignore`. Commits from the root miss website changes.

**Prevention:** Always commit and push from within `klai-website/`.

---

## See Also

- Shared brand DNA: `design/styleguide.md` (auto-loads for website files)
- [Brand colors](https://www.getklai.com/company/brand-colors) - canonical color reference
- [rules/gtm/klai-brand-voice.md](../../gtm/klai-brand-voice.md) - tone and writing style
