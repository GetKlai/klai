# Patterns

> Copy-paste solutions. Use these, don't reinvent.
> Each pattern file has its own index with anchored entries.

## Pattern files

| File | Contents |
|------|----------|
| [patterns/devops.md](patterns/devops.md) | Coolify deployments, Docker, service management, CI/CD |
| [patterns/infrastructure.md](patterns/infrastructure.md) | Hetzner, SOPS secrets, env management, DNS, SSH |
| [patterns/platform.md](patterns/platform.md) | LiteLLM, vLLM, LibreChat, Zitadel, Caddy, MongoDB per-tenant |
| [patterns/backend.md](patterns/backend.md) | Python/FastAPI service patterns | conditional (`paths:`) |
| [patterns/frontend.md](patterns/frontend.md) | i18n (Paraglide JS), frontend conventions, button placement |
| [patterns/code-quality.md](patterns/code-quality.md) | ruff, pyright, ESLint, pre-commit, CI quality gates |
| [patterns/testing.md](patterns/testing.md) | Playwright browser testing, permissions, GlitchTip debugging |

## When to read

- **When building**: Check if a pattern exists before writing new code
- **After solving**: Add reusable solutions via `/retro`

## Adding new patterns

1. Choose the appropriate file based on category
2. Add entry with category-prefixed anchor (e.g., `#sops-secret-add`)
3. Include copy-paste ready commands or code
4. Update that file's index table

## Context loading

Pattern files load on-demand via `paths:` frontmatter, not every session.

## [HARD] Auto-load rule for this directory

Every `.md` file in `.claude/rules/` **without** `paths:` frontmatter loads into EVERY Claude session. Before adding or moving a file into `.claude/rules/klai/patterns/`: add `paths:` frontmatter, or explicitly justify always-loading.
