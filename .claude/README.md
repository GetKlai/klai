# klai-claude

Shared Claude configuration for all Klai projects.

Contains MoAI-ADK agents, GTM content agents, skills, rules and hooks that are
available in every Klai project: website, infra, app.

## Workflow

Typical flows for the most common scenarios:

```
New feature       /sparring → /plan → /run → /sync
Quick fix         /fix  (or /loop for repeated failures)
Code quality      /review → /clean → /coverage
E2E validation    /e2e
New project       /project
Knowledge capture /retro "what happened"
```

**Agent teams** (parallel execution, requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`):
Use `team-*` agents when tasks can run in parallel — e.g. backend + frontend + tests
simultaneously. For sequential or single-domain work, use `expert-*` agents directly.

**GTM content** (website only):
`gtm-launch-strategist` → sets direction and pricing → `gtm-content-strategist` → plans calendar
→ `gtm-blog-writer` / `gtm-conversion-copywriter` → creates content
→ `gtm-voice-editor` → final quality gate before publishing

## Structure

```
agents/
  moai/          MoAI-ADK agents (upstream — do not edit manually)
  gtm/           GTM content agents (Klai-owned, website only)
  klai/          Klai-built agents (synced to all projects)
commands/
  moai/          MoAI slash commands (upstream — do not edit manually)
  klai/          Klai-built slash commands (synced to all projects)
rules/
  moai/          MoAI rules (upstream — do not edit manually)
  gtm/           GTM writing rules (website only)
  klai/          Klai-specific rules (synced to all projects)
hooks/
  moai/          MoAI lifecycle hooks (upstream — do not edit manually)
skills/          MoAI skills (upstream — do not edit manually)
output-styles/
  moai/          MoAI output styles (moai, r2d2, yoda)
docs/
  patterns/      Reusable solutions per domain
  pitfalls/      Known mistakes and prevention per domain
  styleguide.md  UI design system (colors, typography, buttons)
  gtm/           GTM-specific documentation
scripts/
  setup.sh           Set up a new machine with the full klai/ workspace
  sync-to-root.sh    Sync klai assets to monorepo root .claude/
  update-moai.sh     Update MoAI in this repo (interactive diff + confirm)
  moai-update-all.sh Update MoAI in all initialized projects
```

## Commands

### MoAI commands (`commands/moai/`) — upstream, do not edit manually

| Command | What it does |
|---------|-------------|
| `/plan` | Create SPEC document with EARS format requirements and acceptance criteria |
| `/run` | Implement SPEC requirements using DDD/TDD methodology |
| `/sync` | Synchronize documentation, codemaps, create pull request, and capture learnings |
| `/fix` | Auto-detect and fix LSP errors, linting issues, and type errors |
| `/loop` | Iteratively fix issues until all resolved or max iterations reached |
| `/review` | Code review with security and @MX tag compliance check |
| `/clean` | Identify and safely remove dead code with test verification |
| `/coverage` | Analyze test coverage, identify gaps, and generate missing tests |
| `/e2e` | Create and run E2E tests with Chrome, Playwright, or Agent Browser |
| `/codemaps` | Scan codebase and generate architecture documentation in codemaps/ |
| `/mx` | Scan codebase and add @MX code-level annotations for AI context |
| `/project` | Generate project documentation (product.md, structure.md, tech.md, codemaps/) |
| `/feedback` | Collect feedback and create GitHub issue (bug report, feature request) |

### Klai commands (`commands/klai/`) — Klai-owned

| Command | What it does |
|---------|-------------|
| `/sparring` | CEO/product sparring session — challenge what you're building before writing a SPEC |
| `/retro` | Capture a pattern or pitfall from a recent incident or solved problem |

## Agents

### MoAI expert agents (`agents/moai/expert-*`) — upstream, do not edit manually

| Agent | What it does |
|-------|-------------|
| `expert-backend` | API design, authentication, database modeling, query optimization |
| `expert-frontend` | React, Vue, Next.js, component design, state management, accessibility |
| `expert-debug` | Error diagnosis, bug fixing, exception handling, troubleshooting |
| `expert-devops` | CI/CD, Docker, Kubernetes, deployment, infrastructure automation |
| `expert-security` | OWASP, vulnerability assessment, XSS, CSRF, secure code review |
| `expert-testing` | E2E, integration testing, load testing, coverage, QA automation |
| `expert-performance` | Profiling, benchmarking, memory analysis, latency optimization |
| `expert-refactoring` | Codemod, AST-based transformations, API migrations, large-scale changes |
| `expert-chrome-extension` | Chrome Extension Manifest V3, service workers, content scripts, chrome.* APIs |

### MoAI manager agents (`agents/moai/manager-*`) — upstream, do not edit manually

| Agent | What it does |
|-------|-------------|
| `manager-spec` | EARS-format requirements, acceptance criteria, user story documentation |
| `manager-tdd` | RED-GREEN-REFACTOR cycle for test-first development |
| `manager-ddd` | ANALYZE-PRESERVE-IMPROVE cycle for behavior-preserving refactoring |
| `manager-docs` | README, API docs, technical writing, markdown generation |
| `manager-git` | Commits, branches, PR management, merges, releases |
| `manager-project` | Project initialization, .moai configuration, scaffolding |
| `manager-quality` | TRUST 5 validation, code review, quality gates, lint compliance |
| `manager-strategy` | Architecture decisions, technology evaluation, implementation planning |

### MoAI builder agents (`agents/moai/builder-*`) — upstream, do not edit manually

| Agent | What it does |
|-------|-------------|
| `builder-agent` | Create sub-agents, agent blueprints, and custom agent definitions |
| `builder-plugin` | Claude Code plugins, marketplace setup, plugin validation |
| `builder-skill` | Create skills, YAML frontmatter design, knowledge organization |

### MoAI team agents (`agents/moai/team-*`) — upstream, do not edit manually

Team agents run in persistent agent teams (requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`).

| Agent | What it does |
|-------|-------------|
| `team-analyst` | Requirements analysis for team-based plan phase |
| `team-architect` | Technical architecture for team-based plan phase |
| `team-backend-dev` | Backend implementation in team-based development |
| `team-frontend-dev` | Frontend implementation in team-based development |
| `team-designer` | UI/UX design in team-based development |
| `team-tester` | Test writing in team-based development |
| `team-quality` | Quality validation in team-based development |
| `team-researcher` | Codebase exploration and research in team-based workflows |

### Klai agents (`agents/klai/`) — Klai-owned

| Agent | What it does |
|-------|-------------|
| `ceo-sparring` | Challenges problem framing using Four Risks, pre-mortem, and forcing questions |
| `manager-learn` | Writes patterns and pitfalls to the knowledge base in the correct format |

## GTM agents (`agents/gtm/`) — Klai-owned, website only

GTM agents are deployed to `klai-website` only, not synced to other projects.
Edit them directly in this repo. Writing style: `rules/gtm/klai-brand-voice.md`

| Agent | What it does |
|-------|-------------|
| `gtm-launch-strategist` | Product launches, feature announcements, pricing strategy, marketing psychology |
| `gtm-content-strategist` | Editorial roadmaps, content calendars, buyer journey mapping |
| `gtm-blog-writer` | Long-form, SEO-optimized blog posts for getklai.com (NL + EN) |
| `gtm-conversion-copywriter` | Landing pages, CTAs, email copy, ad copy |
| `gtm-cro-specialist` | Conversion rate optimization, A/B test design, signup flow analysis |
| `gtm-email-specialist` | Welcome sequences, lead nurture flows, onboarding and re-engagement emails |
| `gtm-growth-engineer` | Product-led growth, referral programs, churn prevention |
| `gtm-content-optimizer` | SEO content enhancement, on-page optimization, content audits |
| `gtm-paid-specialist` | Paid social media (LinkedIn, Meta) and search (Google Ads) — campaign structure, targeting, creative briefs |
| `gtm-seo-architect` | Keyword strategy, topic clusters, technical SEO specs |
| `gtm-thought-leader` | Organic social media — LinkedIn posts, executive POVs, thought leadership articles, keynote outlines |
| `gtm-voice-editor` | Brand voice review and editing — final quality gate before publishing |

## Rules

Rules are auto-loaded by Claude based on file path patterns. They do not need to be
invoked manually.

### Klai rules (`rules/klai/`) — synced to all projects

| Rule | What it does |
|------|-------------|
| `knowledge.md` | Points to the knowledge base — when to read patterns/pitfalls |
| `serena-workflow.md` | How to use Serena MCP for semantic code navigation |
| `server-secrets.md` | Hard rules for editing `/opt/klai/.env` — prevents secret truncation |
| `klai-ui-styleguide.md` | Auto-loads UI design rules for `.astro` and `.tsx` files |
| `klai-portal-ui.md` | Portal-specific UI component rules |
| `logging.md` | Logging standards and patterns |
| `context7-usage.md` | When and how to use Context7 for external library documentation |

### GTM rules (`rules/gtm/`) — website only

| Rule | What it does |
|------|-------------|
| `klai-brand-voice.md` | Brand voice, tone, and writing style for all Klai content |
| `klai-humanizer.md` | Patterns for making AI-generated content sound human |

## Knowledge base (`docs/`)

Living documentation built up through experience. Claude reads these automatically
based on the task domain (see `rules/klai/knowledge.md` for when).

### Patterns — reusable solutions

| File | Domain |
|------|--------|
| `docs/patterns/devops.md` | Coolify, Docker, container deployment |
| `docs/patterns/infrastructure.md` | Hetzner, SOPS, env vars, DNS |
| `docs/patterns/platform.md` | LiteLLM, LibreChat, vLLM, Zitadel, Caddy |
| `docs/patterns/frontend.md` | i18n, component patterns |
| `docs/patterns/code-quality.md` | ruff, pyright, ESLint, CI quality gates |
| `docs/patterns/testing.md` | Testing patterns and strategies |

### Pitfalls — known mistakes and prevention

| File | Domain |
|------|--------|
| `docs/pitfalls/process.md` | Universal AI dev workflow rules — read before making code changes |
| `docs/pitfalls/git.md` | Git mistakes — read before committing |
| `docs/pitfalls/devops.md` | Deployment and Docker pitfalls |
| `docs/pitfalls/infrastructure.md` | Infrastructure and secret management pitfalls |
| `docs/pitfalls/platform.md` | AI platform stack pitfalls |

Add new entries with `/retro "what happened"`.

## Scripts

| Script | What it does |
|--------|-------------|
| `scripts/setup.sh` | Set up a new machine with the full klai/ workspace |
| `scripts/sync-to-root.sh` | Sync klai assets (`agents/klai/`, `commands/klai/`, `rules/klai/`) to monorepo root `.claude/` |
| `scripts/update-moai.sh` | Update MoAI in this repo — shows diff, asks confirmation, replaces `agents/moai/` and `rules/moai/` |
| `scripts/moai-update-all.sh` | Update MoAI in all initialized projects |

## Using in a project

Projects pull shared configuration via their `scripts/update-shared.sh` script.
That script copies the contents of this repo into `.claude/` in the project.

```bash
# Inside klai-website, klai-infra or klai-app:
./scripts/update-shared.sh
```

## New machine setup

```bash
cd ~/Server/projects
git clone git@github.com:GetKlai/klai-claude.git klai/klai-claude
./klai/klai-claude/scripts/setup.sh
```

## Versioning

This repo uses semantic versioning (see `VERSION`). Projects pin to a version
via `KLAI_CLAUDE_VERSION` in their update script.

- **patch** (1.0.1): minor improvements, typo fixes, tone adjustments
- **minor** (1.1.0): new agent or skill added, MoAI update integrated
- **major** (2.0.0): structural change, breaking change in update script or directories
