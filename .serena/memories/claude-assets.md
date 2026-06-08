# Claude Assets

## Location
Claude Code assets for this monorepo live in `.claude/`. Other repos may have
their own `.claude/` directories and should be treated as separate contexts.

## Structure (monorepo)
```
.claude/
  agents/
    klai/       ← Klai-built agents (ceo-sparring, manager-learn)
    gtm/        ← GTM agents when present in the relevant repo
    moai/       ← MoAI-ADK upstream reference agents
  commands/
    klai/       ← Klai slash commands (/sparring, /retro)
    moai/       ← MoAI slash commands (/plan, /run, /sync, etc.)
  rules/
    klai/       ← Klai rules (paths: frontmatter triggers loading)
      confidence.md
      serena.md
      pitfalls/process-rules.md
      design/styleguide.md
      infra/
      lang/docker.md, python.md, typescript.md, testing.md
      platform/caddy.md, litellm.md, librechat.md, vllm.md, zitadel.md
      projects/portal-backend.md, portal-frontend.md, portal-security.md,
               portal-logging-py.md, portal-logging-ts.md, website.md,
               docs.md, knowledge.md, python-services.md
      workflow/process-full.md
    gtm/        ← GTM rules when present in the relevant repo
    moai/       ← MoAI core rules
  hooks/
    klai/       ← Klai hooks (confidence-check.py, domain-context-injection.sh, git-safety-guard.sh)
    moai/       ← MoAI hooks
  skills/       ← Skill definitions
```

## Knowledge base routing
Decision tree in `.claude/rules/klai/knowledge-structure.md`:
1. Platform component → `platform/{component}.md`
2. Infrastructure → `infra/`
3. Language/tool → `lang/`
4. Project-specific → `projects/`
5. AI dev process → `pitfalls/process-rules.md`
6. Design/branding → `design/styleguide.md`

No index files. `paths:` frontmatter handles loading automatically.
That auto-loading is Claude-specific. Codex only auto-loads `AGENTS.md`.

## Key hooks
- `scripts/confidence-check.py` — blocks stop without confidence + evidence + self-review (>=80)
- `.claude/hooks/klai/domain-context-injection.sh` — injects domain context before DevOps commands
- `.claude/hooks/klai/git-safety-guard.sh` — blocks destructive git commands

## GTM agents
GTM agent details, brand voice, and content workflows belong in the private
`klai-private` repo or in the repo that owns the marketing website. Keep this
public memory limited to generic agent-tooling orientation.

## Commit destination
Work on Claude agents/rules/commands → commit in the monorepo (klai), not a separate repo.
klai-website Claude assets → commit from within `klai-website/`.
