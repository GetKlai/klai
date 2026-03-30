---
name: klai-portal-ui
description: >
  Klai portal UI conventions. Mandatory for any agent editing klai-portal/frontend/src/.
  Covers page layout (h1 left + back button right), component usage, color tokens,
  and the LiteLLM model policy (never use gpt-* or other US model names).
license: Apache-2.0
user-invocable: false
metadata:
  version: "1.0.0"
  category: "domain"
  status: "active"
  updated: "2026-03-24"
  tags: "klai, portal, frontend, ui, react, typescript"
---

# Klai Portal UI Conventions

**[HARD] Read this skill in full before editing any file in `klai-portal/frontend/src/`.**

---

## Page layout standard

Reference implementation: `klai-portal/frontend/src/routes/admin/users/invite.tsx`

Every page uses this structure:

```tsx
<div className="p-8 max-w-lg">           {/* max-w-3xl for wide content pages */}
  <div className="flex items-center justify-between mb-6">
    <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
      Page Title
    </h1>
    <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
      <ArrowLeft className="h-4 w-4 mr-2" />
      {m.some_back_label()}
    </Button>
  </div>
  {/* content */}
</div>
```

Rules:
- **h1 LEFT, back button RIGHT** — always one `flex items-center justify-between` row
- Never put the back button above the h1 in a separate block
- Never put back button bottom-left or in a footer
- `font-serif text-2xl font-bold text-[var(--color-purple-deep)]` on every h1

## Components: always use `components/ui/`

```tsx
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
```

Never use raw `<input>`, `<select>`, `<button>` with inline Tailwind in route files.

## Color tokens — never raw Tailwind colors

| Token | Use |
|---|---|
| `var(--color-purple-deep)` | Headings, primary text, active icons |
| `var(--color-muted-foreground)` | Secondary text, placeholders |
| `var(--color-destructive)` | Errors, delete actions |
| `var(--color-success)` | Save confirm buttons |
| `var(--color-border)` | Borders, dividers |

Never use `text-red-*`, `bg-red-*`, `text-green-*`, `bg-green-*` for semantic states.

## LiteLLM model policy [HARD]

Never use OpenAI/US model names (`gpt-*`, `claude-*`, `text-davinci-*`) in any Klai code.
Klai is EU-only. Use only LiteLLM aliases:
- `klai-primary` — Mistral Small (default for most tasks)
- `klai-fast` — Mistral Nemo (lightweight)
- `klai-large` — Mistral Large (complex reasoning)

## Full references

- `klai-portal/docs/ui-components.md` — full component reference with code examples
- `.claude/rules/klai/portal-patterns.md` — form patterns, table patterns, semantic tokens
- `.claude/rules/klai/styleguide.md` — shared brand DNA (colors, typography, rules)
