# Klai Knowledge — Strategische Positionering

> Vastgelegd: 2026-03-24
> Basis: sparringsessie op basis van marktonderzoek (`rag-market-research.md`)
> Status: Beslissingen vastgelegd — pricing uitgewerkt in `klai-pricing-framework.md`

---

## Het verhaal dat staat

### Overkoepelende positionering
**"Your private AI stack"** — privacy-first, EU/NL-gehost, PII blijft van jou.

Dit is het fundament. Niet vervangen door het knowledge-verhaal — knowledge is onderdeel van de stack, niet de vervanging ervan.

### De hook
**"Je team weet meer dan het weet."**

Dit is de brug tussen de privacypitch en het knowledge-product. Na de features-sectie komt een eigen narratief-sectie die uitlegt hoe alle losse producten samen het geheugen van de organisatie vormen.

### Volgende laag (later)
Eigen menu-item voor "Business Memory" of vergelijkbaar — pas als de sales-trigger duidelijker is in de praktijk.

---

## Productlijn (definitief voor nu)

| Product | Rol | GTM-functie |
|---|---|---|
| **Chat** | Privé ChatGPT-alternatief | Instap, PLG, creditcard |
| **Focus** | Document Q&A (NotebookLM-alternatief) | Instap, PLG |
| **Scribe** | Meeting transcriptie | Instap + upgrade-trigger naar Knowledge |
| **Knowledge** | Organisatiegeheugen (RAG + feedback loop) | De verbindende laag |
| **Docs** | Editable exposure layer → voedt terug in RAG | Unieke differentiator |

**De unieke differentiator:** Docs voeden terug in de kennislaag. Niemand anders heeft deze feedback loop. Dit is de architecturale moat.

**Org kennis voedt ook Chat:** Wanneer een gebruiker chat, gebruikt het systeem de organisatiekennis als context. Dat is niet "just another RAG" — dat is een geïntegreerde kennisoplossing.

---

## GTM-model

### Target
- **Bedrijfsgrootte:** 20–100 medewerkers
- **Buyer:** Zelfstandige beslisser met creditcard (partner, teamlead, operations manager)
- **Geen lange salescyclus** — zelfbediening, geen salesgesprek nodig bij instap
- **Schaalmodel:** "Start met jezelf. Groei naar je team." — klein experiment dat als een vlek de org in gaat

### Onboarding / cold start oplossing
Bij first install: bedrijfswebsite scraapen als eerste kennisbron (demonstratie van het concept). De echte waarde zit in interne documenten — dat is het eerste aha-moment.

### Upgrade-trigger (twee routes)

**Route 1 — Focus als gateway naar Knowledge**
Focus = upload documenten, stel vragen, krijg antwoorden. Eenmalig, per gebruiker.
Knowledge = Focus on steroids — persistent, gedeeld, voedt terug in de hele stack.

De natuurlijke upgrade-flow:
1. Gebruiker gebruikt Focus, ervaart de waarde
2. Realiseert: "Ik upload steeds dezelfde documenten" of "Ik wil dit delen met mijn team"
3. → "Zet je documenten permanent in Knowledge" — zelfde ervaring, nu voor de hele org

Dit is een schone PLG-upgrade: geen ander product, dezelfde kernervaring, meer waarde.

**Route 2 — Scribe als gateway naar Knowledge**
Iemand gebruikt Scribe voor meeting notes, wil die delen met het team → gedeelde kennisbank → Knowledge.

Beide routes leiden naar hetzelfde punt: de org-brede kennislaag. Focus is de sterkste route voor kenniswerkers (legal, finance, support). Scribe is de sterkste route voor teams die veel vergaderen.

---

## Website-architectuur

```
Hero             → "Your private AI stack. Jouw AI. Jouw data. Jouw regels."
Why Klai         → Privacy-probleem + het antwoord (blijft)
Product          → Chat · Focus · Scribe · Knowledge · Docs (5 producten)
Business Memory  → "Je team weet meer" — hoe alles samenkomt als org-geheugen
Ownership        → Steward-owned (blijft)
Pricing          → [open]
FAQ              → Blijft
```

**Sectorpagina's:** Komen later — `/support`, `/finance` etc. Niet op de homepage. Homepage spreekt universeel pijnpunt aan, sectorpagina's vertalen naar specifieke use cases.

---

## Sectorkeuzes

### Wel interessant
- **Support teams** — kennisbank-heavy, upgrade-trigger op team-niveau, duidelijke ROI
- **Finance (20-100 mnd)** — compliance-bewust, zelfstandige beslissers, betalen al voor gespecialiseerde software
- **Legal (20-100 mnd)** — geanonimiseerde dossiers als institutioneel geheugen, brieven die zichzelf schrijven. *Kanttekening: legal heeft al marktspecifieke oplossingen — sterke use case, maar concurrentieel complexer.*

### Bewuste keuze om uit te blijven
- **Healthcare** — AI Act high-risk classificatie, medische data, aparte compliance-wereld
- **HRM** — te gevoelig, HR-dossiers vragen een andere juridische context
- **Grote overheid / gemeentes** — incompatibel met PLG/creditcard-model (aanbesteding, IT-beleid). *Mensen bij overheid met een creditcard zijn welkom, maar we marketen er niet naar.*

### Niet op de site
Healthcare en HRM hoeven niet expliciet uitgesloten te worden op de website — gewoon niet noemen.

---

## Wat nog open staat

| Vraag | Prioriteit | Toelichting |
|---|---|---|
| **Pricing structuur** | 🔴 Hoog | Per user werkt voor Chat/Scribe. Hoe prijs je Knowledge? Per org? Per databron? Per module? Dit blokkeert CRO en copy. |
| **Upgrade-trigger** | 🟡 Middel | Hypothese: meeting notes. Valideren in de praktijk. Bepaalt hoe het product de "nodig je team uit"-moment ontwerpt. |
| **Homepage hero-tekst** | 🟡 Middel | "Your private AI stack" blijft, maar moet de uitgebreide productlijn verwerken (nu zijn Chat/Focus/Scribe de drie features). |
| **Sectorpagina's** | 🟢 Later | Structuur staat, content per sector moet nog geschreven worden. |
| **Business Memory menu-item** | 🟢 Later | Pas als sales-trigger in de praktijk duidelijker is. |

---

## Referenties
- Marktonderzoek: [`rag-market-research.md`](rag-market-research.md)
- Huidige website copy: `klai-website/src/i18n/nl.json` + `en.json`
- Productvisie: `klai-website/src/content/company/the-big-idea.md`
