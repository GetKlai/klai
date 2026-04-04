---
paths:
  - "klai-portal/frontend/src/**/*.ts"
  - "klai-portal/frontend/src/**/*.tsx"
---

# Logging Rules for klai-portal Frontend

## Setup

`frontend/src/lib/logger.ts` exports tagged loggers. Never use `console.log` directly -- ESLint will block it (`no-console: error`).

```ts
import { editorLogger } from '@/lib/logger'
```

Available loggers: `authLogger`, `editorLogger`, `queryLogger`, `treeLogger`, `adminLogger`, `focusLogger`, `helpLogger`, `taxonomyLogger`, `chatKbLogger`, `perfLogger`.
Need a new domain? Add `export const fooLogger = logger.withTag('foo')` to `lib/logger.ts`.

## Levels

| Level | When | Reaches Sentry in prod? |
|---|---|---|
| `debug` | Internal state, flow tracing, dev-only diagnostics | No |
| `info` | Business-significant user actions (save, delete, role change) | No |
| `warn` | Recoverable issues (missing optional data, fallback used) | Yes |
| `error` | Failures that affect the user (API 5xx, save failed) | Yes |

## Where to add logging

MUST log (warn/error):
- API error responses: endpoint, status, error message. Use `error` for 5xx/network, `warn` for 4xx (skip 401)
- Caught exceptions in try/catch blocks
- Auth failures: token refresh failure, unexpected logout (never log tokens)
- Data integrity: unexpected null/undefined from API responses

SHOULD log (info):
- Significant user actions: form submissions, destructive operations
- State transitions: auth state changes, feature toggles

MAY log (debug):
- Complex business logic: multi-step flows, conditional branches
- Cache behavior: hits/misses, stale data detection

NEVER log:
- Every render or re-render
- UI state (hover, focus, scroll)
- Sensitive data: passwords, tokens, API keys, email addresses
- High-frequency events: onChange per keystroke, mousemove
- Library internals

## Using logging to debug during development

When investigating a bug or unexpected behavior during a session:

1. Add targeted `debug` calls at the entry and exit of the suspected flow
2. Include structured context objects, not string concatenation:
   ```ts
   editorLogger.debug('Save flow start', { path, contentLength: html.length })
   editorLogger.debug('Save flow result', { status: res.status, ok: res.ok })
   ```
3. Run the application and observe the output in the browser console
4. Use the output to form a hypothesis, then fix the root cause

Before committing, review all debug calls you added:
- Keep calls that provide long-term diagnostic value (content format detection, cache behavior)
- Remove calls that were only useful for this specific investigation (temporary trace points)
- Never leave debug calls inside loops or high-frequency paths

## Rules summary

- Never use `console.log` -- use the tagged logger from `@/lib/logger`
- Always pass context as structured objects: `{ key: value }`, not template strings
- `warn` and `error` go to Sentry in production -- write them carefully with actionable context
- `debug` is free to use liberally during development; clean up noise before committing
