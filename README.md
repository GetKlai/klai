# klai-claude

Shared Claude configuration for all Klai projects.

Contains MoAI-ADK agents, GTM content agents, skills, rules and hooks that are
available in every Klai project: website, infra, app.

## Structure

```
agents/
  moai/          MoAI-ADK agents (upstream — do not edit manually)
  gtm/           GTM content agents (Klai-owned)
  klai/          Klai-built agents
rules/
  moai/          MoAI rules (upstream)
  gtm/           GTM rules including klai-brand-voice.md
  klai/          Klai-specific rules
skills/          MoAI skills (upstream)
hooks/           Shared hooks
commands/        Shared slash commands
output-styles/   MoAI output styles
scripts/
  setup.sh       Set up a new machine with the full klai/ workspace
  update-moai.sh Update MoAI agents to a new upstream version
```

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

## Updating MoAI

```bash
./scripts/update-moai.sh
```

Shows a diff, asks for confirmation, replaces `agents/moai/` and `rules/moai/`
completely. Then update VERSION and commit.

## Versioning

This repo uses semantic versioning (see `VERSION`). Projects pin to a version
via `KLAI_CLAUDE_VERSION` in their update script.

- **patch** (1.0.1): minor improvements, typo fixes, tone adjustments
- **minor** (1.1.0): new agent or skill added, MoAI update integrated
- **major** (2.0.0): structural change, breaking change in update script or directories

## GTM agents

GTM agents (`agents/gtm/`) are Klai-owned. Edit them directly in this repo.
Upstream GTM is used as inspiration only, not as an automatic source.

Writing style: `rules/gtm/klai-brand-voice.md`
