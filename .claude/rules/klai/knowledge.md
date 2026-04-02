# Klai Knowledge Base

Klai maintains a living knowledge base of patterns and pitfalls, built up through experience.
Always consult this before working on a relevant domain.

## Shared knowledge (.claude/rules/klai/)

| Domain | Patterns | Pitfalls |
|--------|----------|---------|
| Confidence protocol | `.claude/rules/klai/confidence.md` | — |
| Process (AI dev workflow) | — | `.claude/rules/klai/pitfalls/process.md` |
| Git | — | `.claude/rules/klai/pitfalls/git.md` |
| DevOps (Coolify, Docker) | `.claude/rules/klai/patterns/devops.md` | `.claude/rules/klai/pitfalls/devops.md` |
| Infrastructure (Hetzner, SOPS, env, DNS) | `.claude/rules/klai/patterns/infrastructure.md` | `.claude/rules/klai/pitfalls/infrastructure.md` |
| Backend (Python async, FastAPI, httpx) | `.claude/rules/klai/patterns/backend.md` (auto-loaded via `paths:`) | `.claude/rules/klai/pitfalls/backend.md` |
| Platform (LiteLLM, LibreChat, vLLM, Zitadel, Caddy) | `.claude/rules/klai/patterns/platform.md` | `.claude/rules/klai/pitfalls/platform.md` |
| Security (IDOR, multi-tenancy, authorization, per-resource ownership) | — | `.claude/rules/klai/pitfalls/security.md` |
| Frontend (i18n, component patterns) | `.claude/rules/klai/patterns/frontend.md` | — |
| Logging (structlog, VictoriaLogs, LogsQL) | `.claude/rules/klai/patterns/logging.md` / auto-loaded via `.claude/rules/klai/python-logging.md` (Python) and `.claude/rules/klai/logging.md` (frontend) | — |
| Code quality (ruff, pyright, ESLint) | `.claude/rules/klai/patterns/code-quality.md` | `.claude/rules/klai/pitfalls/code-quality.md` |
| UI design system (colors, typography, buttons) | Auto-loaded via `styleguide.md` (shared) + `portal-patterns.md` (portal) + `website-patterns.md` (website) | — |

## Project knowledge

Each project may have its own `docs/patterns/` and `docs/pitfalls/` directories.
Check the project's CLAUDE.md for the full list.

## When to read these files

- **Before making code changes** → read `.claude/rules/klai/pitfalls/process.md` — universal AI dev rules
- **Before committing** → check `pitfalls/git.md`
- **Deploying or changing infrastructure** → read `pitfalls/devops.md` and `pitfalls/infrastructure.md`
- **Writing Python async code** (httpx, asyncio.gather, external service calls) → read `pitfalls/backend.md`
- **Working on the AI stack** (LiteLLM, LibreChat, vLLM, Zitadel, Caddy) → read `pitfalls/platform.md` and `patterns/platform.md`
- **Managing secrets or env vars** → read `patterns/infrastructure.md` first
- **Fixing CI lint/type failures** → read `.claude/rules/klai/pitfalls/code-quality.md` and `.claude/rules/klai/patterns/code-quality.md`
- **Working on frontend UI** → `styleguide.md` + `portal-patterns.md` or `website-patterns.md` auto-load for matching files
- **Writing Python logging** → `python-logging.md` auto-loads for all Klai Python files; full guide in `.claude/rules/klai/patterns/logging.md`
- **Writing portal backend endpoints that access resources by ID** → read `.claude/rules/klai/pitfalls/security.md` — rule auto-loads via `.claude/rules/klai/multi-tenant-pattern.md`
- **Using external libraries** → use context7 MCP for current docs (not for internal code or business logic)
- **SPEC plan phase with external dependencies** → run context7 during research sub-phase
- **Working on a project-specific domain** → read that project's relevant domain files
- **Before reporting task completion** → confidence protocol loads automatically; provides evidence scoring and adversarial check guidance

## When to add new entries

- After any deployment incident or unexpected failure → run `/retro "what happened"`
- After solving a tricky infrastructure problem → run `/retro "what worked"`
- At the end of a SPEC cycle → the sync command will prompt for learnings

**Index maintenance:** Every pattern and pitfall file has an `## Index` table at the top. When adding a new entry to any of these files, also add a row to that file's index table. Keep the "Rule" or "When to use" column to one line.

## Index files

- Patterns index: `.claude/rules/klai/patterns.md`
- Pitfalls index: `.claude/rules/klai/pitfalls.md`
