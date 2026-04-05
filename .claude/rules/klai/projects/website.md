---
paths: ["klai-website/**"]
---

# Website Patterns

> Website-specific design patterns (USE side: warm, editorial, ivory/cream).
> Shared brand DNA (colors, typography roles, rules): auto-loaded via `styleguide.md`.
> Source of truth: `/Users/jantinedoornbos/Documents/Projects/klai-website/src/styles/global.css`

---

## Buttons

### Primary button (`.btn-accent`)

```
Background:   #fcaa2d (amber)
Hover bg:     #e89a1f
Text color:   #191918 (dark on amber)
Radius:       999px (pill)
Padding:      10px 20px (rendered)
Min height:   44px
Font:         Parabole Regular, 12px, weight 400
Text style:   uppercase, tracking 0.04em, line-height 1.2
Icon:         arrow SVG (w-2.5 h-2.5), inline-flex with gap-0.5rem
```

### Ghost button (`.btn-ghost`)

```
Background:   transparent
Border:       1px solid #1919181a (10% dark)
Hover border: #191918 (solid dark)
Text color:   #191918
All other:    same as .btn-accent (same size, font, padding, radius)
```

Both buttons use global CSS classes, not component props. Apply via `class="btn-accent"` or `class="btn-ghost"`.
Definitions live in `global.css` and page-level `<style is:global>` blocks (with `!important`).

---

## Typography sizes

All text uses Parabole. Sizes are set with explicit pixel values, not Tailwind scale.

### Headings (font-display, weight 400)

| Context | Size | Responsive | Tracking | Line height |
|---|---|---|---|---|
| Hero H1 | `text-[48px]` | `text-[32px] sm:text-[40px] md:text-[48px]` | `tracking-[-0.04em]` | `leading-[110%]` |
| Section H2 | `text-[40px]` | `text-[32px] md:text-[40px]` | `tracking-[-0.04em]` | `leading-[110%]` |
| Card title | `text-[17px]` | - | `tracking-[-0.02em]` | default | uses `font-display-medium` |
| Price | `text-[36px]` | - | `tracking-[-0.04em]` | default | uses `font-display-medium` |

### Body text (font-display, weight 400)

| Context | Size | Color | Line height |
|---|---|---|---|
| Lead paragraph | `text-[17px]` | `text-rl-dark-60` | `leading-[1.6]` |
| Body paragraph | `text-[16px]` | `text-rl-dark-60` | `leading-[1.6]` |
| Card description | `text-[14px]` | `text-rl-dark/60` | `leading-[1.6]` |
| Small/list text | `text-[13px]` | `text-rl-dark-60` | default |
| Footnote | `text-[13px]` | `text-rl-dark/30` | default |
| Trusted-by names | `text-[15px]` | `text-rl-dark/40` | default | uses `font-bold` |

### Labels and UI text

| Context | Size | Font | Color | Style |
|---|---|---|---|---|
| Section label | `text-[12px]` | `font-display-medium` | `text-rl-dark/40` | uppercase, tracking `0.04em` |
| Card category | `text-[11px]` | `font-mono` (Decima) | `text-rl-muted` | uppercase, tracking `0.06em` |
| Nav link | `text-[12px]` | `font-display` | `text-rl-dark-60` | uppercase, tracking `0.04em` |
| Button text | `12px` | `font-display` | depends on variant | uppercase, tracking `0.04em` |
| FAQ question | `text-[16px]` | `font-display-medium` | `text-rl-dark` | normal case |
| Browser URL bar | `text-[11px]` | `font-mono` | `text-[#191918]/40` | - |

---

## Section anatomy

Every content section follows this structure:

```
1. Section label:  orange dot + uppercase label
2. Heading:        large Parabole with emphasis word
3. Body text:      16px, max-w-[55ch]
4. Spacer:         <div class="h-10 md:h-14"></div>
5. Content grid:   cards / table / feature list
```

### Section label pattern

```html
<div class="flex items-center gap-2 mb-4">
  <span class="w-2 h-2 bg-[#fcaa2d]"></span>
  <span class="text-[12px] uppercase tracking-[0.04em] text-rl-dark/40 font-display-medium">Label</span>
</div>
```

### Section heading with emphasis

```html
<h2 class="text-[32px] md:text-[40px] leading-[110%] tracking-[-0.04em] max-w-[500px]">
  European AI infrastructure built for <em class="font-accent not-italic">trust</em>
</h2>
```

### Section body text

```html
<p class="text-[16px] text-rl-dark-60 leading-[1.6] mt-4 max-w-[55ch]">...</p>
```

---

## Spacing and layout

### Container

```
Max width:          max-w-[1064px]
Horizontal padding: px-5 (mobile), md:px-10 (desktop)
Centering:          mx-auto
```

### Navigation bar

```
Height:      h-[56px]
Background:  bg-[#fffef2]/90 backdrop-blur-md
Position:    sticky top-0 z-50
Border:      border-b border-transparent (becomes visible on scroll)
Logo height: h-5 (20px)
```

### Vertical section spacing

| Section type | Padding |
|---|---|
| Hero | `pt-16 md:pt-28 pb-0` |
| Standard sections | `py-16 md:py-24` |
| Between heading and content | `<div class="h-10 md:h-14"></div>` (explicit spacer) |
| Between heading and body text | `mt-4` |

### Grid gaps

| Context | Gap |
|---|---|
| Hero two-column | `gap-6 md:gap-12` |
| Content cards | `gap-4` |
| Pricing cards | `gap-3 md:gap-4` |
| Sticky sidebar layout | `gap-10 md:gap-16` |
| Card stack (vertical) | `space-y-4` |

---

## Content card (compact)

Cream card for listing items (products, features, steps). No painting background.

```
Container:    rounded-xl p-5 sm:p-6
Background:   style="background:#f3f2e7"  (inline - bg-rl-cream doesn't always render opaque)
Label:        text-[11px] font-mono text-rl-muted uppercase tracking-[0.06em]
Title:        text-[17px] tracking-[-0.02em] font-display-medium mt-1.5
Description:  text-[14px] text-rl-dark/60 leading-[1.6] mt-2
Stack gap:    space-y-4
Hover:        transition-shadow hover:shadow-md (only on clickable cards)
```

Use for: product feature cards, enumerated feature tiles, step-by-step items, use case cards.
Do not use for: pricing cards (those need CTAs and sit in painting frames).

---

## Pricing card

Pricing cards sit inside a painting background frame.

```
Container:       rounded-xl p-6 md:p-8 flex flex-col
Background:      style="background:#f3f2e7"
Featured border: border-2 border-[#fcaa2d]
Featured badge:  absolute -top-3 left-5, bg-[#fcaa2d], text-[10px] font-display-medium uppercase tracking-[0.06em] px-2.5 py-0.5 rounded-full
Price:           text-[36px] tracking-[-0.04em] font-display-medium
Price unit:      text-[13px] text-rl-dark/40
List bullet:     w-1 h-1 bg-[#fcaa2d] rounded-full
CTA (featured):  btn-accent mt-auto w-full justify-center
CTA (standard):  btn-ghost mt-auto w-full justify-center
```

---

## Painting background sections

Nature/landscape paintings as section backgrounds (pricing, comparison, final CTA).

### Light painting (pricing, comparison)

```html
<div class="relative rounded-2xl overflow-hidden">
  <img src="/bg-2.png" alt="" class="absolute inset-0 w-full h-full object-cover">
  <div class="relative p-4 sm:p-6 md:p-8 lg:p-10">
    <!-- content on top of painting -->
  </div>
</div>
```

### Dark painting with overlay (final CTA)

```html
<div class="relative rounded-2xl overflow-hidden border border-rl-border-light">
  <img src="/bg-7.png" alt="" class="w-full aspect-[16/9] object-cover">
  <div class="absolute inset-0 bg-[#191918]/70"></div>
  <div class="absolute inset-0 flex flex-col items-center justify-center text-center px-8">
    <!-- white text content -->
  </div>
</div>
```

---

## Comparison table

```
Container:     rounded-xl p-5 sm:p-8 md:p-10, style="background:#f3f2e7"
Sits inside:   painting background frame (rounded-2xl)
Header font:   text-[11px] sm:text-[13px] font-display-medium uppercase tracking-[0.04em]
Klai column:   text-[#fcaa2d] (accent color for header)
Body font:     text-[13px] sm:text-[15px]
Klai values:   font-display-medium (bold relative to other columns)
Other values:  text-rl-dark/40
Row border:    border-b border-rl-border/50
```

---

## FAQ (sticky sidebar + accordion)

```
Layout:         grid md:grid-cols-[1fr_1.5fr] gap-10 md:gap-16
Left column:    md:sticky md:top-24 (section label + heading + body)
Right column:   divide-y divide-rl-border/40
Trigger:        w-full flex items-center justify-between py-5
Question font:  text-[16px] font-display-medium
Answer font:    text-[14px] text-rl-dark-60 leading-[1.6]
Icon:           + sign (svg), rotates 45deg on open
Animation:      max-height 0 -> 300px, cubic-bezier(0.4,0,0.2,1) 0.35s
```

---

## Browser mockup

Product demo in a simulated browser window.

```
Container:       rounded-xl overflow-hidden border border-[#e3e2d8] shadow-[0_8px_40px_rgba(0,0,0,0.12)]
Chrome bar:      bg-[#fffef2] border-b border-[#e3e2d8] h-10
Traffic lights:  w-2.5 h-2.5 rounded-full, colors at 50% opacity
URL bar:         bg-[#f3f2e7] rounded-lg py-1.5 px-3, font-mono text-[11px] text-[#191918]/40
Body bg:         bg-[#FAFAF8]
Sidebar bg:      bg-[#f3f2e7]
Active item bg:  bg-[#E5E2D8]
```

---

## Shadows and elevation

| Context | Value |
|---|---|
| Browser mockup | `shadow-[0_8px_40px_rgba(0,0,0,0.12)]` |
| Card hover | `hover:shadow-md` |
| No other shadows | The design is intentionally flat. |

---

## Animations and motion

| Animation | Duration | Used for |
|---|---|---|
| `marquee` | 20s linear infinite | Scrolling feature strip |
| Scroll items | opacity 0.3 -> 1, 0.4s ease | Sticky sidebar scroll highlighting |
| FAQ accordion | max-height 0.35s cubic-bezier | Expand/collapse answers |
| FAQ icon | transform 0.3s ease | + rotates to x |

**Transition defaults:**
- Interactive elements: `transition-all duration-200` (buttons)
- Color-only changes: `transition-colors` (nav links, text)
- Card hover: `transition-shadow` (card elevation)

**No longer used:** shimmer, fade-up, meteor, beam animations from the previous design.

---

## Responsive patterns

| Pattern | Mobile | Desktop |
|---|---|---|
| Hero layout | Single column | `grid-cols-2 gap-12` |
| Section layout | Single column | Sticky sidebar `grid-cols-[1fr_1.5fr]` or `grid-cols-3` |
| Container padding | `px-5` | `md:px-10` |
| Section padding | `py-16` | `md:py-24` |
| Hero top padding | `pt-16` | `md:pt-28` |
| Heading size | `text-[32px]` | `md:text-[40px]` or `md:text-[48px]` |
| Nav links | Hidden | `hidden md:flex` |
| Browser sidebar | Hidden | `hidden sm:flex` |

---

## See Also

- Shared brand DNA: `styleguide.md` (auto-loads for website files)
- [rules/gtm/klai-brand-voice.md](../gtm/klai-brand-voice.md) - tone and writing style
