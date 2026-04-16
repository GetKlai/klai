# Product Marketing Context

*Last updated: 2026-04-16*
*Source: klai-private/research/ — hypotheses, not validated by customer data yet*
*Productbeslissing april 2026: publieke lineup beperkt tot Chat, Focus, Knowledge. Scribe en Docs zijn in beperkte beta bij testklanten.*

---

## Product Overview

**One-liner:** Klai is een privé AI-stack voor Europese bedrijven — jouw AI, jouw data, jouw regels.

**What it does:** Klai biedt drie AI-producten (Chat, Focus, Knowledge) op Europese infrastructuur. PII en bedrijfsgevoelige data blijven volledig onder eigen controle. De primaire markt: bedrijven die een privé ChatGPT-alternatief willen dat in Europa wordt gehost en waarbij PII verwerkt mag worden. Knowledge is de werkelijke kracht van de stack — technologisch uniek en ver voor op de markt — maar wordt niet als primaire verkoopargument gebruikt.

**Product category:** Private AI stack / European AI infrastructure

**Product type:** SaaS, product-led growth

**Business model:** Flat-rate per gebruiker, zelfbediening, creditcard. Geen salesgesprek bij instap. Schaalmodel: "Start met jezelf. Groei naar je team."

---

## Products

### Publiek beschikbaar

| Product | Wat het doet | GTM-rol |
|---------|-------------|---------|
| **Chat** | Privé AI-chat met eigen kenniscontext — EU-gehost, LiteLLM routing, MCP-integratie. Focus is hierin geïntegreerd als "scoped knowledge" (docs/URLs aan kennisbank toevoegen en direct chatten) | Primaire instap, PLG, onboarding-kern |
| **Knowledge** | Org-brede RAG-laag, geavanceerde connectors (Confluence, Google Drive, SharePoint), persoonlijk + org-scope | De werkelijke kracht — upgrade-bestemming |

### Beperkte beta (niet publiek aangeboden)

| Product | Status | Reden |
|---------|--------|-------|
| **Scribe** | Beta bij selecte testklanten | Te losstaand product, eerst valideren |
| **Docs** | Beta bij selecte testklanten (gekoppeld aan Knowledge) | Product nog onvolwassen t.o.v. rest van de stack |

**Productbeslissing april 2026:** Focus als apart product verdwijnt. De functionaliteit (losse documenten en URLs bevragen) is geïntegreerd in de standaard chat-interface als "scoped knowledge." Het onderscheid Focus/Knowledge verdwijnt als apart product-concept voor de buitenwereld.

**Business model:** Betaald basisplan met upgrade-pad — geen freemium

| Plan | Wat je krijgt | Upgrade-trigger |
|------|---------------|-----------------|
| Chat (basis) | Klai Chat + basic Knowledge inbegrepen (5 kennisbanken, 20 documenten) | Geavanceerde connectors nodig, of meer dan 20 docs / 5 bases |
| Knowledge upgrade | Onbeperkt documenten, alle geavanceerde connectors (Confluence, Google Drive, SharePoint, etc.) | — |

De Chat-gebruiker is dus al een betalende klant — geen gratis tier. De basic Knowledge zit in het basisplan zodat het aha-moment (chat met eigen kennis) direct werkt zonder upgrade.

Onboarding-sandbox uitzondering: gescrapete websitepagina's bij signup tellen NIET mee in de 20-doc cap. Zo bereikt de gebruiker het aha-moment vóórdat de cap wordt aangesproken.

**Primaire markt:** Privé chat met eigen kenniscontext — EU-gehost, PII-veilig, direct te gebruiken. Dit is de hoofdhook.

**De werkelijke kracht (intern bekend, extern nog niet gecommuniceerd):**
Knowledge is technologisch ver voor op de markt. De geavanceerde connectors zijn het upgrade-mechanisme én het "intern champion" mechanisme — de AI-enthousiast die het gratis product test ziet de uitgegrijnde Confluence-connector en wordt automatisch pleitbezorger richting IT-buyer.

---

## Target Audience

**Target companies:**
- Bedrijfsgrootte: 20–500 medewerkers (overheden en Webhelp-type zijn groter — apart traject)
- Europees, bij voorkeur NL
- Kennisintensief werk met compliance-blokkade of schaduw-AI probleem

**Sectoren bevestigd door klantdata:**
- Support teams / helpdesk (Voys, The Nerds)
- Overheid — provincies, uitvoeringsorganisaties (Drenthe, Groningen, Omgevingsdienst)
- MSP / IT-dienstverleners (The Nerds)

**Sectoren bevestigd door marktonderzoek (nog geen klanten):**
- Financiële dienstverlening (Rabobank-patroon)
- Juridische sector
- Contact centers / BPO (Webhelp-type)

**Sectoren als hypothese:**
- Finance MKB (20–100 mnd)
- Legal MKB (20–100 mnd)

**Sectoren die we bewust uitsluiten:**
- Healthcare (AI Act high-risk, aparte compliance-wereld)
- HRM (juridisch gevoelig)
- Grote rijksoverheid via aanbesteding (incompatibel met PLG/creditcard-model)

**Decision-makers (hypothese):**
- Partner, teamlead, operations manager
- Zelfstandige beslisser met creditcard
- Geen IT-afdeling nodig, geen lange salescyclus

**Primary use case:** Gevoelige bedrijfsinformatie veilig gebruiken met AI — zonder dat data naar Amerikaanse servers gaat.

**Jobs to be done:**
1. ChatGPT-waarde krijgen zonder compliance-risico (Cloud Act + GDPR)
2. Interne documenten doorzoekbaar maken voor het team — KCC, helpdesk, projectorganisatie
3. Schaduw-AI een compliant alternatief geven zodat IT zichtbaarheid terugkrijgt
4. Organisatiekennis borgen die anders in hoofden zit
5. On-brand AI: tone of voice en merkrichtlijnen centraliseren zodat alle content consistent is — zonder die data naar een extern model te sturen

**Use cases (bevestigd door klantdata):**
- **KCC / Klant Contact Centrum:** medewerkers die snel bij procesdocumentatie en klantkennis willen — Voys, The Nerds, drie overheden
- **Overheid:** compliancy-first AI voor organisaties met ongelabelde gevoelige data en Cloud Act-blokkade
- **MSP / IT-dienstverlener:** kennis van meerdere klanten tegelijk beheren, snel onboarden bij nieuwe opdracht
- **On-brand AI:** tone of voice handboek als kennislaag — alle medewerkers communiceren consistent zonder externe model-training
- **Contact center / BPO** (zoals Webhelp): kennisbank voor grote groepen medewerkers die klanten helpen
- Finance: geanonimiseerde dossiers doorzoeken, compliance-veilig *(hypothese)*
- Legal: institutioneel geheugen van jurisprudentie en precedenten *(hypothese)*

---

## Launching Customers (eerste echte klantdata — april 2026)

### Voys
- **Sector:** Telecom / SaaS (VoIP-aanbieder, NL)
- **Use case:** Interne helpdesk-kennis snel doorzoekbaar maken voor support medewerkers
- **Product:** Knowledge
- **Inzicht:** Instap via Knowledge. Grote kennisbehoefte voor interne support / KCC.

### The Nerds
- **Sector:** Managed services / IT-support
- **Use cases:** Klantkennis snel opnemen bij nieuwe opdracht, medewerkers trainen, interne kennisbank processen + technische documentatie, bellende klanten sneller helpen
- **Product:** Knowledge
- **Inzicht:** MSP-patroon — kennis van meerdere klanten tegelijk beheren. Potentieel kanaal/reseller-segment.

### Provincie Drenthe
- **Sector:** Overheid (provincie)
- **Blokkade:** Cloud Act + interne data niet gelabeld (o.a. paspoorten in systeem zonder classificatie)
- **Beleid:** "Open tenzij" — alles toegankelijk tenzij expliciet verboden, wat AI over ongelabelde gevoelige data heen legt
- **Omgeving:** Microsoft-first
- **Use case:** KCC / projectorganisatie — medewerkers die met data willen praten (compliancy-first)
- **Wil:** Wil wel AI inzetten, mag niet met huidige tooling

### Provincie Groningen
- **Sector:** Overheid (provincie)
- **Blokkade:** Zelfde patroon als Drenthe — Cloud Act, ongelabelde data, MS-first
- **Use case:** KCC, interne kennisbank

### Omgevingsdienst Groningen (Veendam)
- **Sector:** Uitvoeringsorganisatie overheid
- **Blokkade:** Zelfde patroon — compliance-blokkade, data-governance probleem
- **Use case:** Medewerkers die snel bij procesdocumentatie en regelgeving willen

### Wat dit valideert
- ✅ **Support / KCC-sector bevestigd** — Voys, The Nerds, én drie overheidsinstanties
- ✅ **Knowledge is de primaire instap**, niet Chat
- ✅ **Overheid is een actief segment** — niet alleen hypothese, drie concrete prospects
- ✅ **Cloud Act is de blokkade**, niet alleen GDPR — overheden noemen dit expliciet
- 🆕 **Data-governance probleem** — overheid heeft ongelabelde gevoelige data, kan geen AI inzetten zonder risico
- 🆕 **MSP-segment** zichtbaar via The Nerds
- 🆕 **On-brand AI / tone of voice** als apart use case (zie hieronder)

---

## Personas

*Gedeeltelijk gevalideerd door launching customers (april 2026)*

| Persona | Functie | Pijnpunt | Wat wij beloven | Status |
|---------|---------|----------|-----------------|--------|
| **De helpdesk-lead** | Support teamlead, IT-manager | "Onze kennis zit overal — in hoofden, Confluence, mail" | Één doorzoekbare kennislaag voor het hele team | ✅ Bevestigd door Voys + The Nerds |
| **De MSP-eigenaar** | Directeur IT-dienstverlener | "Bij elke nieuwe klant moeten we opnieuw alles leren" | Klantkennis snel opbouwen en hergebruiken | 🆕 Nieuw — The Nerds |
| **De compliance-buyer** | Partner / directeur | "We mogen ChatGPT niet gebruiken met klantdata" | Zelfde ervaring, geen compliance-risico | Hypothese — nog valideren |
| **De kenniswerker** | Teamlead, consultant | "Ik upload steeds dezelfde documenten opnieuw" | Persistent, gedeeld, voedt terug in de org | Hypothese — nog valideren |

---

## Problems & Pain Points

**Core problem:** Bedrijven kunnen de waarde van AI (snelheid, kennisverwerking, productiviteit) niet benutten omdat ze gevoelige data niet kunnen of mogen delen met Amerikaanse platforms.

**Why alternatives fall short:**
- ChatGPT / Microsoft Copilot: Cloud Act exposure, data gaat naar VS, vertrouw op beloften
- NotebookLM: Google-infrastructuur, geen persistentie, geen org-scope
- Fireflies / Otter: Amerikaanse servers, audio opgeslagen, geen kennisintegratie
- Eigen RAG bouwen: te duur, te complex voor 20–100 mnd bedrijven

**What it costs them:**
- Compliance-risico (AVG, brancheregels, klantcontracten)
- Verloren productiviteit: ze gebruiken AI niet terwijl concurrenten wel doen
- Kennissilos: kennis zit in mensen, niet in systemen

**Emotional tension:** "We willen bijblijven met AI, maar voelen ons niet veilig om het echt te gebruiken."

---

## Competitive Landscape

**Direct competitors:**
- ChatGPT Enterprise — "private" maar US-based, veranderende privacy policies, vertrouw op beloften
- Microsoft Copilot — complex, duur, enterprise-only, vereist M365 ecosysteem
- Glean — $100K–$500K voor 500+ mnd, totaal andere prijsklasse

**Secondary competitors (zelfde probleem, andere oplossing):**
- Eigen LLM hosten — te technisch, te duur voor doelgroep
- Geen AI gebruiken — verliest concurrentiepositie

**Indirect competitors:**
- Notion AI, Confluence — geen echte privacy, geen RAG-integratie
- Fireflies, Otter — transcriptie zonder kennisintegratie

**Governance-specifieke concurrenten (voor "AI under control"-haak):**

| Concurrent | Primaire hook | Governance = | Gap |
|------------|--------------|-------------|-----|
| Langdock (DE) | AI adoption platform | Data + toegang + compliance | Geen standaard-prompts, geen tone of voice |
| Mistral (FR) | Sovereign AI | Infrastructure control, on-prem | Geen organisatorische gebruiksgovernance |
| Aleph Alpha (DE) | Europese soevereiniteit | Europese infra + domeinmodellen | Staatsniveau, niet organisatieniveau |
| OpenAI Enterprise | Productiviteit + security | Data protection, admin console | Geen audit trail op gebruiksniveau |
| Microsoft Copilot | AI in tools die je al hebt | Purview + M365 access | Governance = data/toegang, niet gebruik/kwaliteit |
| Glean | AI you can trust | Real-time permissions (data-lek voorkomen) | Geen gebruiksstandaarden, geen prompt-governance |
| Writer | Enterprise AI voor content | Brand consistency + centralized supervision | Alleen content-agents, niet breed intern platform |
| Notion AI | AI in je workspace | Admin controls, zero retention | Geen usage analytics, geen tone governance |

**Cruciale marktobservatie (april 2026):**
Alle concurrenten definiëren governance als *defensief* — wat je *niet* doet (data niet lekken, training niet toestaan). Niemand positioneert governance als hoe AI *binnen* de organisatie werkt.

Drie onbezette gaten:
1. **Centrale prompt-governance bestaat niet** — geen concurrent biedt org-brede standaard-prompts die IT-beheerders uitrollen
2. **Tone of voice als IT-governance feature bestaat niet** — Writer doet dit voor content, maar niemand positioneert het als org-brede governance
3. **AI-gebruik audittrails op menselijk niveau** — bestaande logs gaan over inloggen en data, niet over "welke prompts gebruikte ons team"

**Writer = enige partiële overlap** maar uitsluitend voor marketing/sales content-agents. Niet voor een breed intern AI-platform (Chat + Knowledge + Focus).

---

## Differentiation

**Key differentiators:**
1. Volledig EU/NL-gehost — geen Cloud Act exposure, aantoonbaar (niet beloofd)
2. Geïntegreerde stack — Chat, Focus en Knowledge werken samen, niet losse tools
3. Knowledge-laag technologisch uniek — ver voor op wat de markt biedt (intern onderzoek bevestigt dit)
4. Flat-rate pricing — geen tokens, geen uitleg aan finance

**De onbezette positie — organisatorische AI-governance:**

De markt levert:
- Infrastructure governance → data soevereiniteit, compliance, toegang
- Model governance → bias, drift, lifecycle

Wat niemand levert:
- **Organizational governance** → hoe AI werkt *binnen* de organisatie: standaard-prompts, tone of voice, afdelingsbeleid, usage auditing, centraal beheer van AI-identiteit

Dit is de tweede groeifase voor Klai. "AI under control" is de overkoepelende haak — positief geframed, onderbezet in de markt.

**De twee killer-zinnen (onbezet in de markt):**
> *"De meeste AI-tools geven IT controle over data. Klai geeft de organisatie controle over AI."*

> *"Compliance regelt wie toegang heeft. Klai regelt hoe AI werkt."*

**Toekomstige differentiator (nog niet publiek):**
Docs → Knowledge feedback loop is uniek. Zodra Docs volwassen is, wordt dit de architecturale moat die publiek gecommuniceerd wordt.

**Waarom customers ons kiezen (hypothese):**
- Privacy is aantoonbaar, niet alleen beloofbaar
- Werkt meteen, geen IT-project
- Groeit mee: start solo, schaalt naar team

**Positioning statement:** "Your private AI stack" — privacy-first, EU/NL-gehost, PII blijft van jou. Dit is het fundament en de primaire markt. Knowledge is de reden dat klanten blijven. "AI under control" is de overkoepelende paraplu die beide verkoopt.

---

## Objections

| Bezwaar | Antwoord |
|---------|---------|
| "Is het net zo goed als ChatGPT?" | Voor 90% van business use cases: ja. En het is de enige die je kan gebruiken met gevoelige data. |
| "Wat als jullie stoppen?" | Open modellen, Europese infra. We zijn niet de enige schakel. |
| "Onze IT moet het goedkeuren" | Zelfbediening, geen deployment, geen IT-project nodig voor instap. |
| "Wie ziet onze documenten in Knowledge?" | Toegangscontrole per kennisbank. Publiek (org-breed) of privé, per kennisbank instelbaar. |

**Anti-persona:** Grote enterprise met eigen IT-afdeling, healthcare-organisaties, HRM-zware omgevingen, publieke aanbestedingstrajecten.

---

## Switching Dynamics

**Push (weg van huidige situatie):**
- Compliance-officer die ChatGPT-gebruik blokkeert
- Data-lek incident bij concurrent
- EU AI Act enforcement wordt concreter

**Pull (naar Klai toe):**
- Zelfde ChatGPT-ervaring zonder compliance-risico
- Team kan eindelijk kennis delen via AI

**Habit (houdt ze bij de status quo):**
- "We doen het gewoon niet met gevoelige data" — werkt ook
- Microsoft Copilot is er al (betaald voor M365)

**Anxiety (over switchen naar Klai):**
- "Werkt het wel net zo goed?"
- "Gaat ons team het echt gebruiken?"
- "Wat als de modellen minder goed zijn dan OpenAI?"

---

## Customer Language

*Gedeeltelijk gevalideerd door digital watering hole research (april 2026)*

**Hoe ze het probleem beschrijven (verbatim — uit forums, nieuwsartikelen, officiële verklaringen):**

Medewerkers (frustratie over verbod):
- "This is so dumb."
- "Are we in 1997?"
- "How am I supposed to keep up with my workload now?"
- "Makes life a lot harder..."

IT/Management (compliance-angst):
- "We mogen niet dat data van klanten op platformen van Amerikaanse techbedrijven terechtkomen." — Rabobank
- "Je weet niet precies wat er met de ingevoerde data gebeurt."
- "Ze worden vaak verwerkt buiten de EU."
- "Als er persoonsgegevens zijn ingevoerd, betekent dit dat er sprake is van een datalek." — AP
- "We zijn ervan geschrokken dat er veel en gevoelige persoonsgegevens zijn gestuurd naar AI-websites." — Gemeente Eindhoven

Zoeken naar alternatieven:
- "ChatGPT is not GDPR-compliant. Here are the alternatives."
- "Your data, securely hosted in Germany."
- "Zero retention policy — data is never used to train external models."

**Hoe ze een oplossing beschrijven:**
- "GDPR-compliant AI workspace"
- "Self-hosted AI" / "on-premise AI"
- "Not subject to CLOUD Act"
- "Private AI" / "secure AI workspace"
- "EU-hosted alternative"

**Words to use:** privé, veilig, van jou, Europees, aantoonbaar (niet beloofd), controle, zichtbaarheid, goedgekeurd

**Words to avoid:** revolutionary, enterprise-grade, cutting-edge, leveraging, seamless, empower, AI-powered (te generiek)

**Terminologie:** "collega" i.p.v. "agent" voor mensen in een organisatie

---

## Marktvalidatie (digital watering hole research — april 2026)

### Schaduw-AI is massaal en groeit
- 71% kenniswerkers gebruikt AI zonder IT-goedkeuring (Reco.ai, 3M+ werknemers)
- 57–59% verbergt AI-gebruik voor leidinggevende (KPMG/Uni Melbourne, 48.340 respondenten)
- 84% Nederlandse bedrijven overweegt een AI-verbod — maar verboden werken niet
- 40% medewerkers zegt bewust beleid te overtreden voor productiviteit

### CLOUD Act is het nieuwe GDPR-argument
- Microsoft erkende in het Franse Parlement (juni 2025): geen technische of contractuele regeling omzeilt de CLOUD Act
- Rabobank, ING, ABN AMRO willen actief afhankelijkheid van US-tech verminderen
- 40% Europese organisaties gebruikt sovereign cloud (van 30% in 2024)
- AP heeft AI als focusgebied 2026–2028 — handhaving komt

### Sectoren die AI actief beperken (gevalideerd)
| Sector | Status |
|--------|--------|
| Overheid / gemeenten | ✅ Rijksoverheid verbod + Gemeente Eindhoven datalek (okt. 2025) |
| Financiële dienstverlening | ✅ Rabobank tijdelijke stop, JPMorgan/Goldman/Deutsche Bank beperkt |
| Gezondheidszorg | ✅ Australische ziekenhuizen verbod, AP noemt medische data hoog-risico |
| Juridische sector | ✅ 15% advocatenkantoren heeft officiële waarschuwingen, Mishcon de Reya volledig verbod |
| Technologie (R&D) | ✅ Samsung broncode-lek, Apple intern verbod na IP-bezorgdheden |

### Drie haken (gevalideerd)

**Haak 1 — Compliance (primair voor overheid en gereguleerde sectoren):**
"Jouw organisatie mag AI niet gebruiken met gevoelige data. Klai kan dat wel — aantoonbaar, Europees gehost, niet onderhevig aan de Cloud Act."
→ Buyer: informatiemanager, DPO, projectleider

**Haak 2 — Schaduw-AI (primair voor MKB en IT-managers):**
"Jouw medewerkers gebruiken ChatGPT al — alleen niet veilig. Klai geeft je zichtbaarheid terug."
→ Buyer: IT-manager, ops manager, teamlead

**Haak 3 — AI under control (primair voor organisaties die AI willen uitrollen):**
"Centrale MCP-beheer, audit trail, standaard prompts en agents, tone of voice geborgd — AI die je organisatie écht in handen heeft."
→ Buyer: CTO, COO, IT-manager die AI wil uitrollen maar gecontroleerd

### Drie segmenten

| Segment | Haak | Buyer | Bevestigd |
|---------|------|-------|-----------|
| MKB 20–200 mnd | Schaduw-AI + AI under control | IT-manager, ops, teamlead | Hypothese |
| (Semi-)overheid | Compliance + Cloud Act | Informatiemanager, projectleider | ✅ 3 klanten |
| KCC (niche) | Kennis op de lijn | Operationeel manager, directeur | ✅ Voys, The Nerds, overheden |

### Beste positionering (uit onderzoek + klantdata)
Niet: "ChatGPT-alternatief"
Wel: **"De privé AI-stack die je organisatie wél mag gebruiken — en écht in handen heeft"**

Kernboodschappen die resoneren:
1. "Jouw medewerkers gebruiken ChatGPT al — alleen niet veilig."
2. "Data op een Europese AWS-server is nog steeds bereikbaar via de Cloud Act."
3. "De AP beschouwt ChatGPT + persoonsgegevens al als datalek."
4. "Een verbod creëert schaduw-IT. Klai geeft je zichtbaarheid terug."
5. "Jouw tone of voice, jouw kennis, jouw regels — zonder dat er iets naar buiten gaat."
6. "Centrale regie over welke AI-tools je team gebruikt, met audit trail."

### Referentiecases die werken in sales
- **Gemeente Eindhoven** (okt. 2025): duizenden persoonsgegevens via ChatGPT gelekt, nationaal nieuws
- **Rabobank**: tijdelijke AI-stop, citaat Bart Leurs (raad van bestuur) over klantdata op US-platforms
- **Microsoft CLOUD Act-toegave** in Frans Parlement: zelfs Frankfurt-datacenters zijn niet veilig

---

## Brand Voice

**Tone:** Warm, direct, eerlijk. Zoals een slimme vriend die toevallig veel van AI-infrastructuur weet.

**Style:** Conversationeel maar niet nonchalant. Korte zinnen. Geen jargon zonder uitleg. Humor mag, gericht op de situatie (niet op de klant).

**Personality:** Calm, confident, honest, European

**Formality:** Semi-formeel. "Je" en "jij" in NL, "you" in EN. "We" voor het bedrijf.

**What the reader should think:** "Deze mensen begrijpen mijn probleem en hebben een echte oplossing — en ik kan ze vertrouwen."

**Klai is NOT:** Een startup die schreeuwt. Een enterprise wall. Een AI-hype machine.

**Klai IS:** Een plek die kalm en zeker uitstraalt. Zoals iemand die niet bang is van stilte.

---

## Proof Points

*Vroeg stadium — bewijs is architecturaal, niet via klantcijfers*

**Technische bewijzen:**
- Audio nooit opgeslagen (Scribe) — aantoonbaar via architectuur
- Per-tenant container isolatie (Chat) — aantoonbaar via broncode
- Europese hosting — aantoonbaar, niet beloofd

**Value themes:**

| Thema | Bewijs |
|-------|--------|
| Privacy is aantoonbaar | Architectuur publiek, niet privacy policy |
| Eenvoud | Flat-rate, geen tokens, geen IT-project |
| Org-geheugen groeit | Docs → Knowledge feedback loop (uniek) |

---

## GTM-strategie & doelen (april 2026)

### Fase 1 — Besloten beta (nu → 5 mei 2026)
**Doel:** 100 MKB-bedrijven (20–100 mnd) in een besloten beta. Alles testen, doorontwikkelen, product-market fit bevestigen.

**Aanpak:**
- Waitlist — geen open inschrijving
- AI-begeleide onboarding als primaire onboardingststraat
- Eventueel lichte human touch bij onboarding (geen actieve sales)
- Overheids-trajecten lopen parallel door (los traject, niet via PLG)

**Acquisitie in fase 1:** Lead engine bouwen (geen actieve sales engine). Organisch + content. Warm netwerk. Geen paid, geen outbound.

### Fase 2 — Officiële launch (na 5 mei, na 100 bedrijven)
**Doel:** Grote publieke launch zodra product bewezen is bij 100 bedrijven.
- Product Hunt
- Paid en sales-support mogen dan activeren
- PR en borrowed channels

### Wat converteert (uit demo-feedback)
Iedereen die een demo heeft gezien is enthousiast. Twee dingen resoneren het sterkst:
1. **Kennis bevragen** — documenten en org-kennis doorzoekbaar maken
2. **PII-verwerking mag hier wél** — compliance-sectoren zijn het meest enthousiast

Compliance-sectoren (overheid, zorg, finance, legal) zijn de snelste converters.

### Acquisitie-model
- **Geen actieve sales engine** — lead engine is het doel
- **AI-begeleide onboarding** als primaire conversie-motor
- Menselijke onboarding-support als aanvulling, niet als kern
- Budget beschikbaar voor paid + sales support — bewust uitgesteld tot fase 2
- PLG: "Start met jezelf. Groei naar je team."

**Business goal:** 100 betalende MKB-bedrijven vóór officiële launch op 5 mei 2026.

**Conversion action:** Waitlist-aanmelding → onboarding → eerste kennisbank actief.

---

## Openstaande vragen (valideren met echte klantdata)

- Welke functietitels kopen echt? (partner vs. teamlead vs. ops) — vragen aan Voys + The Nerds
- Is de MSP / IT-dienstverlener een eigen segment met eigen GTM-aanpak nodig?
- Stappen toekomstige klanten ook direct in via Knowledge, of via Chat/Focus eerst?
- Wat zeggen Voys en The Nerds letterlijk over waarom ze kozen voor Klai? (verbatim quotes nodig)
- Hoe lang duurt de time-to-first-value (signup → eerste echte query)?
- Wanneer is Docs volwassen genoeg om publiek te communiceren?
- Klopt de compliance-buyer hypothese, of is de primaire driver "kennis organiseren" (niet privacy)?

*Volgende update: na eerste gesprek met Voys en The Nerds over hun ervaring.*
