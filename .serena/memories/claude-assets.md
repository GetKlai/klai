# Claude Assets

## Location
All Claude Code assets live in the monorepo at `/Users/mark/Server/projects/klai/.claude/`.
klai-website has its own `.claude/` at `/Users/mark/Server/projects/klai/klai-website/.claude/` — separate git repo, separate Claude context.

## Structure (monorepo)
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
    klai/       ← Klai rules (paths: frontmatter triggers loading)
      confidence.md     ← always loaded
      serena.md         ← always loaded
      pitfalls/process-rules.md  ← always loaded
      design/styleguide.md
      infra/deploy.md, servers.md, sops-env.md
      lang/docker.md, python.md, typescript.md, testing.md
      platform/caddy.md, litellm.md, librechat.md, vllm.md, zitadel.md
      projects/portal-backend.md, portal-frontend.md, portal-security.md,
               portal-logging-py.md, portal-logging-ts.md, website.md,
               docs.md, knowledge.md, python-services.md
      workflow/process-full.md
    gtm/        ← GTM rules (brand-voice, humanizer, mark-tone-of-voice)
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

## Key hooks
- `scripts/confidence-check.py` — blocks stop without confidence + evidence + self-review (>=80)
- `.claude/hooks/klai/domain-context-injection.sh` — injects domain context before DevOps commands
- `.claude/hooks/klai/git-safety-guard.sh` — blocks destructive git commands

## GTM agents (klai-website only)
GTM agents (`gtm-blog-writer`, `gtm-blog-seo`, `gtm-voice-editor`, etc.) live in `klai-website/.claude/agents/gtm/`.
They are ONLY available when Claude is started from `klai-website/`. Invisible from monorepo root.

## Commit destination
Work on Claude agents/rules/commands → commit in the monorepo (klai), not a separate repo.
klai-website Claude assets → commit from within `klai-website/`.
