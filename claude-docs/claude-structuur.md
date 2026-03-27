# Klai: Claude Environment Structure

Standard for setting up Claude configuration across all Klai projects.
Update this document when making structural changes. Last updated: 2026-03-04.

---

## Principle

Every Klai repo can be used independently. Someone working only on the website
does not need infra code — but does need all shared Claude agents. That shared layer
(MoAI, GTM, Klai rules) is managed in a separate repo and distributed to projects
via an update script.

---

## Local directory structure

All Klai repos live locally inside a shared `klai/` directory:

```
~/stack/02 - Voys/Code/klai/
  CLAUDE.md            ← shared base instructions (created by setup.sh, not in Git)
  klai-claude/         ← GetKlai/klai-claude
  klai-website/        ← GetKlai/klai-website
  klai-infra/          ← GetKlai/klai-infra
  klai-app/            ← GetKlai/klai-app
```

### Why a CLAUDE.md at the klai/ level?

Claude Code automatically reads `CLAUDE.md` from all parent directories. The `klai/CLAUDE.md`
is therefore loaded in every project you open, without any additional configuration.
Its content is identical to `klai-claude/CLAUDE.md` and is created by `setup.sh`.

This file is not in Git — `klai/` is not a repo. It is created once during machine setup
and updated by `update-shared.sh`.

### What inherits and what does not

| What | Inherited via parent directory? |
|------|--------------------------------|
| `CLAUDE.md` | Yes — Claude walks up the directory tree |
| `.claude/agents/` | No — only loaded from the project root |
| `.claude/settings.json` | No — only loaded from the project root |

Agents are available via vendoring (copied by `update-shared.sh`), not via inheritance.

---

## Repo overview

| Repo | Contents | Owner |
|------|----------|-------|
| `GetKlai/klai-claude` | All shared Claude configuration | Klai (platform) |
| `GetKlai/klai-website` | Website (Astro, Keystatic) + website-specific Claude | Website team |
| `GetKlai/klai-infra` | Server infrastructure + infra-specific Claude | Infra team |
| `GetKlai/klai-app` | Application + app-specific Claude | App team |

---

## `klai-claude` repo: structure

```
klai-claude/
├── agents/
│   ├── moai/          ← upstream MoAI-ADK, never edit manually
│   ├── gtm/           ← Klai-owned (based on GTM upstream, fully adapted)
│   └── klai/          ← 100% Klai-built agents
├── rules/
│   ├── moai/          ← upstream MoAI rules, never edit manually
│   ├── gtm/           ← GTM rules incl. klai-brand-voice.md
│   └── klai/          ← Klai-specific rules
├── skills/
│   └── moai/          ← upstream MoAI skills
├── hooks/
│   └── moai/          ← upstream MoAI hooks
├── CLAUDE.md          ← shared base instructions for all projects
├── VERSION            ← semantic version (e.g. 1.2.0)
└── scripts/
    ├── setup.sh       ← set up a new machine
    └── update-moai.sh ← fetch new MoAI version and replace agents/moai/
```

---

## Project repo: `.claude/` structure

Every project follows the same base layout. Example for `klai-website`:

```
klai-website/
├── .claude/
│   ├── agents/
│   │   ├── moai/          ← copied from klai-claude, do not edit here
│   │   ├── gtm/           ← copied from klai-claude, do not edit here
│   │   └── website/       ← website-specific, edit here
│   ├── rules/
│   │   ├── moai/          ← copied from klai-claude
│   │   ├── gtm/           ← copied from klai-claude (incl. brand voice)
│   │   └── website/       ← website-specific
│   ├── skills/
│   │   └── moai/          ← copied from klai-claude
│   ├── hooks/             ← mix of shared and project-specific
│   └── settings.json
├── CLAUDE.md              ← website-specific additions
└── scripts/
    └── update-shared.sh   ← syncs agents/moai, agents/gtm, rules/ from klai-claude
```

---

## Ownership

| Layer | Contents | Where to edit | Who |
|-------|----------|---------------|-----|
| Upstream MoAI | `agents/moai/`, `rules/moai/`, `skills/moai/` | Never manually, only via update script | Script |
| GTM (Klai-owned) | `agents/gtm/`, `rules/gtm/` | `klai-claude/agents/gtm/` | Klai |
| Klai-built agents | `agents/klai/` | `klai-claude/agents/klai/` | Klai |
| Website-specific | `agents/website/`, `rules/website/` | `klai-website/.claude/agents/website/` | Website team |
| Infra-specific | `agents/infra/` | `klai-infra/.claude/agents/infra/` | Infra team |

Golden rule: never edit the copied moai/ or gtm/ directories inside a project repo.
All changes go through `klai-claude`.

---

## Update workflows

### Update MoAI (new version available)

```bash
# In klai-claude:
./scripts/update-moai.sh

# Script does:
# 1. Downloads new MoAI agents to a temp directory
# 2. Shows diff with current agents/moai/
# 3. On approval: replaces agents/moai/ completely
# 4. Commit: "chore: update MoAI agents to vX.Y.Z"
```

### Update GTM (manual)

GTM agents are Klai-owned. Upstream is a source of inspiration, not an automatic source.

```bash
# Review upstream at: https://github.com/gtmagents/gtm-agents
# Manually compare with klai-claude/agents/gtm/
# Take what is relevant, adapt for Klai context
# Commit in klai-claude
```

### Update a project to a new klai-claude version

```bash
# In a project repo (e.g. klai-website):
./scripts/update-shared.sh

# Script does:
# 1. Copies agents/moai/, agents/gtm/, rules/moai/, rules/gtm/, skills/ from klai-claude
# 2. Updates klai/CLAUDE.md (workspace-level, auto-loaded by Claude Code)
# 3. Shows which version of klai-claude is now active
```

---

## Versioning

`klai-claude` uses semantic versioning:

- **patch** (1.0.1): minor improvements, typos, tone adjustments
- **minor** (1.1.0): new agent or skill added, MoAI update integrated
- **major** (2.0.0): structural change, breaking change in update script or directories

Each project tracks which version of `klai-claude` it uses. This is set at the top of
`scripts/update-shared.sh`:

```bash
KLAI_CLAUDE_VERSION="1.2.0"
```

Projects upgrade deliberately, not automatically.

---

## What lives where in Git

| Contents | In Git? | Notes |
|----------|---------|-------|
| `klai-claude` agents, rules, skills | Yes | Full repo in Git |
| Vendored agents in project repo | Yes | Vendored, origin tracked via VERSION in scripts |
| `klai/CLAUDE.md` | No | Local workspace dir, created by setup.sh |
| `settings.local.json` | No | Machine-specific, always in .gitignore |
| Playwright auth sessions | No | Always in .gitignore |
| Build output / node_modules | No | Standard |

### Setting up a new machine

```bash
cd ~/Server/projects
git clone git@github.com:GetKlai/klai-claude.git klai/klai-claude
./klai/klai-claude/scripts/setup.sh
```

`setup.sh` clones the remaining repos, creates `klai/CLAUDE.md` and runs `update-shared.sh`
in each project.

---

## Naming conventions

| Type | Prefix | Example |
|------|--------|---------|
| MoAI upstream agent | none | `expert-backend.md` |
| GTM agent (Klai-owned) | `gtm-` | `gtm-blog-writer.md` |
| Klai-built agent | `klai-` | `klai-onboarding.md` |
| Project-specific agent | `[project]-` | `website-keystatic.md` |
