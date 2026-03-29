# Claude Assets (klai-claude)

## Location
`C:\Users\markv\stack\02 - Voys\Code\klai\klai-claude` → GitHub: GetKlai/klai-claude

## Structure
```
agents/
  klai/     ← klai-built agents (synced to monorepo root .claude/)
  gtm/      ← GTM agents (website-specific, not synced)
  moai/     ← MoAI-ADK upstream reference (moai update manages root)
commands/klai/    ← klai commands (synced)
rules/
  klai/     ← klai rules (synced)
  gtm/      ← GTM rules (website-specific)
  moai/     ← MoAI core rules
docs/       ← Patterns + pitfalls + styleguide (loaded via @import)
scripts/    ← sync-to-root.sh, moai-update-all.sh
```

## Sync workflow
```bash
./scripts/sync-to-root.sh       # sync klai assets to monorepo root .claude/
./scripts/moai-update-all.sh    # update MoAI in all initialized projects
```

## Key Knowledge Base Files
- `docs/pitfalls/process.md` — universal AI dev rules (read before any code change)
- `docs/pitfalls/git.md` — before committing
- `docs/pitfalls/devops.md` + `patterns/devops.md` — before infra work
- `docs/pitfalls/platform.md` + `patterns/platform.md` — LiteLLM/LibreChat/Zitadel/Caddy
- `docs/pitfalls/infrastructure.md` + `patterns/infrastructure.md` — SOPS, env, DNS
- `docs/patterns/frontend.md` — i18n, component patterns
- `docs/styleguide.md` — full UI design system

## Commit destination
Work on Claude agents/rules/commands → commit in klai-claude (NOT klai-mono)
