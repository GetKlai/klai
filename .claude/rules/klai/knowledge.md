# Klai Knowledge Base

Klai maintains a living knowledge base of patterns and pitfalls, built up through experience.
Always consult this before working on a relevant domain.

## Shared knowledge (claude-docs/)

| Domain | Patterns | Pitfalls |
|--------|----------|---------|
| Process (AI dev workflow) | — | `claude-docs/pitfalls/process.md` |
| Git | — | `claude-docs/pitfalls/git.md` |
| DevOps (Coolify, Docker) | `claude-docs/patterns/devops.md` | `claude-docs/pitfalls/devops.md` |
| Infrastructure (Hetzner, SOPS, env, DNS) | `claude-docs/patterns/infrastructure.md` | `claude-docs/pitfalls/infrastructure.md` |
| Backend (Python async, FastAPI, httpx) | — | `claude-docs/pitfalls/backend.md` |
| Platform (LiteLLM, LibreChat, vLLM, Zitadel, Caddy) | `claude-docs/patterns/platform.md` | `claude-docs/pitfalls/platform.md` |
| Frontend (i18n, component patterns) | `claude-docs/patterns/frontend.md` | — |
| Logging (structlog, VictoriaLogs, LogsQL) | `claude-docs/patterns/logging.md` / auto-loaded via `.claude/rules/klai/python-logging.md` (Python) and `.claude/rules/klai/logging.md` (frontend) | — |
| Code quality (ruff, pyright, ESLint) | `claude-docs/patterns/code-quality.md` | `claude-docs/pitfalls/code-quality.md` |
| UI design system (colors, typography, buttons) | `claude-docs/styleguide.md` (full) / auto-loaded via `.claude/rules/klai/klai-ui-styleguide.md` | — |

## Project knowledge

Each project may have its own `docs/patterns/` and `docs/pitfalls/` directories.
Check the project's CLAUDE.md for the full list.

## When to read these files

- **Before making code changes** → read `pitfalls/process.md` — universal AI dev rules
- **Before committing** → check `pitfalls/git.md`
- **Deploying or changing infrastructure** → read `pitfalls/devops.md` and `pitfalls/infrastructure.md`
- **Writing Python async code** (httpx, asyncio.gather, external service calls) → read `pitfalls/backend.md`
- **Working on the AI stack** (LiteLLM, LibreChat, vLLM, Zitadel, Caddy) → read `pitfalls/platform.md` and `patterns/platform.md`
- **Managing secrets or env vars** → read `patterns/infrastructure.md` first
- **Fixing CI lint/type failures** → read `claude-docs/pitfalls/code-quality.md` and `claude-docs/patterns/code-quality.md`
- **Working on frontend UI** → styleguide rule auto-loads for `.astro`/`.tsx` files; full detail in `claude-docs/styleguide.md`
- **Writing Python logging** → `python-logging.md` auto-loads for all Klai Python files; full guide in `claude-docs/patterns/logging.md`
- **Working on a project-specific domain** → read that project's relevant domain files

## When to add new entries

- After any deployment incident or unexpected failure → run `/retro "what happened"`
- After solving a tricky infrastructure problem → run `/retro "what worked"`
- At the end of a SPEC cycle → the sync command will prompt for learnings

**Index maintenance:** Every pattern and pitfall file has an `## Index` table at the top. When adding a new entry to any of these files, also add a row to that file's index table. Keep the "Rule" or "When to use" column to one line.

## Index files

- Patterns index: `claude-docs/patterns.md`
- Pitfalls index: `claude-docs/pitfalls.md`
