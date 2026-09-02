---
paths:
  - "klai-portal/frontend/**"
  - "klai-website/**"
  - "klai-portal/backend/app/templates/**"
  - "klai-portal/backend/app/static/**"
---

# Klai Styleguide

> Shared brand DNA for all Klai products (portal, website, future apps).
> Portal-specific UI/UX patterns: `klai-portal/frontend/docs/ui-standards.md`
> Website-specific patterns (spacing, animations, buttons): `../projects/website.md`
> Source of truth: `klai-website/src/styles/global.css` and `klai-portal/frontend/src/index.css`.

---

## Design philosophy

Klai should feel calm, confident, and warm. Not a startup shouting for attention. Not an enterprise wall.

- **Calm over chaos.** Warm ivory space. Few elements per screen. One point per section.
- **One font family does everything.** Parabole in three weights handles headings, body, and UI. Decima Mono for labels only.
- **Movement with restraint.** No animations for their own sake. Scroll-triggered opacity and subtle hover responses only.
- **Show the product.** Screenshots, real interfaces, painting backgrounds. No stock photos of people behind laptops.
- **Two contexts.** USE side (warmer, more cream, human language) vs. BUILD side (darker, technical details).

---

## Colors

### CSS variables (defined in `@theme` in global.css)

| Variable | Hex | Usage |
|---|---|---|
| `--color-rl-bg` | `#fffef2` | Body background. Warm ivory. |
| `--color-rl-dark` | `#191918` | Primary text, headings, dark overlays. |
| `--color-rl-dark-60` | `#19191899` | Readable muted text on ivory only (4.57:1); it fails normal text on cream (4.42:1). |
| `--color-rl-dark-30` | `#1919184d` | Muted text, placeholders (30% opacity). |
| `--color-rl-dark-10` | `#1919181a` | Subtle borders, ghost button borders (10% opacity). |
| `--color-rl-accent` | `#fcaa2d` | Primary CTA buttons, section dots, badges, list markers. |
| `--color-rl-accent-dark` | `#a36404` | Dark amber text on ivory/plain light only (4.73:1); it fails on cream (4.26:1) and amber tints (4.28:1). |
| `--color-accent-text` | `#7D4D03` | Portal accent text on cream, tints or at small sizes (6.36:1 on cream; 6.39:1 on the amber tint). |
| `--color-rl-cream` | `#f3f2e7` | Card backgrounds, sidebar, input fields. |
| `--color-rl-border` | `#e3e2d8` | Card borders, dividers, table rules. |
| `--color-rl-border-light` | `#d1d0c666` | Subtle borders with transparency. |
| `--color-rl-muted` | `#bab9b0` | Label text, mono tags, FAQ icons. |

### Text colors (Tailwind usage)

| Token | Class | Usage |
|---|---|---|
| Primary text | `text-rl-dark` | Headings, card titles, strong emphasis. |
| Readable muted text | `text-rl-dark-60` or `text-rl-dark/60` | Ivory only (4.57:1); it fails normal text on cream at 4.42:1, so use a surface-verified darker foreground there. |
| Decorative-only text | `text-rl-dark/40` | Non-informative decoration only; never labels, nav links or other informative text (2.53:1 on ivory). |
| Disabled/placeholder | `text-rl-dark/30` or `text-rl-dark/20` | Sidebar metadata, inactive items. |
| Readable text on dark bg | `text-white` or `text-white/60` | CTA sections with painting overlay; white/60 passes normal text at 6.97:1. |
| Decorative-only on dark bg | `text-white/40` | Non-informative decoration only; it fails normal text at 3.81:1. |

### Semantic colors (in components)

| Color | Hex | Usage |
|---|---|---|
| Active green | `#3D6B35` | Knowledge base dots, status indicators. |
| Browser red | `#FF5F57` at 50% | Traffic light close button. |
| Browser yellow | `#FFBD2E` at 50% | Traffic light minimize button. |
| Browser green | `#28C840` at 50% | Traffic light maximize button. |

### Accessibility (WCAG)

| Combination | Result |
|---|---|
| `#191918` on `#fffef2` | Excellent contrast |
| `#191918` on `#f3f2e7` | Excellent contrast |
| `#fcaa2d` on `#191918` (button text on accent bg) | Good contrast |
| `#fcaa2d` as text on `#fffef2` | **Fails AA - use as background only** |
| `#a36404` as text on `#fffef2` | Passes at 4.73:1; use only on ivory/plain light, not cream or amber tints |
| `#7D4D03` as text on cream or amber tint | Portal accent text; passes at 6.36:1 and 6.39:1 respectively |

---

## Typography

One font family (Parabole) in multiple weights, plus Decima Mono for labels.

> [HARD] **The table below is the WEBSITE font stack only.** The portal binds
> the same token names to different faces and weights. `--font-display` is
> `"Parabole Trial Regular Text"` at 400 here and `"Parabole Medium"` at 500
> in the portal, and `--font-display-medium` does not exist in the portal at
> all. When you are editing `klai-portal/**`, the font tokens in
> `tokens.md` win over this section. Colors, logo and anti-patterns in this
> file are shared except where a section is explicitly marked website-only.
>
> Source of truth: `klai-website/src/styles/global.css` for the values below,
> `klai-portal/frontend/src/index.css` for the portal.

### Font stack (website)

| Variable | Font | Tailwind class | Weight | Usage |
|---|---|---|---|---|
| `--font-display` | Parabole Trial Regular Text | `font-display` | 400 | Body, headings, nav, buttons - default for everything. |
| `--font-display-medium` | Parabole Trial Medium Text | `font-display-medium` | 500 | Card titles, section labels, FAQ questions, comparison headers, prices. |
| `--font-display-bold` | Parabole Trial Bold Text | `font-display-bold` | 700 | Trusted-by names, user names in chat. Rarely used. |
| `--font-mono` | Decima Mono Pro Regular | `font-mono` | 400 | Card category labels (e.g., "Apps", "No. 1"), URL bar text. |

### Parabole accent variant (display/italic emphasis)

The `font-accent` class applies a display variant of Parabole used exclusively for emphasis words in headings:

```html
<h2>European AI infrastructure built for <em class="font-accent not-italic">trust</em></h2>
```

This creates the calligraphic emphasis on key words. Always used with `not-italic` to prevent browser italic rendering.

**Never use for:** buttons, nav, body text, or standalone text.

---

## Inline text links

Light background: `text-rl-dark`, underline `decoration-rl-accent/60`. Hover must stay legible: darken to the portal's `text-accent-text` on cream or tints; never change informative text to amber.
Dark background: `text-white/70`, hover `text-white`.

---

## Border radius

> [HARD] **The table below keeps the WEBSITE values and Tailwind names.** Do
> not translate them one-for-one into portal tokens or px. The portal uses a
> 110% rem base and maps `--radius-sm` to 0.375rem, `--radius-md` to 0.5rem,
> `--radius-lg` to 0.75rem and `--radius-xl` to 1rem; use the portal's token
> name from `index.css` / `DESIGN.md` instead of the website label or px note.

| Token | Value | Tailwind | Used for |
|---|---|---|---|
| Small | 0.5rem (8px) | `rounded-lg` | URL bars, sidebar items, input fields. |
| Card | 0.75rem (12px) | `rounded-xl` | Content cards, FAQ containers. |
| Section frame | 1rem (16px) | `rounded-2xl` | Painting backgrounds, comparison tables, CTA sections. |
| Pill | 999px | `rounded-full` | Buttons, badges, status dots, model pills. |

---

## Logo

| Variant | File | Usage |
|---|---|---|
| Black | `/logo-black.svg` | Nav bar, light backgrounds (default). |
| White | `/klai-logo-white.svg` | Dark overlays, painting CTA sections. |

Height: `h-5` (20px) in nav. Never distort or tint.

---

## Rules and constraints

1. **Never** use Parabole Bold for body text. It is reserved for emphasis (names, trust logos).
2. **Never** use `#fcaa2d` as text color on light backgrounds - it fails contrast. Use it as a background. For text, `#a36404` is limited to ivory/plain light; on cream, tints or at small sizes use the portal's `--color-accent-text` (`#7D4D03`).
3. **Never** add new colors without updating `global.css` and this file.
4. **Never** use animations for decoration. Scroll-triggered opacity and hover responses only.
5. **Never** use stock photos. Product screenshots or painting backgrounds only.
6. **Never** use em dashes (--) in content. House rule: use regular dashes or rewrite.

---

## Anti-patterns

| What | Why not |
|---|---|
| Neon glow gradients | Not Klai. Calm, not flashy. |
| Cold enterprise gray | Loses warmth. Use cream/ivory tones instead. |
| Stock photos of people | Says nothing about the product. |
| Auto-playing video | Breaks calm. |
| Multiple font families | Parabole handles everything. Do not introduce Inter/Manrope/etc. |
| Purple accent colors | Old brand. The new accent is amber `#fcaa2d`. |
| Many elements per screen | Dilutes the message. One point per section. |
| `rounded-lg` on cards | Cards use `rounded-xl`. Buttons use `rounded-full`. |

---

## See Also

- Portal UI standards: `klai-portal/frontend/docs/ui-standards.md`
- Portal compatibility rule entrypoint: `portal-patterns.md`
- Website patterns (buttons, spacing, animations, shadows): `../projects/website.md`
- [rules/gtm/klai-brand-voice.md](../gtm/klai-brand-voice.md) - tone and writing style
- [../projects/portal-frontend.md](../projects/portal-frontend.md) - technical frontend patterns (i18n, UI components)
