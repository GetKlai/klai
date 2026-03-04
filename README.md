# klai-claude

Gedeelde Claude-configuratie voor alle Klai-projecten.

Bevat MoAI-ADK agents, GTM content agents, skills, rules en hooks die
beschikbaar zijn in elk Klai-project: website, infra, app.

## Structuur

```
agents/
  moai/          MoAI-ADK agents (upstream, niet handmatig aanpassen)
  gtm/           GTM content agents (Klai-eigendom)
  klai/          Klai-eigen agents
rules/
  moai/          MoAI regels (upstream)
  gtm/           GTM regels incl. klai-brand-voice.md
  klai/          Klai-specifieke regels
skills/          MoAI skills (upstream)
hooks/           Gedeelde hooks
commands/        Gedeelde slash commands
output-styles/   MoAI output styles
scripts/
  update-moai.sh Update MoAI agents naar nieuwe versie
```

## Gebruik in een project

Projecten halen de gedeelde configuratie op via het `scripts/update-shared.sh`
script dat in elk projectrepo staat. Dat script kopieert de inhoud van deze
repo naar `.claude/` in het project.

```bash
# In klai-website, klai-infra of klai-app:
./scripts/update-shared.sh
```

## MoAI updaten

```bash
./scripts/update-moai.sh
```

Toont een diff, vraagt om bevestiging, vervangt `agents/moai/` en `rules/moai/`
volledig. Daarna VERSION aanpassen en committen.

## Versioning

Dit repo gebruikt semantic versioning (zie `VERSION`).
Projecten pinnen op een versie via `KLAI_CLAUDE_VERSION` in hun update-script.

- **patch** (1.0.1): kleine verbeteringen
- **minor** (1.1.0): nieuwe agent of MoAI update
- **major** (2.0.0): structuurwijziging

## GTM agents aanpassen

GTM agents (`agents/gtm/`) zijn Klai-eigendom. Aanpassingen direct in die
bestanden, in deze repo. Upstream GTM wordt handmatig als inspiratie gebruikt.

Klai-schrijfstijl staat in: `rules/gtm/klai-brand-voice.md`
