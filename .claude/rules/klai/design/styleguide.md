---
paths:
  - "klai-portal/frontend/**"
  - "klai-website/**"
---

# Klai Styleguide

> Shared brand DNA for all Klai products (portal, website, future apps).
> Portal-specific patterns (tokens, sidebar, forms): `portal-patterns.md`
> Website-specific patterns (spacing, animations, buttons): `website-patterns.md`
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
| `--color-rl-dark-60` | `#19191899` | Body/paragraph text (60% opacity dark). |
| `--color-rl-dark-30` | `#1919184d` | Muted text, placeholders (30% opacity). |
| `--color-rl-dark-10` | `#1919181a` | Subtle borders, ghost button borders (10% opacity). |
| `--color-rl-accent` | `#fcaa2d` | Primary CTA buttons, section dots, badges, list markers. |
| `--color-rl-accent-dark` | `#a36404` | Dark amber for text links that need contrast. |
| `--color-rl-cream` | `#f3f2e7` | Card backgrounds, sidebar, input fields. |
| `--color-rl-border` | `#e3e2d8` | Card borders, dividers, table rules. |
| `--color-rl-border-light` | `#d1d0c666` | Subtle borders with transparency. |
| `--color-rl-muted` | `#bab9b0` | Label text, mono tags, FAQ icons. |

### Text colors (Tailwind usage)

| Token | Class | Usage |
|---|---|---|
| Primary text | `text-rl-dark` | Headings, card titles, strong emphasis. |
| Body text | `text-rl-dark-60` or `text-rl-dark/60` | Paragraphs, descriptions. |
| Muted text | `text-rl-dark/40` | Section labels, trusted-by logos, nav links. |
| Disabled/placeholder | `text-rl-dark/30` or `text-rl-dark/20` | Sidebar metadata, inactive items. |
| On dark bg | `text-white`, `text-white/60`, `text-white/40` | CTA sections with painting overlay. |

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
| `#fcaa2d` as text on `#fffef2` | **Fails AA - use as background only, or use `#a36404` for text** |

---

## Typography

One font family (Parabole) in multiple weights, plus Decima Mono for labels.

### Font stack

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

Light background: `text-rl-dark`, underline `decoration-rl-accent/60`, hover `text-rl-accent`.
Dark background: `text-white/70`, hover `text-white`.

---

## Border radius

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
2. **Never** use `#fcaa2d` as text color on light backgrounds - it fails contrast. Use as background or use `#a36404` for text.
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

- Portal patterns (tokens, sidebar, forms, cards, tables): `portal-patterns.md`
- Website patterns (buttons, spacing, animations, shadows): `website-patterns.md`
- [rules/gtm/klai-brand-voice.md](../gtm/klai-brand-voice.md) - tone and writing style
- [patterns/frontend.md](patterns/frontend.md) - technical frontend patterns (i18n, UI components)
