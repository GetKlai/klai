# Klai-Claude: Canonical Source for Shared Claude Assets

This repo stores the canonical versions of all klai-specific Claude Code assets.
Assets are synced to the monorepo root `.claude/` via `scripts/sync-to-root.sh`.

For shared project instructions, see the root `CLAUDE.md` in the monorepo.

## What lives here

| Directory | Purpose | Synced to root? |
|---|---|---|
| `agents/klai/` | Klai-built agents | Yes |
| `agents/gtm/` | GTM content agents (deployed to klai-website) | No (website-specific) |
| `agents/moai/` | MoAI-ADK agents (upstream reference copy) | No (moai update manages root) |
| `commands/klai/` | Klai-built commands | Yes |
| `rules/klai/` | Klai-built rules (root-relative paths) | Yes |
| `rules/gtm/` | GTM rules (deployed to klai-website) | No (website-specific) |
| `docs/` | Patterns, pitfalls, styleguide | Loaded via @import in root CLAUDE.md |
| `scripts/` | Sync and update utilities | N/A |

## Sync workflow

```bash
# Sync klai assets to monorepo root .claude/
./scripts/sync-to-root.sh

# Update MoAI in all initialized projects
./scripts/moai-update-all.sh
```
