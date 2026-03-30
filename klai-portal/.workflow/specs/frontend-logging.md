# SPEC: Frontend Logging Setup

**Status:** DRAFT — awaiting approval
**Scope:** `klai-portal` frontend only
**Goal:** Structured, environment-aware logging using `consola` + native Sentry integration — replacing ad-hoc `console.log` across the codebase

---

## 1. Motivation

Currently the frontend has no consistent logging strategy:
- Debug `console.log` calls are scattered and ship to production
- There is no way to filter by module/component
- Sentry errors lack structured context (breadcrumb trail)
- Debugging issues like the wikilink race condition required guesswork instead of log data

---

## 2. What does NOT change

- Sentry DSN, org, project — already configured via `VITE_SENTRY_DSN`
- No new environment variables needed
- No changes to API, router, or query logic
- Existing `console.log` calls are replaced gradually — this SPEC covers setup + one example module

---

## 3. Technology decision

**Library: `consola`**

Reasons over alternatives:
- `Sentry.createConsolaReporter()` is a native integration (requires `@sentry/react >= 10.12.0` — we have `10.43.0`)
- `withTag('module')` gives per-module scoping with zero boilerplate
- Auto pretty-prints in dev (browser console), structured in prod
- Universal (browser + Node), works in Vite without config

**Not chosen:**
- `loglevel` — no native Sentry integration, no pretty-printing
- `debug` — for library authors, not application logging
- Custom wrapper — reinvents what consola provides

---

## 4. Log level strategy

| Environment | Console output | Sentry |
|---|---|---|
| `DEV` (local) | All levels (debug → fatal) | Nothing (enabled: false) |
| `PROD` | warn + error + fatal only | warn + error + fatal via reporter |

Level numbers (consola scale):
- 4 = debug
- 3 = info
- 1 = warn
- 0 = error/fatal

---

## 5. New files

### `frontend/src/lib/logger.ts`

```ts
import { createConsola } from 'consola/browser'
import * as Sentry from '@sentry/react'

const logger = createConsola({
  level: import.meta.env.DEV ? 4 : 1,
})

if (!import.meta.env.DEV) {
  logger.addReporter(Sentry.createConsolaReporter())
}

// Per-module loggers — import these instead of the root logger
export const authLogger   = logger.withTag('auth')
export const editorLogger = logger.withTag('editor')
export const queryLogger  = logger.withTag('query')
export const treeLogger   = logger.withTag('tree')
```

**Rules:**
- Never export the root `logger` directly — always use a tagged sub-logger
- Add a new tag per module/domain as needed
- Never use `console.log` directly in application code — use the logger

---

## 6. Changes to existing files

### `frontend/src/main.tsx` — add `enableLogs: true` to Sentry.init

```ts
Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  enabled: !import.meta.env.DEV,
  enableLogs: true, // activates Sentry structured logs API
  integrations: [
    Sentry.consoleLoggingIntegration({ levels: ['warn', 'error'] }),
  ],
  // ... rest of existing config unchanged
})
```

### `frontend/src/components/kb-editor/BlockPageEditor.tsx` — example usage

Replace debug `console.log` (if any) with:
```ts
import { editorLogger } from '@/lib/logger'

editorLogger.debug('Loading content', { format: 'html', length: initialContent.length })
editorLogger.debug('Inserting wikilink', { pageId, title, icon })
editorLogger.warn('initialContent empty on mount')
```

---

## 7. Usage guide for all future code

```ts
// Always import a named tagged logger, never console.log
import { editorLogger } from '@/lib/logger'

// Debug: internal state, useful during development only
editorLogger.debug('Parsing content', { startsWith: initialContent.slice(0, 20) })

// Info: business-significant user actions
editorLogger.info('Page saved', { path: selectedPath, duration: ms })

// Warn: recoverable issues
editorLogger.warn('Page index empty, wikilink picker will be empty')

// Error: failures that affect the user
editorLogger.error('Failed to save page', { path, status: res.status })
```

**What to log per domain:**

| Logger | What to log |
|---|---|
| `authLogger` | Token refresh, session expiry, login/logout |
| `editorLogger` | Content load/save, wikilink insert, format detection |
| `queryLogger` | Cache misses, fetch errors, stale data |
| `treeLogger` | DnD events, drop target calculation, tree mutations |

---

## 8. Installation

```bash
cd frontend && npm install consola
```

One new dependency, ~8 kB gzipped.

---

## 9. Acceptance criteria

- [ ] `consola` installed, `src/lib/logger.ts` created
- [ ] `Sentry.init` updated with `enableLogs: true` and `consoleLoggingIntegration`
- [ ] `BlockPageEditor.tsx` uses `editorLogger` as reference implementation
- [ ] Build passes (`npm run build`)
- [ ] In dev: debug logs visible in browser console with `[editor]` prefix
- [ ] No `console.log` added in new code after this point
- [ ] Pattern doc written in `klai-claude/docs/patterns/frontend.md`

---

## 10. Out of scope

- Replacing ALL existing `console.log` calls in one pass (do incrementally)
- Backend/API logging (separate concern)
- Log aggregation service (Sentry covers this for now)
