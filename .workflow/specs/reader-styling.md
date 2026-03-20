# SPEC: Reader styling — match klai-website design

**Status:** DRAFT — awaiting approval
**Scope:** `klai-docs` public reader only (no editor, no API changes)
**Goal:** Apply the `www.getklai.com/docs/` visual identity to the `{org}.getklai.com/docs/{kb}` reader

---

## 1. What it should look like after

Reference: `www.getklai.com/docs/` (Astro site, `klai-website` repo)

Design tokens from `klai-website/src/styles/global.css`:

| Token | Value | Usage |
|---|---|---|
| `--color-purple-deep` | `#1A0F40` | Headings, strong text |
| `--color-purple-primary` | `#2D1B69` | Borders, code background |
| `--color-purple-accent` | `#7C6AFF` | Links, active nav, accents |
| `--color-sand-light` | `#F5F0E8` | Sidebar background |
| `--color-off-white` | `#FAFAF8` | Page background |
| `--font-serif` | Libre Baskerville | h1, h2 |
| `--font-sans` | Inter | body, UI |

---

## 2. What does NOT change

- No changes to routing, API, database, or auth logic
- `PageRenderer.tsx` markdown parsing logic untouched
- Wikilink resolution untouched
- No new dependencies for fonts (load from Google Fonts or use `next/font`)

---

## 3. Files to change

### 3a. `app/globals.css`

Add CSS custom properties and font-face declarations:

```css
@import "tailwindcss";

@source ".";
@source "../components";

@plugin "@tailwindcss/typography";

@theme {
  --color-purple-deep: #1A0F40;
  --color-purple-primary: #2D1B69;
  --color-purple-accent: #7C6AFF;
  --color-purple-muted: #4A3A8A;
  --color-sand-light: #F5F0E8;
  --color-sand-mid: #EAE3D5;
  --color-off-white: #FAFAF8;

  --font-serif: 'Libre Baskerville', Georgia, serif;
  --font-sans: 'Inter', system-ui, sans-serif;
}
```

For fonts: use `next/font/google` in `layout.tsx` to load Inter + Libre Baskerville (zero-CLS, self-hosted by Next.js).

### 3b. `app/layout.tsx`

Load fonts via `next/font/google`, apply to `<body>`:

```tsx
import { Inter, Libre_Baskerville } from 'next/font/google'

const inter = Inter({ subsets: ['latin'], variable: '--font-sans' })
const libreBaskerville = Libre_Baskerville({
  subsets: ['latin'],
  weight: ['400', '700'],
  style: ['normal', 'italic'],
  variable: '--font-serif',
})

export default function RootLayout({ children }) {
  return (
    <html lang="nl" className={`${inter.variable} ${libreBaskerville.variable}`}>
      <body className="font-sans">{children}</body>
    </html>
  )
}
```

### 3c. `app/(reader)/[...path]/page.tsx`

Update layout and typography classes:

**Root div:** `flex min-h-screen bg-white` → `flex min-h-screen bg-[var(--color-off-white)]`

**Main area:** `flex-1 px-8 py-10 max-w-3xl` → `flex-1 px-16 py-12 max-w-[780px]`

**h1:** `text-3xl font-bold mb-2` →
`font-[var(--font-serif)] text-[2rem] font-bold text-[var(--color-purple-deep)] mb-6 leading-tight`

**Description paragraph:** `text-gray-500` →
`text-[rgba(26,26,26,0.6)] text-[0.9375rem] leading-relaxed mb-8`

**Empty state h1:** same as above

**Empty state paragraph:** same as description

### 3d. `components/reader/Sidebar.tsx`

Full color swap from gray/blue → purple theme:

| Old class | New class |
|---|---|
| `w-64 shrink-0 border-r border-gray-100 bg-gray-50 min-h-screen px-4 py-6` | `w-64 shrink-0 border-r border-[rgba(45,27,105,0.08)] bg-[var(--color-sand-light)] min-h-screen px-5 py-6` |
| `text-sm font-semibold text-gray-800 mb-4 hover:text-blue-600` | `text-sm font-semibold text-[var(--color-purple-deep)] mb-6 hover:text-[var(--color-purple-accent)] transition-colors` |
| `text-xs font-semibold uppercase tracking-wide text-gray-400 mt-4 mb-1` | `text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-[rgba(26,26,26,0.35)] mt-4 mb-1` |
| `flex items-center gap-1 w-full text-xs font-semibold uppercase tracking-wide text-gray-400 mt-4 mb-1 hover:text-gray-600` | `flex items-center gap-1 w-full text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-[rgba(26,26,26,0.35)] mt-4 mb-1 hover:text-[var(--color-purple-deep)] transition-colors` |
| `text-gray-400 hover:text-gray-600 transition-colors` (expand button) | `text-[rgba(26,26,26,0.3)] hover:text-[var(--color-purple-deep)] transition-colors` |
| Active: `bg-blue-50 text-blue-700 font-medium` | Active: `bg-[rgba(124,106,255,0.07)] text-[var(--color-purple-accent)] font-medium` |
| Inactive: `text-gray-600 hover:text-gray-900 hover:bg-gray-100` | Inactive: `text-[rgba(26,26,26,0.6)] hover:text-[var(--color-purple-deep)] hover:bg-[rgba(45,27,105,0.04)]` |

### 3e. `components/reader/PageRenderer.tsx`

Update prose classes to use purple theme:

**Old:** `prose prose-gray max-w-none prose-a:text-blue-600 prose-a:underline`

**New:** custom prose overrides via CSS in `globals.css` instead of utility classes, because Tailwind Typography's `prose-a:` shorthand can't use arbitrary values. Add to `globals.css`:

```css
.klai-prose {
  --tw-prose-body: rgba(26, 26, 26, 0.72);
  --tw-prose-headings: #1A0F40;
  --tw-prose-links: #7C6AFF;
  --tw-prose-bold: #1A0F40;
  --tw-prose-counters: #7C6AFF;
  --tw-prose-bullets: #7C6AFF;
  --tw-prose-hr: rgba(45, 27, 105, 0.1);
  --tw-prose-quotes: #2D1B69;
  --tw-prose-quote-borders: #7C6AFF;
  --tw-prose-captions: rgba(26, 26, 26, 0.4);
  --tw-prose-code: #2D1B69;
  --tw-prose-pre-code: rgba(255, 255, 255, 0.85);
  --tw-prose-pre-bg: #1A0F40;
  --tw-prose-th-borders: rgba(45, 27, 105, 0.15);
  --tw-prose-td-borders: rgba(45, 27, 105, 0.08);
}

.klai-prose h1,
.klai-prose h2 {
  font-family: var(--font-serif);
}

.klai-prose a {
  text-decoration: none;
  border-bottom: 1px solid rgba(124, 106, 255, 0.4);
  transition: border-color 0.1s, color 0.1s;
}

.klai-prose a:hover {
  color: #2D1B69;
  border-bottom-color: #2D1B69;
}

.klai-prose code:not(pre code) {
  background: rgba(45, 27, 105, 0.06);
  border-radius: 4px;
  padding: 0.1em 0.35em;
  font-size: 0.875em;
}
```

Change component wrapper class: `prose prose-gray max-w-none prose-a:text-blue-600 prose-a:underline` → `prose max-w-none klai-prose`

---

## 4. Acceptance criteria

- [ ] Sidebar background is sand (`#F5F0E8`), border subtle purple tint
- [ ] Active nav item: purple accent background + text, not blue
- [ ] Section headers: tiny uppercase, muted — not gray-400
- [ ] KB name link: deep purple, not gray
- [ ] Page background: off-white (`#FAFAF8`)
- [ ] h1 uses Libre Baskerville serif font in deep purple
- [ ] Body text: dark with slight opacity (`rgba(26,26,26,0.72)`)
- [ ] Links: purple accent `#7C6AFF`, underline-as-border style
- [ ] Code blocks: dark purple background (`#1A0F40`)
- [ ] Inline code: subtle purple tint background
- [ ] Build passes (`npm run build`)
- [ ] No changes to editor, API, or auth

---

## 5. Out of scope

- Responsive/mobile layout
- Dark mode
- Header bar / breadcrumbs
- KB home page (no article selected) beyond basic styling
- Custom domain support
