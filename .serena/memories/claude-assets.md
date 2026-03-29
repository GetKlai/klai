# Claude Assets

## Location
All Claude Code assets live directly in the monorepo at `/Users/mark/Server/projects/klai/.claude/`.
There is no longer a separate `klai-claude` repo — everything was merged into the monorepo.

## Structure
```
.claude/
  agents/
    klai/       ← Klai-built agents
    gtm/        ← GTM agents
    moai/       ← MoAI-ADK upstream reference
  commands/klai/  ← Klai slash commands
  rules/
    klai/       ← Klai rules (loaded via CLAUDE.md @imports)
    gtm/        ← GTM rules
    moai/       ← MoAI core rules
  skills/       ← Skill definitions
  hooks/        ← Claude Code hooks

claude-docs/    ← Living knowledge base (patterns + pitfalls + styleguide)
  patterns/
    frontend.md
    platform.md
    devops.md
    infrastructure.md
    logging.md
    code-quality.md
  pitfalls/
    process.md
    git.md
    devops.md
    platform.md
    infrastructure.md
    backend.md
    code-quality.md
  styleguide.md
  patterns.md   ← index
  pitfalls.md   ← index
```

## Key Knowledge Base Files
- `claude-docs/pitfalls/process.md` — universal AI dev rules (read before any code change)
- `claude-docs/pitfalls/git.md` — before committing
- `claude-docs/pitfalls/devops.md` + `claude-docs/patterns/devops.md` — before infra work
- `claude-docs/pitfalls/platform.md` + `claude-docs/patterns/platform.md` — LiteLLM/LibreChat/Zitadel/Caddy
- `claude-docs/pitfalls/infrastructure.md` + `claude-docs/patterns/infrastructure.md` — SOPS, env, DNS
- `claude-docs/patterns/frontend.md` — i18n, component patterns
- `claude-docs/styleguide.md` — full UI design system

## Commit destination
Work on Claude agents/rules/commands → commit in the monorepo (klai), not a separate repo.
