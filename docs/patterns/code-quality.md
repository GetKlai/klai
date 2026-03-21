# Code Quality

> Per-project quality tooling reference. What runs, where, and how to invoke it.

---

## Overview: what we use

| Tool | Purpose | Python | TypeScript |
|------|---------|--------|------------|
| ruff | Lint + format | ✅ | — |
| pyright | Type checking | ✅ | — |
| pip-audit | Dependency vulnerability scan | ✅ | — |
| ESLint 9 + typescript-eslint | Lint | — | ✅ |
| TypeScript compiler (tsc) | Type checking | — | ✅ (via build) |
| pre-commit | Local gate before commit | klai-portal only | — |

Tests are not yet written. pytest (Python) and Vitest (TypeScript) are the intended tools when tests are added.

---

## Per-project status

### klai-portal/backend

Full quality gate: pre-commit + CI both block on failure.

**Run locally:**
```bash
cd backend
uv run ruff check .          # lint
uv run ruff format --check . # format check
uv run ruff format .         # auto-fix formatting
uv run --with pyright pyright
uv run --with pip-audit pip-audit
```

**Pre-commit hooks** (`.pre-commit-config.yaml` at repo root):
- `ruff` + `ruff-format` on `backend/**`
- `pyright` on `backend/**/*.py`
- `pip-audit` on `backend/pyproject.toml` changes

**CI** (`.github/workflows/portal-api.yml`):
ruff check → ruff format --check → pyright → pip-audit → build Docker image → deploy

Config: `backend/pyproject.toml` under `[tool.ruff]` and `[tool.ruff.lint]`.

**ruff rules enabled:** E, F, I (imports), UP (pyupgrade), B (bugbear), S (security), RUF (ruff-specific)

Notable ignores:
- `B008` — FastAPI `Depends()` in default args (standard FastAPI pattern)
- `S101` — assert in tests is fine
- `S105` — too many false positives on config defaults
- `alembic/*` — auto-generated migrations excluded from UP, I, E501

---

### klai-portal/frontend

ESLint configured; TypeScript checked via tsc at build time.

**Run locally:**
```bash
cd frontend
npm run lint    # ESLint
npm run build   # also runs tsc -b
```

**CI** (`.github/workflows/portal-frontend.yml`):
npm ci → lint (ESLint) → build (includes tsc) → rsync to core-01

Config: `frontend/eslint.config.js`

**ESLint setup:** ESLint 9 flat config, `typescript-eslint recommendedTypeChecked`, react-hooks, react-refresh.

Notable rules:
- `no-console` errors (except `warn`/`error`) — use the tagged logger from `lib/logger.ts`
- `@typescript-eslint/no-unused-vars` — underscore prefix ignores `_varName`
- `@typescript-eslint/no-unsafe-*` disabled until API client types are generated

**No pre-commit hook for frontend.** ESLint runs only in CI.

---

### klai-infra/core-01/klai-mailer

Ruff configured; pyright available as dev dependency.

**Run locally:**
```bash
cd klai-infra/core-01/klai-mailer
uv run --group dev ruff check .
uv run --group dev ruff format --check .
uv run --group dev pyright
```

**No CI quality gate.** klai-mailer is deployed via docker-compose on core-01, not through a GitHub Actions build pipeline. Quality checks are local only.

Config: `pyproject.toml` under `[tool.ruff]`.

**ruff rules enabled:** E, F, I, UP, B, S, RUF (same as portal-api).

---

### klai-infra/core-01/klai-knowledge-mcp

Ruff + pyright configured; no CI gate (deployed via docker-compose on core-01).

**Run locally:**
```bash
cd klai-infra/core-01/klai-knowledge-mcp
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev pyright
```

Config: `pyproject.toml` under `[tool.ruff]`.

**ruff rules enabled:** E, F, I, UP, B, S, RUF (same as portal-api).

**Note:** Production uses `requirements.txt` + Docker. `pyproject.toml` is dev-only tooling.

---

### klai-docs

ESLint 9 flat config via `next/core-web-vitals` + `next/typescript`. TypeScript strict enabled.

**Run locally:**
```bash
npm install    # first time: installs eslint, eslint-config-next, @eslint/eslintrc
npm run lint   # next lint (uses eslint.config.mjs)
npm run build  # also runs TypeScript check
```

**No CI quality gate.** klai-docs has no GitHub Actions workflow.

Config: `eslint.config.mjs` — `next/core-web-vitals` + `next/typescript` presets.

---

### klai-website

ESLint 9 with `typescript-eslint` + `eslint-plugin-astro`. TypeScript strict via `astro/tsconfigs/strict`.

**Run locally:**
```bash
npm install    # first time: installs eslint, typescript-eslint, eslint-plugin-astro, globals
npm run lint   # eslint .
npm run build  # also runs TypeScript check via Astro
```

**No CI quality gate.** klai-website is deployed via Coolify, no GitHub Actions lint gate.

Config: `eslint.config.js` — `@eslint/js` recommended + `typescript-eslint` recommended + `eslint-plugin-astro` recommended.

---

## Adding pre-commit to a new Python project

```bash
# Install pre-commit
uv add --group dev pre-commit

# Create .pre-commit-config.yaml (copy from klai-portal, adjust paths)
pre-commit install
pre-commit run --all-files  # validate
```

## Pyright config

Pyright is invoked via `uv run --with pyright pyright` — no `pyrightconfig.json` needed for basic use. If you need custom settings (e.g., strict mode, exclude paths), add:

```json
// pyrightconfig.json at project root
{
  "pythonVersion": "3.12",
  "typeCheckingMode": "standard"
}
```

## The no-console rule

Frontend code must never use `console.log`. Use the tagged logger from `frontend/src/lib/logger.ts`:

```ts
import { editorLogger } from '@/lib/logger'
editorLogger.debug('info only visible in dev')
editorLogger.error('goes to GlitchTip in prod', { context })
```

ESLint enforces this with `'no-console': ['error', { allow: ['warn', 'error'] }]`.

---

## See Also

- [patterns/testing.md](testing.md) — Playwright browser testing
- [pitfalls/process.md](../pitfalls/process.md) — process rules (test-user-facing-not-imports, verify-full-flow)
