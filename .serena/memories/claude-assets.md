# Claude Assets

## Location
All Claude Code assets live directly in the monorepo at `/Users/mark/Server/projects/klai/.claude/`.
There is no longer a separate `klai-claude` repo — everything was merged into the monorepo.

## Structure
```
.claude/
  agents/
    klai/       ← Klai-built agents (ceo-sparring, manager-learn)
    gtm/        ← GTM agents (blog-writer, seo-architect, etc.)
    moai/       ← MoAI-ADK upstream reference agents
  commands/
    klai/       ← Klai slash commands (/sparring, /retro)
    moai/       ← MoAI slash commands (/plan, /run, /sync, etc.)
  rules/
    klai/       ← Klai rules (loaded via CLAUDE.md @imports + paths: frontmatter)
      patterns/ ← Copy-paste solutions (devops, infrastructure, platform, frontend, etc.)
      pitfalls/ ← Mistakes to avoid (process-rules, git, devops, platform, backend, etc.)
    gtm/        ← GTM rules
    moai/       ← MoAI core rules (constitution, coding-standards, etc.)
  skills/       ← Skill definitions (moai-*, klai-portal-ui)
  hooks/        ← Claude Code hooks
  output-styles/ ← Output formatting
```

## Key Knowledge Base Files (in .claude/rules/klai/)
- `pitfalls/process-rules.md` — universal AI dev rules (loaded every session)
- `pitfalls/git.md` — before committing
- `pitfalls/devops.md` + `patterns/devops.md` — before infra work
- `pitfalls/platform.md` + `patterns/platform.md` — LiteLLM/LibreChat/Zitadel/Caddy
- `pitfalls/infrastructure.md` + `patterns/infrastructure.md` — SOPS, env, DNS
- `patterns/frontend.md` — i18n, component patterns
- `styleguide.md` — full UI design system (auto-loads for matching files)
- `patterns/logging.md` — structlog + VictoriaLogs patterns
- `patterns/code-quality.md` — ruff, pyright, ESLint

## Index files
- Patterns index: `.claude/rules/klai/patterns.md`
- Pitfalls index: `.claude/rules/klai/pitfalls.md`

## Commit destination
Work on Claude agents/rules/commands → commit in the monorepo (klai), not a separate repo.
