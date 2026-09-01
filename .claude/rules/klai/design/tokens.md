---
paths:
  - "**"
---
# Klai Design Contract — Always-Loaded Entry Point

> Loads on every operation so an agent sees the design sources before choosing
> values in user-facing markup (HTML, CSS, JSX, email-template).
>
> Generated portal contract: `klai-portal/frontend/DESIGN.md`.
> Rules ledger: `klai-portal/frontend/docs/ui-standards.md`.
> Shared brand DNA: `.claude/rules/klai/design/styleguide.md`.
> Portal code source: `klai-portal/frontend/src/index.css` (CSS `@theme`).

## When you write user-facing markup, use these tokens

If you are about to render anything a Klai customer will see, use owned token
names instead of hardcoded values. For portal work, read `DESIGN.md` before
writing: it is the generated contract for current tokens, type scale, component
variants, and guidelines. Never edit it by hand; regenerate it with
`npm run docs:design` from `klai-portal/frontend/`.

The Rules Ledger in `docs/ui-standards.md` owns each rule's stable ID, level,
and verification mode. Component-owned rules live in the component header as
`@guideline` metadata. When choosing a portal foreground, consult the Colors
section of `DESIGN.md` first: legibility and semantic `-text` use are computed,
not guessed.

## Logo — canonical sources (CRIT)

[HARD] When you need a Klai logo for any rendered output (HTML email,
external preview image, embed, social card, third-party config), use
ONLY one of these URLs:

| Use case | URL | Format |
|---|---|---|
| **Email, external preview, embed (light bg)** | `https://getklai.com/logo-black.svg` | SVG, 316×98 viewBox, fill="black" |
| **Email, external preview, embed (dark bg)** | `https://my.getklai.com/klai-logo-white.svg` | SVG, white wordmark |
| Portal SPA in-app (light bg) | `/klai-logo.svg` (served by portal SPA) | SVG, identical content to website's `logo-black.svg` |
| Portal SPA in-app (dark bg) | `/klai-logo-white.svg` (served by portal SPA) | SVG |
| Browser favicon | `/favicon.svg` | SVG |

Recommended LOGO_WIDTH for email templates: `120` (renders ~120×37 with
the 316×98 aspect ratio — readable on mobile, not overwhelming).

### NEVER use these (old branding / deprecated)

[HARD] These files exist in some repos for legacy reasons but are the
OLD "ai" branding (dark blue lowercase "a" with green dot) — using them
in any user-facing surface ships the wrong brand:

| Path | Why forbidden |
|---|---|
| `klai-website/public/klai-icon-square.png` | Old "ai" mark, pre-rebrand |
| `klai-website/public/klay-icon.png` | Old "ai" mark, typo'd filename |
| `cdn.getklai.com/klai-logo.png` | DNS no-op, returns HTML 404 (not a real PNG) |
| `getklai.com/klai-logo.png` | 404, file doesn't exist on the website |
| `getklai.com/klai-logo.svg` | 404 — the file is named `logo-black.svg`, NOT `klai-logo.svg` |

### Quick verification before shipping

When configuring `LOGO_URL` for a service, the production envs, or any
external image reference, validate the URL serves a real image:

```bash
curl -sI <url> | grep -iE "^HTTP|content-type"
# MUST show: HTTP/2 200 + content-type: image/svg+xml (or image/png)
# If content-type is text/html → wrong URL, you got an HTML error page back
```

### Founders photo

| URL | Use |
|---|---|
| `https://getklai.com/founders.jpg` | Founders group photo (Jantine, Mark, Steven) — for email footers, about-us embeds |

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
- Writing a per-file font fallback chain. Use the owning font token so its
  current fallback remains single-sourced.

If a portal token is missing, add it to `index.css`, then regenerate
`DESIGN.md`. Do not invent ad-hoc tokens in feature code.
