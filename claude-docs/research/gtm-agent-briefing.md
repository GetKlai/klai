# GTM Agent Briefing — Klai Knowledge

> Voor: gtm-launch-strategist, gtm-conversion-copywriter, gtm-cro-specialist, gtm-seo-architect
> Lees ook: `klai-knowledge-positioning.md` (strategie) en `rag-market-research.md` (markt)
> Website repo: `klai-website/` — Astro 5, Keystatic CMS, NL primair / EN secundair
> Pricing: `klai-pricing-framework.md`

## Relevante bestanden in de website repo

| Bestand | Inhoud | Voor welke agent |
|---|---|---|
| `src/i18n/nl.json` | Alle copy NL (= EN, beide zijn identiek) | conversion-copywriter, cro-specialist |
| `src/i18n/en.json` | Alle copy EN | conversion-copywriter |
| `src/components/sections/Features.astro` | Hoe de features-sectie gebouwd is | cro-specialist |
| `src/components/sections/Hero.astro` | Hero-sectie structuur | cro-specialist, conversion-copywriter |
| `src/components/sections/Pricing.astro` | Huidige pricing-sectie | cro-specialist, launch-strategist |
| `klai-claude/rules/gtm/klai-brand-voice.md` | Brand voice regels | conversion-copywriter, voice-editor |
| `klai-website/src/content/company/the-big-idea.md` | Productvisie en toon | conversion-copywriter |

**Let op:** NL en EN copy zijn inhoudelijk identiek. Schrijf altijd beide talen tegelijk.

---

## Wat je moet weten over het product

Klai is een **privé AI-stack** voor bedrijven van 20–100 medewerkers. Vijf producten:

| Product | Wat het doet |
|---|---|
| Chat | Privé ChatGPT-alternatief, EU-gehost, PII blijft van jou |
| Focus | Document Q&A — stel vragen over je eigen bestanden |
| Scribe | Meeting transcriptie + bestand-upload, veilig, Europese servers |
| Knowledge | RAG-laag over organisatiekennis — het geheugen van de org |
| Docs | Editable kennisdocumenten die terugvoeden in de RAG (**unieke differentiator**) |

**De architecturale moat:** Docs voeden terug in Knowledge. Niemand anders heeft deze feedback loop. Org-kennis voedt ook Chat — betere context bij elk gesprek.

---

## Wat elk product werkelijk kan (broncode-research, maart 2025)

> **Gebruik dit als grond voor alle copy. Verzin geen claims.**
> Geverifieerd via broncode (klai-scribe, klai-focus, klai-connector, klai-portal) + live product (screenshots).

### Chat
- Per-tenant LibreChat container, eigen Zitadel OIDC app per org
- LiteLLM routing via org-specifieke team key (klai-primary / klai-fast / klai-large)
- Gespreksgeschiedenis opgeslagen in LibreChat (MongoDB, EU-gehost)
- Geen extern LLM-contact: alles gaat via de eigen LiteLLM proxy
- **MCP-integratie:** vanuit Chat kan de MCP-server worden aangeroepen om te schrijven naar Docs-kennisbanken en naar de Knowledge-laag (persoonlijk en org-scope)

### Focus
- RAG over zelf-geüploade bronnen: **pdf, docx, xlsx, pptx, txt, md + URL + YouTube**
- 3 zoekmodes: narrow (alleen je eigen docs), broad (inferentie voorbij docs), web (+ live websearch)
- Geciteerde antwoorden: elke respons geeft bronvermeldingen terug (bestandsnaam + chunk)
- Chatgeschiedenis standaard opgeslagen (`save_history = True` per notebook)
- Persoonlijke of org-scoped notebooks
- Bronbestanden permanent opgeslagen op EU-server, chunks in Qdrant
- **Opslaan naar Knowledge:** Focus-resultaten kunnen via "Save to Knowledge" opgeslagen worden als pagina in de persoonlijke Docs-kennisbank (met type: feitelijk/procedureel/overtuiging/hypothese, tags, bronvermelding)

### Scribe — drie manieren, twee flows

**Manier 1: opnemen in de browser (Record-tab)**
- Gebruiker klikt "Start recording", browser gebruikt microfoon via MediaRecorder
- Audio lokaal opgenomen, daarna geüpload naar scribe-api
- Zelfde flow als bestand uploaden: resultaat is een `TranscriptionDraft`, nog NIET opgeslagen
- Gebruiker kiest expliciet om op te slaan

**Manier 2: bestand uploaden (Upload-tab)**
- Gebruiker uploadt audiobestand
- Audio verwerkt door zelf-gehoste Whisper (large-v3-turbo), audio NIET opgeslagen
- Resultaat is `TranscriptionDraft`, NIET automatisch opgeslagen
- Gebruiker kiest expliciet om op te slaan; kan hernoemen en verwijderen

**Manier 3: meeting bot (via Vexa)**
- Gebruiker geeft meeting-URL op + toestemming (`consent_given: true` verplicht)
- Bot jointe Google Meet/Zoom/Teams (max 2 gelijktijdig)
- Na afloop: audio tijdelijk opgehaald van Vexa, door Whisper gestuurd, daarna verwijderd
- Transcript **altijd automatisch opgeslagen** in de database (geen opt-in)
- Gebruiker kan het record achteraf verwijderen

**Wat NIET bestaat:** samenvattingen of actiepunten via de API, speaker diarization (in progress)
**Wat NOOIT opgeslagen wordt:** audio

### Knowledge
- Org-brede kennislaag (Qdrant vectors), persoonlijk en org-scope
- **Connector:** klai-connector synchroniseert GitHub-repositories naar Qdrant
  - Gepland: Google Drive, Notion, MS Docs (nog niet geïmplementeerd)
- **Docs → Knowledge:** Docs-inhoud voedt terug in de Knowledge-laag (behandel als live)
- **Geen Scribe → Knowledge** (ook niet gepland)
- Meerdere kennisbanken met verschillende toegangsniveaus (in ontwikkeling)

### Docs
- Kennisbank-editor met BlockNote rich-text editor
- Meerdere kennisbanken per org, elk met eigen naam
- Zichtbaarheid per kennisbank: **publiek** (org-breed) of **privé**
- Hiërarchische paginaboom, wiki-links tussen pagina's
- Toegangscontrole per kennisbank
- **Docs-inhoud voedt terug in de Knowledge-laag** — dit is de architecturale moat
- Verwijdering van een kennisbank verwijdert alle pagina's (onomkeerbaar)

---

## Positionering (niet onderhandelen)

**Hoofdlijn:** *"Your private AI stack"* — privacy-first, EU/NL-gehost, PII blijft van jou. Dit is het fundament. Niet vervangen door het knowledge-verhaal.

**De hook na de features:** *"Je team weet meer dan het weet"* — hoe alle producten samen het geheugen van de organisatie vormen.

**Wat Klai niet is:** "just another RAG" of een losstaande knowledge tool. Het is een geïntegreerde stack waarbij de kennislaag alle producten verbindt.

---

## Target & GTM-model

- **Buyer:** Zelfstandige beslisser met creditcard — partner, teamlead, ops manager
- **Bedrijfsgrootte:** 20–100 medewerkers
- **Model:** Zelfbediening, geen salesgesprek bij instap. "Start met jezelf. Groei naar je team."
- **Upgrade-trigger (hypothese):** Scribe → meeting notes delen → gedeelde kennisbank → team adopteert Knowledge
- **Geen focus op:** Grote overheid, healthcare, HRM (niet op de site)

---

## Website-architectuur (nieuw)

De huidige site heeft Chat, Focus en Scribe als drie features. Dit moet uitgebreid naar vijf, plus een nieuwe narratieve sectie na de features:

```
Hero             → "Your private AI stack" (blijft, tekst aanpassen voor 5 producten)
Why Klai         → Privacy-probleem + antwoord (blijft)
Product          → Chat · Focus · Scribe · Knowledge · Docs
Business Memory  → "Je team weet meer" — narratieve sectie, geen feature-lijst
Ownership        → Steward-owned (blijft)
Pricing          → [open — zie opdracht per agent]
FAQ              → Bijwerken voor Knowledge/Docs vragen
```

---

## Wat open staat per agent

### gtm-launch-strategist — PRIORITEIT 🔴
**Opdracht: pricing-framework uitwerken**

Huidige situatie: per gebruiker, flat rate voor Chat/Focus/Scribe. Maar Knowledge is een org-brede laag — de waarde zit niet in individuele gebruikers maar in de organisatiekennis die opgebouwd wordt.

Vragen om te beantwoorden:
- Wordt Knowledge een add-on op de per-user prijs, of een org-brede toeslag?
- Hoe prijs je de Docs-module?
- Hoe werkt de "start met jezelf, groei naar je team" expansie financieel — wordt het goedkoper per seat bij meer gebruikers?
- Welk pricing-model past bij zelfbediening én bij de compliance-buyer?

Referentie markt: Glean $100K–$500K voor 500+ medewerkers. Klai target 20–100 medewerkers — dat is een totaal andere prijsklasse. Wat is realistisch en wat stuurt de juiste adoptie?

### gtm-conversion-copywriter
**Opdracht: twee nieuwe secties schrijven (NL + EN)**

1. **Feature-sectie uitbreiden:** Knowledge en Docs toevoegen naast Chat/Focus/Scribe. Zelfde stijl als huidige feature-tabs.
2. **"Business Memory" narratieve sectie:** Na de features, legt uit hoe alles samenkomt. Hook: "Je team weet meer dan het weet." Geen feature-lijst — een verhaal. Lees `the-big-idea.md` voor de toon.

Lees eerst de huidige copy in `src/i18n/nl.json` en `src/i18n/en.json` voor de stijl.

### gtm-cro-specialist
**Opdracht: paginastructuur herzien**

De huidige pagina is gebouwd voor drie producten en één buyer (compliance manager). Met vijf producten en een bredere buyer-set (ook teamleads, ops managers) moet de flow herziend worden.

Specifiek:
- Waar in de flow introduceert de "Business Memory" sectie zich het beste?
- Hoe helpt de FAQ bij het wegnemen van de compliance-bezwaren rond Knowledge (rechten, wie ziet wat)?
- Is er een upgrade-CTA nodig in de features-sectie zelf?

### gtm-seo-architect
**Opdracht: sectorpagina's plannen**

De homepage spreekt een universeel pijnpunt aan. Sectorpagina's vertalen dit naar specifieke use cases. Plan de structuur voor:
- `/support` — support teams, gedeelde kennisbank voor agents
- `/finance` — financieel advies, geanonimiseerde dossiers
- `/legal` — juridische kennis, brieven die zichzelf schrijven (let op: concurrentieel segment)

Per pagina: zoekwoorden, structuur, CTA.

---

## Wat je NIET moet doen

- Privacy-positionering afzwakken of vervangen — dat is het fundament
- Healthcare of HRM noemen
- Grote enterprise-taal gebruiken (geen "enterprise-grade", geen "at scale")
- Pricing verzinnen zonder input van gtm-launch-strategist
- De ownership/steward-owned sectie aanraken — die blijft ongewijzigd
