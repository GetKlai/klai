---
paths:
  - ".claude/rules/klai/**"
  - ".claude/agents/klai/**"
---
# Knowledge Base Structure

Reference for adding learnings to `.claude/rules/klai/`. Used by `manager-learn` on `/retro`.

## Decision tree

1. **Platform component** (Caddy, LiteLLM, LibreChat, vLLM, Zitadel)? → `platform/{component}.md`
2. **Infrastructure** (servers, CI/CD, SOPS, env vars)? → `infra/deploy.md`, `infra/servers.md`, or `infra/sops-env.md`
3. **Language/tool** (Docker, Python, TypeScript, testing)? → `lang/{docker|python|typescript|testing}.md`
4. **Project-specific** (portal, website, docs, knowledge)? → `projects/{portal-backend|portal-frontend|portal-security|portal-logging-py|portal-logging-ts|website|docs|knowledge|python-services}.md`
5. **AI dev process rule** (debugging, verification, communication)? → `pitfalls/process-rules.md`
6. **Design/branding**? → `design/styleguide.md`

## Adding an entry

Add a new `##` section in the right file. No index file needed — `paths:` frontmatter handles loading.

**Pattern:** `## Name` / `**When:**` / explanation / commands / `**Rule:**`

**Pitfall:** `## Name (CRIT|HIGH|MED)` / what went wrong / `**Why:**` / `**Prevention:**`

New file only if the domain truly fits nowhere. Always include `paths:` frontmatter.
