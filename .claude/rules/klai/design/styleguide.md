---
paths:
  - "klai-portal/frontend/**"
  - "klai-website/**"
---

# Klai Styleguide

> Shared brand DNA for all Klai products (portal, website, future apps).
> Portal-specific patterns (tokens, sidebar, forms): `projects/portal-frontend.md`
> Website-specific patterns (spacing, animations, buttons): `projects/website.md`
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

### Accessibility (WCAG)

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

## Inline text links

Light background: `#4A3A8A` (not `#7C6AFF` - fails contrast), hover `#2D1B69`.
Dark background: `white/70` or `white/50`, hover `white/80` or `white`.

---

## Border radius

| Token | Value | Tailwind | Used for |
|---|---|---|---|
| `--radius-sm` | 0.375rem (6px) | `rounded` | Small chips, tags |
| `--radius-md` | 0.5rem (8px) | `rounded-md` | Toggle items, small elements |
| `--radius-lg` | 0.75rem (12px) | `rounded-lg` | Buttons, input fields |
| `--radius-xl` | 1rem (16px) | `rounded-xl` | Cards, modals, panels |

---

## Logo

| Variant | File | Usage |
|---|---|---|
| White | `/klai-logo-white.svg` | Dark backgrounds (nav on hero, sidebar, dark sections) |
| Default (color) | `/klai-logo.svg` | Light backgrounds |

Height: `28px` in nav, proportional in sidebar. Never distort or tint.

---

## Rules and constraints

1. **Never** put `font-serif` (Baskerville) on nav labels, buttons, or UI chrome.
2. **Never** use `#7C6AFF` for small text on sand backgrounds - use `#4A3A8A` instead.
3. **Never** add new colors without updating `global.css`/`index.css` and this file.
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
| Em dashes (--) in content | House rule: use regular dashes or rewrite |

---

## See Also

- Portal patterns (tokens, sidebar, forms, cards, tables): `projects/portal-frontend.md`
- Website patterns (buttons, spacing, animations, shadows): `projects/website.md`
- [Brand colors](https://www.getklai.com/company/brand-colors) - canonical color reference
- [rules/gtm/klai-brand-voice.md](../../gtm/klai-brand-voice.md) - tone and writing style
