---
paths:
  - "**"
---
# Klai Design Tokens — Always-Loaded Reference

> Compact brand-token snapshot. Loads on every operation so any agent that
> writes user-facing markup (HTML, CSS, JSX, email-template) sees the
> source-of-truth tokens before deciding on values.
>
> Full patterns + components: `.claude/rules/klai/design/styleguide.md`,
> `.claude/rules/klai/design/portal-patterns.md`.
> Source of truth: `klai-portal/frontend/src/index.css` (CSS `@theme` block).

## When you write user-facing markup, use these tokens

If you are about to render anything a Klai customer will see — a React
component, an HTML template, a CSS file, an email — use the token names
below instead of hardcoding hex values. Drift across surfaces is the
exact failure mode this file exists to prevent (consent-page incident
2026-05-07: brand colors hardcoded in a backend Jinja-style template,
diverged from `index.css` because the design rules' `paths:` glob did
not match `klai-portal/backend/**/*.html`).

## Brand colors

| Token | Hex | Use |
|---|---|---|
| `--color-rl-bg` | `#fffef2` | Marketing-side background (warm ivory) |
| `--color-background` | `#faf9f6` | Portal app surface |
| `--color-rl-dark` / `--color-foreground` | `#191918` | Foreground text (near-black) |
| `--color-rl-accent` / `--color-primary` | `#fcaa2d` | CTA buttons, focus rings, active states |
| `--color-rl-accent-hover` | `#e89a1f` | Hover on primary CTA |
| `--color-rl-accent-dark` | `#a36404` | Accent text on light surfaces (links, dark labels) |
| `--color-rl-cream` / `--color-secondary` | `#f3f2e7` / `#f5f4ef` | Secondary surfaces, sidebar, detail blocks |
| `--color-rl-border` / `--color-border` | `#e3e2d8` / `#e8e6de` | Borders |
| `--color-destructive` | `#C0392B` | Destructive action (delete / deny) |
| `--color-warning` / `--color-warning-bg` | `#D97706` / `#FFFBEB` | Cautions, "newly registered" badges |
| `--color-success` / `--color-success-bg` | `#27AE60` / `#D1FAE5` | Confirmation states |

## Typography

| Token | Value | Use |
|---|---|---|
| `--font-sans` | `"Parabole Regular", system-ui, sans-serif` | Body text |
| `--font-display` | `"Parabole Medium", system-ui, sans-serif` | Headings, buttons |
| `--font-display-bold` | `"Parabole Bold", system-ui, sans-serif` | Strong emphasis, brand wordmark |
| `--font-mono` | `"Decima Mono", ui-monospace, monospace` | Code, technical labels, URLs |

Fonts are self-hosted at `/fonts/parabole-{regular,medium,bold,display}.woff2`
and `/fonts/decima-mono.woff2`. Both `my.getklai.com` and `getklai.com`
serve them. Always supply `font-display: swap` and a system fallback so
first-paint never blocks on a font fetch.

## Radius + spacing

- Border radii: `--radius-sm: 0.375rem`, `--radius-md: 0.5rem`, `--radius-lg: 0.75rem`, `--radius-xl: 1rem`.
- Buttons + cards default to `--radius-md`.
- Body baseline line-height: `1.6`. Headings: `1.25–1.3`.

## Logo

- Black-on-light: `/klai-logo.svg` (316×98 viewBox, fill="black").
- White-on-dark: `/klai-logo-white.svg`.
- Both are served by the SPA static handler. Cross-domain requests from
  backend-rendered pages on `my.getklai.com` resolve correctly via the
  Caddy fallthrough — no separate copy needed.

## Where these tokens MUST be honored

- React `.tsx` in `klai-portal/frontend/src/` (auto-loads
  `portal-patterns.md`)
- Static-served HTML/CSS in `klai-portal/backend/app/templates/` and
  `klai-portal/backend/app/static/` (e.g. OAuth consent page) — this
  rule is the only one that loads here, by design.

## Anti-patterns

- Hardcoded hex (`#fcaa2d`, `#191918`, etc.) in any `.tsx`, `.html`,
  `.css`, or `.j2`. Use `var(--color-rl-accent)` etc.
- Defining a new local CSS variable that re-declares a token (e.g.
  `--accent: #fcaa2d` in a component-scoped block). Reference the
  global token directly.
- Using a different font fallback chain per file. Always fall back via
  `system-ui` so missing-font behavior is predictable across surfaces.

If a token you need is missing here, add it to `index.css` first, then
mirror it in this file. Do not invent ad-hoc tokens in feature code.
