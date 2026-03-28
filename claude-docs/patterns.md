# Patterns

> Copy-paste solutions. Use these, don't reinvent.

## How to use

**When building**: Check if a pattern exists before writing new code
**After solving**: Add reusable solutions to the appropriate file
**In code**: Add `# AI-CONTEXT: See claude-docs/patterns/[category].md#[anchor]`

## Shared Pattern Files

| File | Contents |
|------|----------|
| [patterns/devops.md](patterns/devops.md) | Coolify deployments, Docker, service management, CI/CD |
| [patterns/infrastructure.md](patterns/infrastructure.md) | Hetzner, SOPS secrets, env management, DNS, SSH |
| [patterns/platform.md](patterns/platform.md) | LiteLLM, vLLM, LibreChat, Zitadel, Caddy, MongoDB per-tenant |
| [patterns/frontend.md](patterns/frontend.md) | i18n (Paraglide JS), frontend conventions, button placement |
| [patterns/code-quality.md](patterns/code-quality.md) | ruff, pyright, ESLint, pre-commit, CI quality gates |
| [patterns/testing.md](patterns/testing.md) | Playwright browser testing, permissions, GlitchTip debugging |

## Project Pattern Files

Each project has its own `docs/patterns/` directory for project-specific patterns.

| Project | File |
|---------|------|
| klai-website | [klai-website/docs/patterns/frontend.md](../../klai-website/docs/patterns/frontend.md) *(planned)* |

## Quick Reference

### DevOps

- **[coolify-env-update](patterns/devops.md#coolify-env-update)** - Update env var in SOPS and Coolify
- **[coolify-redeploy](patterns/devops.md#coolify-redeploy)** - Trigger a fresh deploy
- **[docker-rebuild-no-cache](patterns/devops.md#docker-rebuild-no-cache)** - Full rebuild after dependency changes
- **[atomic-env-deploy](patterns/devops.md#atomic-env-deploy)** - Write `.env` via temp file + atomic `mv`, never `cat >`

### Infrastructure

- **[sops-overview](patterns/infrastructure.md#sops-overview)** - How SOPS + age works in this project (files, keys, structure)
- **[sops-secret-edit](patterns/infrastructure.md#sops-secret-edit)** - Change or add a secret in core-01
- **[sops-secret-add](patterns/infrastructure.md#sops-secret-add)** - Add a brand-new secret end-to-end
- **[sops-disaster-recovery](patterns/infrastructure.md#sops-disaster-recovery)** - Rebuild server from scratch using SOPS backups
- **[sops-add-new-server](patterns/infrastructure.md#sops-add-new-server)** - Register a new server as a SOPS recipient
- **[ssh-server-access](patterns/infrastructure.md#ssh-server-access)** - Connect to Hetzner servers
- **[dns-propagation-check](patterns/infrastructure.md#dns-propagation-check)** - Verify DNS changes

### Frontend

- **[i18n-paraglide](patterns/frontend.md#i18n-paraglide)** - i18n with Paraglide JS (React + Vite + TanStack Router)
- **[portal-ui-components](patterns/frontend.md#portal-ui-components)** - Button placement, form structure, component rules

### Code Quality

- **[code-quality-portal-backend](patterns/code-quality.md#klai-portalbackend)** - ruff + pyright + pip-audit: run locally and in CI
- **[code-quality-portal-frontend](patterns/code-quality.md#klai-portalfrontend)** - ESLint + tsc: run locally and in CI
- **[code-quality-no-console](patterns/code-quality.md#the-no-console-rule)** - Use tagged logger, never console.log

### Testing

- **[playwright-workflow](patterns/testing.md#standard-workflow)** - Kill Brave, navigate, grant permissions, snapshot, close browser
- **[playwright-permissions](patterns/testing.md#3-grant-browser-permissions-mic-camera-etc)** - Grant mic/camera/etc. programmatically
- **[glitchtip-debugging](patterns/testing.md#debugging-with-glitchtip)** - Frontend error tracking via errors.getklai.com

### Platform (AI Stack)

- **[platform-litellm-vllm-config](patterns/platform.md#platform-litellm-vllm-config)** - Full LiteLLM config for Qwen3 dual-model
- **[platform-vllm-startup-sequence](patterns/platform.md#platform-vllm-startup-sequence)** - Sequential startup with health checks
- **[platform-mongodb-per-tenant](patterns/platform.md#platform-mongodb-per-tenant)** - One database per tenant via MONGO_URI
- **[platform-caddy-tenant-router](patterns/platform.md#platform-caddy-tenant-router)** - FastAPI dispatcher for per-tenant routing
- **[platform-zitadel-org-per-tenant](patterns/platform.md#platform-zitadel-org-per-tenant)** - Zitadel Organization provisioning
- **[platform-zitadel-user-role-assignment](patterns/platform.md#platform-zitadel-user-role-assignment)** - Assign project role to user at signup/invite
- **[platform-portal-users-mapping-only](patterns/platform.md#platform-portal-users-mapping-only)** - portal_users as thin mapping table, live Zitadel identity fetch
- **[platform-librechat-env-template](patterns/platform.md#platform-librechat-env-template)** - Required LibreChat `.env` settings
- **[platform-hetzner-dns-wildcard-tls](patterns/platform.md#platform-hetzner-dns-wildcard-tls)** - Custom Caddy build for wildcard TLS
- **[platform-vexa-bot-lifecycle](patterns/platform.md#platform-vexa-bot-lifecycle)** - Vexa meeting bot state machine, lifecycle config location, two resolution paths

## Anchor format

Use category-prefixed anchors:
- `#coolify-env-update`
- `#sops-secret-add`
- `#docker-rebuild-no-cache`

This enables splitting files later without breaking code references.

## Adding new patterns

1. Choose the appropriate file based on category
2. Add entry with category-prefixed anchor
3. Include copy-paste ready commands or code
4. Update the quick reference in this file
5. Run `/retro` to do this automatically after a solved problem

## Context loading strategy

Pattern files are NEVER @imported in CLAUDE.md. They load on-demand via `knowledge.md` references when Claude works in the relevant domain. This keeps CLAUDE.md lean and avoids wasting context tokens on domain knowledge that isn't needed every session.
