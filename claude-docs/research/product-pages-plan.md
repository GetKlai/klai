# Product Pages Plan

> Vastgelegd: 2026-03-24
> Status: Research fase — competitor scraping gestart

## Doel

Vijf dedicated productpagina's bouwen op basis van de beste conversie-patronen van directe concurrenten, herschreven in Klai brand voice. Daarna pas blog, company en security.

## URL-structuur

```
/product/chat
/product/focus
/product/scribe
/product/knowledge
/product/docs
```

NL-equivalenten: `/nl/product/[slug]` (zelfde structuur)

---

## Per product: inspiratiebronnen + Klai-specifieke angle

### /product/chat
**Competitor research:** librechat.ai
**Klai-specifieke angles:**
- Auto model-selectie (minder stroom, altijd de juiste tool voor de taak)
- Privacy by default — geen data training, EU-hosting
- Zelfde ervaring als ChatGPT, maar jouw data blijft van jou
- LibreChat als open source fundament (transparantie)

### /product/focus
**Competitor research:** notebooklm.google.com
**Klai-specifieke angles:**
- Zelfde kernervaring als NotebookLM, maar niet van Google
- Documenten verlaten je sessie nooit
- Gateway naar Knowledge: "upload steeds dezelfde docs? Zet ze permanent in Knowledge"
- Werkt met elk bestandstype

### /product/scribe
**Competitor research:** fireflies.ai + otter.ai
**Klai-specifieke angles:**
- Klai joint de vergadering — meteen een transcript of samenvatting
- EU-hosting: meeting-audio gaat niet naar Amerikaanse servers
- Geen US cloud (Otter.ai is AWS US-West — expliciet te counteren)
- Niets opgeslagen tenzij je dat wil

### /product/knowledge
**Competitor research:** notion.so/product/ai + glean.com + andere RAG-aanbieders
**Klai-specifieke angles:**
- De feedback loop: Docs → Knowledge → Chat (niemand anders heeft dit)
- Org-kennis als context in elke Chat-sessie
- Upgrade-pad vanuit Focus ("Focus on steroids, nu voor de hele org")
- Access controls: jij bepaalt wie wat ziet
- De kennis van Sarah is er nog als Sarah weg is

### /product/docs
**Competitor research:** notion.so (docs angle) + coda.io
**Klai-specifieke angles:**
- Documenten die terugpraten: elke update voedt de kennisbank
- Geen stale wiki — kennisbank groeit mee
- Altijd inbegrepen bij Knowledge
- De editable exposure layer van de architecturale moat

---

## Fase-indeling

### Fase 1 — Nu: competitor research (parallel)
Scrape alle competitor sites → opslaan als MD in `klai-claude/docs/research/competitors/`

### Fase 2 — Daarna: page structuur per product
Op basis van competitor research + Klai-positioning per product de pagina-structuur bepalen (sections, CTA-plaatsing, social proof-blokken).

### Fase 3 — Copy schrijven
gtm-conversion-copywriter schrijft NL + EN per pagina.
gtm-voice-editor reviewt voor publicatie.

### Fase 4 — Implementatie
Nieuwe Astro-pagina's bouwen in klai-website onder /product/[slug].

### Fase 5 — Na product pages
- /blog (SEO, top-of-funnel)
- /company (steward-owned verhaal, the big idea)
- /security (trust center, compliance blokkade opheffen)

---

## Agent-taakverdeling

| Taak | Agent |
|---|---|
| Competitor research scraping | general-purpose (parallel, 1 per product) |
| Page structuur | gtm-cro-specialist |
| Copy NL + EN | gtm-conversion-copywriter |
| Voice review | gtm-voice-editor |
| Implementatie | expert-frontend |

---

## Referenties
- Positionering: `klai-knowledge-positioning.md`
- Pricing: `klai-pricing-framework.md`
- Brand voice: `klai-claude/rules/gtm/klai-brand-voice.md`
- Marktonderzoek: `rag-market-research.md`
- Website architectuur onderzoek: (dit gesprek, 2026-03-24)
