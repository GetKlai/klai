# GTM Agents — Integratie Documentatie

Geïnstalleerd: 2026-03-03
Upstream: [gtmagents/gtm-agents](https://github.com/gtmagents/gtm-agents)

---

## Wat is dit?

Een set van 7 AI agents voor content, SEO en copywriting, gebaseerd op de open-source
[GTM Agents](https://github.com/gtmagents/gtm-agents) collectie (92 agents, 67 plugins).
We hebben de meest relevante agents geselecteerd en aangepast voor Klai:
NL/EN, Astro 5, Keystatic CMS, en Mark Vletter's schrijfstijl.

---

## Bestandsstructuur

```
.claude/
  agents/gtm/
    gtm-blog-writer.md          # Blogposts schrijven (NL/EN, Keystatic-ready)
    gtm-content-strategist.md   # Redactioneel plan, contentkalender
    gtm-thought-leader.md       # Executive content, LinkedIn, ghostwriting
    gtm-seo-architect.md        # Keyword strategie, topic clusters
    gtm-content-optimizer.md    # Bestaande content SEO-verbeteren
    gtm-conversion-copywriter.md # Landing pages, CTA's, email copy
    gtm-voice-editor.md         # Kwaliteitscheck + humanizer (laatste stap)

  rules/gtm/
    README.md                   # Dit bestand
    klai-brand-voice.md         # Schrijfwijzer Mark Vletter + Humanizer protocol

.claude-plugin/
  marketplace.json              # Registratie van de GTM Agents marketplace

scripts/
  update-gtm-agents.sh          # Check en download upstream wijzigingen
```

---

## Hoe gebruik je de agents?

Gewoon in gewone taal vragen. Claude selecteert automatisch de juiste agent.

**Voorbeelden:**
- "Schrijf een blogpost over AI in sales voor de NL blog"  → `gtm-blog-writer`
- "Maak een contentkalender voor Q2"  → `gtm-content-strategist`
- "Schrijf een LinkedIn post voor Mark over GTM automation"  → `gtm-thought-leader`
- "Wat zijn goede zoekwoorden voor onze blog?"  → `gtm-seo-architect`
- "Optimaliseer deze bestaande blogpost voor SEO"  → `gtm-content-optimizer`
- "Schrijf copy voor onze homepage hero sectie"  → `gtm-conversion-copywriter`
- "Check dit artikel op stijl en AI-patronen"  → `gtm-voice-editor`

---

## Aanbevolen workflow per blogpost

```
1. Strategie    gtm-content-strategist  → Contentbrief (onderwerp, keywords, CTA-doel)
2. SEO          gtm-seo-architect       → Keyword focus + zoekintentie
3. Schrijven    gtm-blog-writer         → Concept in Markdown + frontmatter
4. SEO-check    gtm-content-optimizer   → Titels, meta, interne links checken
5. Voice-check  gtm-voice-editor        → Schrijfwijzer + Humanizer toepassen
6. Publiceren   → Bestand in src/content/blog-nl/ of blog-en/ plaatsen
```

Stappen 3-5 kun je ook in één keer vragen:
"Schrijf een blogpost over X, optimaliseer voor SEO en pas mijn schrijfstijl toe."

---

## Schrijfstijl en Humanizer

Alle content-agents kennen Mark Vletter's schrijfwijzer en de humanizer-regels.
De volledige stijlgids staat in: `.claude/rules/gtm/klai-brand-voice.md`

**Kernprincipes:**
- Direct, informeel Nederlands ("je/jij", niet "u")
- Korte zinnen afgewisseld met langere uitleg
- Stellig waar gepast, kwetsbaar waar eerlijk
- Geen AI-clichés (crucial, delve, landscape, testament...)
- Output: alleen het artikel in Markdown, geen commentaar

**Toonbalans per type:**
| Type | Toon |
|------|------|
| Kennisartikel / analyse | Formeler, onderbouwd |
| How-to / praktisch | Informeler, Mark's eigen stijl |
| Persoonlijk / observatie | Volledig persoonlijk |

---

## Keystatic Frontmatter

Blogposts moeten dit schema volgen:

```yaml
---
title: ""
publishDate: "YYYY-MM-DD"
description: ""
author: ""
tags: []
featured: false
---
```

Bestanden komen in:
- `src/content/blog-nl/` — Nederlandse posts
- `src/content/blog-en/` — Engelse posts

---

## Updates draaien

### Onze agents updaten (klai-specifieke aanpassingen)
Aanpassingen in `.claude/agents/gtm/` of `.claude/rules/gtm/` gewoon committen naar main.
Dit zijn onze eigen bestanden — geen speciale procedure nodig.

### Upstream GTM Agents checken op wijzigingen
```bash
# Toon welke upstream files we tracken
./scripts/update-gtm-agents.sh

# Download upstream files naar /tmp voor vergelijking
./scripts/update-gtm-agents.sh --apply
```

Het script downloadt de upstream bronbestanden naar een tijdelijke map.
Vergelijk ze handmatig met onze versies en besluit zelf wat je overneemt.

**Let op:** onze agents zijn aangepast voor Klai. Upstream files zijn referentie,
geen directe vervanging. Altijd reviewen voor je iets overneemt.

### MoAI agents updaten
MoAI agents (`.claude/agents/moai/`) komen via de MoAI-ADK installatie.
Update via: `moai-adk update` of vervang de bestanden vanuit de upstream MoAI repo.

---

## Waarom deze aanpak?

**Waarom handmatig kopiëren en aanpassen, niet de plugin install?**
De GTM Agents `/plugin install` werkt prima voor een leeg project, maar onze agents
zijn sterk aangepast (NL/EN context, Klai branding, schrijfwijzer, Keystatic schema).
Door ze lokaal te beheren in `.claude/agents/gtm/` houd je volledige controle en
zijn aanpassingen direct zichtbaar in Git.

**Waarom `.claude/rules/gtm/klai-brand-voice.md` apart?**
De schrijfwijzer en humanizer zijn gedeeld over meerdere agents. Door ze als aparte
rule op te slaan, hoef je ze maar op één plek aan te passen. Alle agents laden de
rule automatisch via Astro's paths-matching in de frontmatter.

---

## Referenties

- Upstream repo: https://github.com/gtmagents/gtm-agents
- MoAI-ADK docs: `.claude/rules/moai/`
- Keystatic docs: https://keystatic.com/docs
- Schrijfwijzer: `.claude/rules/gtm/klai-brand-voice.md`
