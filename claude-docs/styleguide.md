# Klai UI Styleguide

> Authoritative visual and interaction reference for all Klai products (website, portal, future apps).
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
- **Accent glow effects:** `#7C6AFF` at 10–30% opacity as radial blur backgrounds.
- **Borders and dividers:** `#2D1B69` at 6–15% opacity (`rgba(45,27,105,0.1)`).
- **Active/hover states:** `#7C6AFF` for links and nav, `#4A3A8A` for secondary buttons.
- **Section backgrounds:** `#F5F0E8` and `#EAE3D5` for alternating light sections.

### Accessibility (WCAG)

`#7C6AFF` on `#F5F0E8` fails WCAG AA at small text sizes.

| Combination | Result |
|---|---|
| `#1A1A1A` on `#F5F0E8` | Excellent contrast |
| `#F5F0E8` on `#1A0F40` | Excellent contrast |
| `#7C6AFF` on `#1A0F40` | Fine for UI use |
| `#7C6AFF` on `#F5F0E8` (small text) | **Fails AA — use `#4A3A8A` instead** |

---

## Typography

Three fonts. Each has a strict role. Do not mix roles.

### Font stack

| Variable | Font | Tailwind class | Fallback |
|---|---|---|---|
| `--font-serif` | Libre Baskerville | `font-serif` | Georgia, serif |
| `--font-display` | Manrope | `font-display` | system-ui, sans-serif |
| `--font-sans` | Inter | `font-sans` | system-ui, sans-serif |

### Libre Baskerville (serif) — editorial weight

Used for: H1/H2 in prose (blog, docs, company pages), hero H1, blockquotes, italic emphasis (`<em>`).

**Never use for:** nav labels, buttons, form inputs, or any functional UI chrome.

| Context | Size | Weight |
|---|---|---|
| Hero H1 | `text-4xl` → `text-6xl` (36px–60px at 16px base) | 400 or 700 |
| Section H1/H2 prose | `text-2xl` → `text-xl` (24px–20px) | 400 or 700 |
| Blockquotes | Italic, same size as body | 400 italic |

Line height for headings: `leading-[1.1]` (tight). Tracking: `tracking-tight`.

### Manrope (display) — numbers and CTA labels

Used for: prices and billing numbers, CTA button labels, large stats (NumberTicker), comparison table column headers.

**Never use for:** body paragraphs or prose.

| Context | Size | Weight |
|---|---|---|
| Price display | `text-3xl` (30px) | 700 |
| CTA button label | `text-sm` (12px at 14px base) | 600 (`font-semibold`) |
| Large stats | `text-4xl`+ | 700 or 800 |

### Inter (sans) — everything else

Used for: body paragraphs, nav labels, sidebar links, secondary text, captions, metadata, form inputs.

Safe default: if in doubt, use Inter.

| Context | Size | Weight |
|---|---|---|
| Base body | 14px (`--text-base: 0.875rem`) | 400 |
| Lead/intro text | `text-lg` (18px) | 400 |
| Nav links | `text-sm` | 400 |
| Captions / metadata | `text-xs` (10–12px) | 400 |
| Labels in UI | `text-sm` | 500 (`font-medium`) |

Line height for body: `leading-relaxed` (1.625). Anti-aliasing: `-webkit-font-smoothing: antialiased`.

---

## Logo

| Variant | File | Usage |
|---|---|---|
| White | `/klai-logo-white.svg` | Dark backgrounds (nav on hero, sidebar, dark sections) |
| Default (color) | `/klai-logo.svg` | Light backgrounds |

**Sizes:**

| Context | Height |
|---|---|
| Top navigation | `28px`, width `auto` |
| Footer | Same as nav or slightly smaller |
| Sidebar (portal) | Proportional to sidebar width |

Do not distort or tint the logo. Always maintain aspect ratio.

---

## Buttons

### Primary button (ShimmerButton — variant `primary`)

```
Background:   #7C6AFF
Hover bg:     #6B5AEE
Hover shadow: 0 0 20px rgba(124, 106, 255, 0.3)
Padding:      px-6 py-3  (24px / 12px)
Radius:       rounded-lg (0.75rem / 12px)
Font:         font-display font-semibold text-sm tracking-wide
Text color:   white
```

Used for: primary CTAs (Get started, Buy, Sign up).

### Ghost button (ShimmerButton — variant `ghost`)

```
Border:     border border-white/20
Text:       text-white/80
Hover:      text-white, border-white/40
Padding:    same as primary
Radius:     same as primary
Font:       same as primary
```

Used for: secondary actions on dark backgrounds.

### Toggle / segmented button (e.g., pricing toggle)

```
Container:     bg-[#F5F0E8] rounded-lg p-1
Active item:   bg-white shadow-sm text-[#1A0F40], rounded-md, px-5 py-2 text-sm font-medium
Inactive item: bg-transparent text-[#1A1A1A]/40, same padding/radius/font
```

### Inline text link (light background)

```
Color:  #4A3A8A  (not #7C6AFF — fails contrast)
Hover:  #2D1B69
```

### Inline text link (dark background)

```
Color:  white/70 or white/50
Hover:  white/80 or white
```

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
| Standard sections | `py-24` to `py-32` (96–128px) |
| Content within sections | `mb-6` to `mb-12` between blocks |

### Grid gaps

| Context | Gap |
|---|---|
| Pricing cards | `gap-6` (24px) |
| Navigation links | `gap-8` (32px) |
| Hero grid columns | `gap-12` (48px) |
| Card content items | `gap-2` to `gap-4` (8–16px) |

---

## Border radius

| Token | Value | Tailwind | Used for |
|---|---|---|---|
| `--radius-sm` | 0.375rem (6px) | `rounded` | Small chips, tags |
| `--radius-md` | 0.5rem (8px) | `rounded-md` | Toggle items, small elements |
| `--radius-lg` | 0.75rem (12px) | `rounded-lg` | Buttons, input fields |
| `--radius-xl` | 1rem (16px) | `rounded-xl` | Cards, modals, panels |

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

Use restraint. Animate only to confirm interaction or ease content into view. Never animate for decoration.

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

**Never:** auto-playing video, confetti, parallax on every element, floating/bobbing elements.

---

## Sidebar (portal — dark variant)

The portal sidebar uses the dark purple palette.

```
Background:         #1A0F40  (--color-sidebar)
Text:               #F5F0E8  (--color-sidebar-foreground)
Border:             rgba(124, 106, 255, 0.15)
Active/hover item:  rgba(124, 106, 255, 0.15) background
Muted text:         rgba(245, 240, 232, 0.55)
```

---

## Semantic tokens (portal / shadcn)

The portal maps Klai brand tokens to shadcn/ui semantic names.

| Token | Value | Notes |
|---|---|---|
| `--color-background` | `#FAFAF8` | Page background |
| `--color-foreground` | `#1A1A1A` | Default text |
| `--color-card` | `#FFFFFF` | Card backgrounds |
| `--color-primary` | `#2D1B69` | shadcn primary (purple-primary) |
| `--color-primary-foreground` | `#FAFAF8` | Text on primary |
| `--color-secondary` | `#F5F0E8` | shadcn secondary (sand-light) |
| `--color-secondary-foreground` | `#2D1B69` | Text on secondary |
| `--color-muted` | `#EAE3D5` | Muted backgrounds |
| `--color-muted-foreground` | `#6B6B6B` | Muted text |
| `--color-accent` | `#7C6AFF` | Accent (purple-accent) |
| `--color-accent-foreground` | `#FAFAF8` | Text on accent |
| `--color-destructive` | `#C0392B` | Error / destructive actions |
| `--color-success` | `#27AE60` | Save confirm buttons, positive feedback icons |
| `--color-border` | `rgba(45,27,105,0.1)` | Default border |
| `--color-input` | `rgba(45,27,105,0.08)` | Input field background |
| `--color-ring` | `#7C6AFF` | Focus ring |

---

## Rules and constraints

1. **Never** put `font-serif` (Baskerville) on nav labels, buttons, or UI chrome. It is editorial, not functional.
2. **Never** use `#7C6AFF` for small text on sand backgrounds — use `#4A3A8A` instead.
3. **Never** add new colors without updating `global.css`/`index.css` and this document.
4. **Never** use `font-display` (Manrope) for body text.
5. **Never** use animations for decoration. Every animation must serve a purpose.
6. **USE side** (user-facing tools): lean warm, more sand, larger type, human language.
7. **BUILD side** (API/developer): lean dark, more purple, technical details, code snippets.

---

## Anti-patterns

| What | Why not |
|---|---|
| Neon glow gradients | Not Klai — that's Jasper/Copy.ai energy |
| Cold enterprise gray | Loses warmth |
| Stock photos of people | Says nothing about the product |
| Auto-playing video | Breaks calm |
| Over-designed Framer animations | Substance over style |
| Many elements per screen | Dilutes the message |
| `display:none/block` for content switching | Use proper state management |
| Em dashes (--) in content | House rule: use regular dashes or rewrite |

---

## See Also

- [Brand colors](https://www.getklai.com/company/brand-colors) — canonical color reference
- [UX & feel](https://www.getklai.com/company/ux-feel) — design philosophy
- [rules/gtm/klai-brand-voice.md](../../rules/gtm/klai-brand-voice.md) — tone and writing style
- [patterns/frontend.md](patterns/frontend.md) — technical frontend patterns (i18n, etc.)
