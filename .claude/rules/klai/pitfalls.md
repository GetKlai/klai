# Pitfalls

> Mistakes we've made. Don't repeat them.
> Each category file has a full index table with ID, severity, trigger, and rule.

## Categories

| Category | File | Scope |
|----------|------|-------|
| [Process](../../../docs/pitfalls/process.md) | AI dev workflow, testing discipline, minimal changes | 14 entries |
| [Git](pitfalls/git.md) | Destructive commands, secrets in commits | 4 entries |
| [DevOps](pitfalls/devops.md) | Coolify, Docker, deployments, services | 3 entries |
| [Infrastructure](pitfalls/infrastructure.md) | Hetzner, SOPS, env vars, DNS, SSH | 12 entries |
| [Platform](pitfalls/platform.md) | LiteLLM, vLLM, LibreChat, Zitadel, Caddy, Grafana, Vexa | 33 entries |
| [Backend](pitfalls/backend.md) | Python async, FastAPI, prometheus_client | 5 entries |
| [Code Quality](pitfalls/code-quality.md) | ESLint, ruff, pyright, CI quality gates | 2 entries |
| [Docs-app](pitfalls/docs-app.md) | klai-docs (Next.js) integration from portal-api | 4 entries |
| [Vexa](pitfalls/vexa-leave-detection.md) | Meeting bot leave/end detection (Playwright DOM) | conditional (`paths:`) |

## Runbooks

| Runbook | File | Contents |
|---------|------|---------|
| [Platform Recovery](../../../docs/runbooks/platform-recovery.md) | Zitadel Login V2 deadlock, PAT rotation, portal-api outage | 3 procedures |
| [Uptime Kuma](../../../docs/runbooks/uptime-kuma.md) | Adding a new monitor to status.getklai.com | 7-step procedure |

## When to read

- **Before deploying**: `pitfalls/devops.md` and `pitfalls/infrastructure.md`
- **Before platform work**: `pitfalls/platform.md` for the relevant component
- **After an incident**: Run `/retro "what happened"`

## Process rules (always loaded)

Compact table: `pitfalls/process-rules.md` (loads every session).
Full descriptions with examples: `docs/pitfalls/process.md` (on-demand).

## Adding new pitfalls

1. Run `/retro "description"`
2. Or manually: add to the appropriate category file + update its index table
3. If project-specific, add to `[project]/docs/pitfalls/` instead

## Context loading

| Type | Location | Loading |
|------|----------|---------|
| Process rules (compact) | `pitfalls/process-rules.md` | Every session (in `.claude/rules/`) |
| Process rules (full) | `docs/pitfalls/process.md` | On-demand |
| Domain pitfalls | `pitfalls/[category].md` | On-demand via `knowledge.md` |
