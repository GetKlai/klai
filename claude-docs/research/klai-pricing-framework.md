# Klai Pricing Framework

> Author: gtm-launch-strategist + marktonderzoek 2026-03-24
> Date: 2026-03-24 (bijgewerkt na competitive pricing research)
> Status: Recommended — niet gevalideerd in productie
> Blocks: gtm-conversion-copywriter (pricing copy), gtm-cro-specialist (pricing section layout)

---

## Contents

| § | Section |
|---|---|
| 1 | [Framing: wat we eigenlijk prijzen](#1-framing) |
| 2 | [Pricing unit per product](#2-pricing-unit-per-product) |
| 3 | [Knowledge: add-on vs. org-wide charge](#3-knowledge-add-on-vs-org-wide) |
| 4 | [Recommended price points](#4-recommended-price-points) |
| 5 | [De solo-to-team expansie-mechaniek](#5-solo-to-team-expansie) |
| 6 | [Zelfbediening vs. compliance-buyer](#6-zelfbediening-vs-compliance-buyer) |
| 7 | [Upgrade path: team adoptie voelt onvermijdelijk](#7-upgrade-path) |
| 8 | [Wat te vermijden](#8-wat-te-vermijden) |
| 9 | [Open vragen te valideren](#9-open-vragen) |

---

## 1. Framing

### De structurele spanning

Klai verkoopt twee economisch verschillende dingen onder één merk:

**Per-person tools** (Chat, Focus, Scribe) — individuele productiviteit. Waarde is ruwweg proportioneel aan het aantal gebruikers. Natuurlijke pricing unit: seat/maand.

**Org-level infrastructuur** (Knowledge, Docs) — institutioneel geheugen. Waarde is niet proportioneel aan gebruikers; het accumuleert op organisatieniveau. De kennisbank van een bedrijf is meer waard met 5 gebruikers dan met 1, maar niet proportioneel aan headcount. Natuurlijke pricing unit: org/maand of org/jaar, eventueel getierd op datavolume.

Een enkele flat per-seat rate werkt niet voor beide. Als je Knowledge per seat prijst, onderprijst je kleine teams met grote documentvolumes en maak je team-adoptie onnodig duur. Als je de individuele tools op org-niveau prijst, verlies je de PLG-beweging.

**De oplossing is een two-axis structuur:** per-seat voor individuele tools, org-level flat fee voor Knowledge/Docs, met een bundle die het samen kopen duidelijk de juiste keuze maakt.

### Marktanker (bijgewerkt na research)

| Concurrent | Prijs | Wat je krijgt |
|---|---|---|
| **Mistral Team** | $24.99/seat/maand | EU-modeltraining, maar cloud-gehost (Azure/GCP) |
| **ChatGPT Teams** | $25/seat/maand ($30 monthly) | AI chat, geen private hosting, US-gehost |
| **Microsoft Copilot** | $28/seat/maand | AI in Office-apps, US cloud, vereist M365-basis |
| **Perplexity Enterprise Pro** | $40/seat/maand | Web + document search, geen EU-garantie |
| **Glean** | $45-65/seat, minimum $50K/jaar | Enterprise search, 500+ medewerkers |
| **Guru (knowledge)** | $25-30/seat, minimum 10 seats | Wiki + AI, geen feedback loop |

**Conclusie:** €28/seat voor Chat+Focus is exact marktconform. Copilot vraagt €28 zonder EU-hosting. Klai geeft EU-hosting + privacy + document Q&A. Er is geen reden om lager te gaan.

---

## 2. Pricing Unit Per Product

### Chat

**Unit:** per seat / maand
**Rationale:** Chat is een directe vervanging voor ChatGPT Teams ($25/seat) of Copilot for Microsoft 365 ($28/seat). De aankoopbeslissing wordt genomen door wie het budget voor productiviteitstools beheert. De vergelijking is direct.

**Positionering:** Klai Chat is EU-gehost; de privacy-premium is reëel en rechtvaardigt prijsstelling op of boven de marktequivalenten. Onderprijzen signaleert inferioriteit aan een koper die specifiek voor compliance betaalt.

### Focus

**Unit:** gebundeld met Chat (geen apart seat)
**Rationale:** Document Q&A is een feature die de meeste kopers gebundeld met Chat willen, niet als een aparte aankoop. NotebookLM Pro kost $19.99/maand alleen voor document Q&A, zonder EU-hosting. Focus inbegrepen bij Chat maakt het bundel sterker — meer voor dezelfde prijs als Copilot.

### Scribe

**Unit:** per seat / maand, gebundeld in het tweede tier
**Rationale:** Fireflies Business kost $29/seat/maand voor alleen transcriptie. Fathom $32/seat. Klai Scribe zit in een bundel met Chat+Focus bij €42/seat — dat is een sterk argument: je krijgt meer voor een marginale meerprijs.

Scribe is ook de primaire upgrade-trigger naar Knowledge (meeting notes delen → gedeelde kennisbank). Dat maakt het strategisch de moeite waard om het als upgradepad te positioneren, niet als een dure extra.

### Knowledge

**Unit:** org-brede flat fee, getierd op datavolume
**Rationale:** Knowledge is de RAG-laag — het geheugen van de org. De waarde schaalt met de rijkheid van de kennisbank, niet het aantal mensen dat queried. Datavolume (aantal documenten / GB geïndexeerde content) is de duidelijkste proxy voor de geleverde waarde.

Zie §3 voor de volledige analyse.

### Docs

**Unit:** inbegrepen in Knowledge-tier (niet apart verkocht)
**Rationale:** Docs is de feedback loop naar Knowledge — de differentiator, geen apart product. Docs apart verkopen:
1. Creëert een verwarrende aankoopbeslissing
2. Betekent dat een klant Knowledge kan kopen zonder Docs en de kernwaardepropositie mist
3. Voegt een regel toe die niet-technische kopers moeilijk kunnen rechtvaardigen

Docs is altijd inbegrepen wanneer Knowledge actief is.

---

## 3. Knowledge: Add-on vs. Org-Wide Charge

### De opties

| Optie | Mechaniek | Pros | Cons |
|---|---|---|---|
| A — Per-seat add-on | +€X/seat/maand voor Knowledge | Eenvoudige regel, vertrouwd voor kopers | Verkeerde economie: een 5-persoons team met 10K docs betaalt hetzelfde als een 50-persoons team met 50 docs |
| B — Org-wide flat fee | Vaste maandelijkse/jaarlijkse vergoeding per org, ongeacht seats | Matcht waardlevering, moedigt adoptie aan | Onbekend voor kopers gewend aan seat-pricing |
| C — Org-wide, getierd op datavolume | Maandelijkse fee die schaalt met GB geïndexeerd of aantal bronnen | Meest nauwkeurige waarde-alignering | Complexer te communiceren; vereist duidelijke tier-definities |
| D — Seat-prijs inclusief Knowledge, geen aparte fee | Knowledge is gewoon onderdeel van de per-seat prijs | Maximale eenvoud, nul wrijving | Onderprijst grote-content, kleine-team gebruik cases |

### Aanbeveling: Optie C, met eenvoudige twee-tier startstructuur

Knowledge is een **org-brede add-on**, geprijsd op datavolume in eenvoudige tiers. Start met twee tiers (vermijd drietier-verlamming bij launch):

| Tier | Wat het dekt | Prijs (maandelijks) | Prijs (jaarlijks) |
|---|---|---|---|
| Knowledge Starter | Tot 1.000 documenten / 5 GB geïndexeerd | €120/maand | €99/maand |
| Knowledge Business | Tot 10.000 documenten / 50 GB geïndexeerd | €299/maand | €249/maand |

Beide tiers bevatten Docs (de feedback loop), onbeperkte queries, en EU-gehoste opslag.

**Waarom niet per-seat voor Knowledge:**
- Een 3-persoons juridisch team met 5.000 dossiers is een waardevoller en duurder te serveren klant dan een 30-persoons operationeel team met 200 interne docs. Seat-pricing inverteert dit.
- Org-level pricing stuurt ook het juiste signaal: dit is infrastructuur voor de organisatie, geen persoonlijk hulpmiddel.
- De compliance-koper (partner, ops manager) denkt in "wat kost dit mijn bedrijf" — een enkel org-level item is makkelijker goed te keuren dan een per-seat berekening vermenigvuldigd met headcount.

---

## 4. Recommended Price Points

### Core plan structuur

**Plan: Starter**
- Bevat: Chat + Focus
- Prijs: **€28/seat/maand** (maandelijks), **€23/seat/maand** (jaarlijks)
- Minimum: 1 seat (echte zelfbediening, geen minimum)
- Doel: Solo adopteerder, team experimenteerder, PLG instapmoment

**Plan: Pro**
- Bevat: Chat + Focus + Scribe
- Prijs: **€42/seat/maand** (maandelijks), **€35/seat/maand** (jaarlijks)
- Minimum: 1 seat
- Doel: Team dat vergadert en kennis deelt; Scribe als gateway naar Knowledge

**Plan: Business** (inclusief Knowledge)
- Bevat: Chat + Focus + Scribe + Knowledge (1K of 10K docs keuze) + Docs
- Prijs: **€42/seat/maand** + org fee op basis van Knowledge tier
- Praktische all-in kosten voor een 10-persoons team (Starter Knowledge): €420 + €99 = **€519/maand** (~€52/persoon)
- Praktische all-in kosten voor een 30-persoons team (Business Knowledge): €1.260 + €249 = **€1.509/maand** (~€50/persoon)
- Minimum: 3 seats aanbevolen (niet enforced bij launch)
- Doel: Team met gedeelde kennisbehoeften, compliance-bewuste koper

### Alternatief: per-seat all-in voor Knowledge-tier

Als de two-axis structuur te complex is voor de eerste versie van de pricing-pagina:

| | Starter | Pro | Business |
|---|---|---|---|
| **Bevat** | Chat + Focus | Chat + Focus + Scribe | Chat + Focus + Scribe + Knowledge + Docs |
| **Per seat (maandelijks)** | €28/seat | €42/seat | €68/seat of €98/seat |
| **Per seat (jaarlijks)** | €23/seat | €35/seat | €57/seat of €82/seat |

**€68 of €98?** Zie analyse hieronder.

### €68 vs. €98: de eerlijke afweging

| Argument voor €68 | Argument voor €98 |
|---|---|
| Lager dan Glean per seat, geen enterprise-drempel | Niemand verkoopt de Docs→RAG feedback loop — architecturaal uniek |
| Zelfbediening: snellere conversie bij lagere prijs | Bij 30 medewerkers: €2.940/maand vs. Glean's $50K/jaar minimum — je bent nog steeds 14x goedkoper |
| Kopers zijn teamleads met creditcard, niet enterprise procurement | Compliance-waarde is aantoonbaar: 68% van financiële instellingen noemt data sovereignty als primaire adoptiedrempel |
| Je wil snelle adoptie in de 20-100 segment | €98 geeft ruimte voor jaarkorting (bijv. €82/maand bij jaarbetaling) |

**Aanbeveling: begin op €98 met jaarkorting naar ~€82.**

Redenen:
1. Je hebt geen Glean-concurrent in de 20-100 segment — je bent de enige
2. Zelfbediening + geen minimum = laagdrempelig genoeg ondanks hogere prijs
3. Als de prijs de conversie remt, kun je altijd omlaag. Omhoog gaan na launch is veel moeilijker
4. De kennis die opgebouwd wordt in een org is exponentieel waardevoller naarmate meer mensen gebruikmaken — €98 is nog steeds onderschat als je de ROI bekijkt

### Jaarlijkse prijsprikkel

**Early adopter (eerste cohort, ~3 maanden na launch):** 30% korting op jaarlijkse facturering.
**Regulier (daarna):** 20% korting ("2 maanden gratis") op jaarlijkse facturering.

Geen tijdslot op early adopter vastgelegd bij launch — wordt later gecommuniceerd. Mechaniek: je betaalt het jaarbedrag in één keer (seat × maandprijs × 12 × 0.70).

**Weergave op de pricing-pagina:** jaarprijs is de default. Maandelijks is de toggle. Zelfde werkwijze als huidige site.

---

## 5. Solo-to-Team Expansie

### De mechaniek van organische groei

De "start met jezelf, groei naar je team"-beweging werkt financieel alleen als:
1. Solo-instap goedkoop genoeg is om geen goedkeuring te vereisen.
2. Het individu snel genoeg waarde ziet om het te willen delen.
3. Delen heeft een natuurlijke trigger — een moment waarop het individu niet volledige waarde kan halen zonder anderen uit te nodigen.
4. De kosten van teamgenoten toevoegen laag genoeg zijn dat het individu de beslissing kan nemen of makkelijk kan rechtvaardigen.

### Designed expansion triggers

**Trigger 1: Scribe → Knowledge**
Een gebruiker neemt een vergadering op met Scribe. Het transcript is nuttig. Ze willen het delen of erop voortbouwen. De natuurlijke volgende stap is "maak dit beschikbaar voor het team" — wat Knowledge vereist. Op het moment van delen: "voeg je team toe om gedeeld geheugen te ontgrendelen."

**Trigger 2: Focus → Knowledge (documentoverbelasting)**
Een gebruiker met 50 documenten die ze regelmatig manueel queriet realiseert dat de documenten gesilo'd zijn. "Maak je documenten beschikbaar voor je team" is de volgende vraag — dat is de Knowledge-module.

**Trigger 3: Chat → Knowledge (contextgap)**
Een gebruiker chat met Klai en krijgt generieke antwoorden waar ze org-specifieke verwachtten. "Verbind de kennis van je org" is het antwoord.

### Financiële structuur van expansie (two-axis model)

| Teamgrootte | Seats (Pro €42) | Knowledge Starter | Totaal | Per persoon |
|---|---|---|---|---|
| 1 persoon | €42 | — | €42/maand | €42 |
| 3 personen | €126 | €99 | €225/maand | €75 |
| 5 personen | €210 | €99 | €309/maand | €62 |
| 10 personen | €420 | €99 | €519/maand | €52 |
| 20 personen | €840 | €99 | €939/maand | €47 |
| 30 personen | €1.260 | €249 | €1.509/maand | €50 |

De Knowledge vaste kost spreidt zich over meer seats — de per-persoon prijs voor een 20-persoons team met Knowledge is lager dan een 5-persoons team. Dit is een overtuigende visualisatie die het team-plan als korting laat voelen.

---

## 6. Zelfbediening vs. Compliance-Buyer

### De spanning

De zelfbedieningskoper (teamlead met creditcard) heeft nodig: geen wrijving, directe aanmelding, duidelijke maandelijkse prijs, op elk moment opzeggen.

De compliance-koper (ops manager, partner, CISO-equivalent bij een 50-persoons bedrijf) heeft nodig: jaarcontract, factuurafschrijving, data processing agreement (DPA), duidelijke data-residency verklaring, audit-trail mogelijkheid.

Dit zijn niet wederzijds uitsluitend — maar ze vereisen verschillende UX en aanbiedingen.

### Aanbeveling: één product, twee kooipaden

**Pad 1: Zelfbediening (creditcard)**
- Maandelijkse facturering, op elk moment opzeggen
- Geen minimum seats bij Starter
- Aanmeldstroom: e-mail → kaart → beginnen
- DPA beschikbaar als zelfbedieningsdownload (niet achter verkoopgesprek)
- GDPR/AVG compliance documentatie beschikbaar in footer

**Pad 2: Jaarlijks / compliance**
- Jaarlijkse facturering via factuur
- DPA standaard uitgevoerd (niet optioneel)
- Data-residency bevestiging opgenomen in onboarding-e-mail
- Contactformulier voor jaarcontracten >10 seats — geen volledige verkoopcyclus, alleen een menselijk contactpunt voor contracten

### Wat de compliance-koper specifiek moet zien

- "EU-gehost, NL datacenters" — zichtbaar zonder klikken
- "GDPR / AVG compliant by default" — één regel, geen modal
- "Data processing agreement inbegrepen" — met directe downloadlink
- "Jouw data wordt nooit gebruikt voor modeltraining" — expliciet, prominent

---

## 7. Upgrade Path: Team Adoptie Voelt Onvermijdelijk

### Het framing-probleem

De meeste SaaS-upgrade flows mislukken omdat ze extractief aanvoelen: "je hebt je limiet bereikt, betaal meer." Klai's upgrade van solo naar team moet expansief aanvoelen: "je hebt iets waardevolls gevonden, deel het nu."

### Tactische aanbevelingen

**In-product momenten:**

1. Na de eerste Scribe-sessie: "Deel dit transcript met je team — of bouw het in het geheugen van je org." CTA leidt naar team-uitnodiging + Knowledge upsell.

2. Na het uploaden van 10+ documenten naar Focus: "Je team heeft deze waarschijnlijk ook nodig. Verbind ze met je gedeelde kennisbank." Framing gaat over het team, niet over een limiet bereiken.

3. In de chat-interface als geen Knowledge verbonden is: subtiele indicator "Geen org-kennis verbonden" met een link naar "Verbind de documenten van je org." Blokkeert chat niet — maakt alleen de kloof zichtbaar.

---

## 8. Wat te Vermijden

**Vermijd: ondoorzichtige pricing**
Het marktonderzoek benadrukt expliciet dat vrijwel alle concurrenten ondoorzichtige pricing hebben. Klai kan winnen door transparant te zijn. Geen "neem contact op voor prijzen" op de primaire plannen.

**Vermijd: per-seat pricing voor Knowledge**
Al beargumenteerd hierboven. Het wanverhoudt waarde en laat grote-content, kleine-team klanten de grote-team, kleine-content klanten subsidiëren.

**Vermijd: een gratis tier bij launch**
Een gratis tier trekt het verkeerde koopersprofiel voor een privacy-first, compliance-gepositioneerd product. De koper die gratis verwacht is de consumenten-koper — niet het 30-persoons juridisch bedrijf dat betaalt voor GDPR-compliance. Een proefperiode (14 dagen, creditcard vereist) geeft PLG-beweging zonder Klai als freemium-tool te positioneren.

**Vermijd: een derde "Enterprise"-tier bij launch**
De 20-100 persoons doelgroep ziet zichzelf niet als enterprise. Een drietier-structuur (Starter / Business / Enterprise) veroorzaakt analyseverlamming en signaleert dat het echte product "Enterprise" is.

**Vermijd: per-document of per-query metering voor Knowledge**
Verbruiksgebaseerde pricing voor kennisopvraging creëert angst ("hoeveel queries hebben we gebruikt?") en is incompatibel met de vertrouwensrelatie die Klai opbouwt. Flat org-pricing bevat onbeperkte queries binnen de tier.

**Vermijd: de privacy-premium afprijzen**
Prijs niet onder €28/seat om op kosten te concurreren. De compliance-koper kiest Klai niet omdat het goedkoop is — ze kiezen het omdat het veilig is. Prijsstelling onder markt leest als een kwaliteitssignaal, geen waarde-signaal.

---

## 9. Open Vragen te Valideren

| Vraag | Waarom het ertoe doet | Hoe te valideren |
|---|---|---|
| Creëert de €99 Knowledge Starter fee wrijving voor 3-5 persoons teams? | Als ja, stokt solo-naar-team conversie bij de Knowledge upsell | A/B test: Knowledge gebundeld bij hogere per-seat rate vs. aparte org fee |
| Is maandelijkse facturering de dominante voorkeur, of domineren jaarcontracten? | Bepaalt omzetvoorspelbaarheid en of de compliance-koper in zelfbediening verschijnt | Factureringsfrequentie bijhouden bij eerste 100 betalende accounts |
| Is Scribe werkelijk de upgrade-trigger naar Knowledge? | De upgrade-flow is gebouwd op deze hypothese — als die onjuist is, moeten de in-product momenten opnieuw worden ontworpen | Tag Scribe → Knowledge conversies in productanalyse |
| Stopt de compliance-koper bij het ontbreken van een formeel verkoopproces? | Als het 30-100 persoons bedrijf een door een mens ondertekend contract nodig heeft, heeft het zelfbedienings-jaarlijkse pad een lichtgewicht "assisted signup"-optie nodig | Ondersteuningstickets en inkomende contactformulierinzendingen van potentiële klanten die contractvereisten noemen monitoren |

---

## DEFINITIEVE PRICING (vastgelegd 2026-03-24, bijgewerkt met jaarkorting)

### Structuur

Klai gebruikt **modulaire per-seat pricing**. Elke module wordt per gebruiker aan- en uitgezet. Een org betaalt alleen voor wie wat gebruikt.

| Module | Prijs/seat/maand | Cumulatief |
|---|---|---|
| Chat + Focus | €28 | €28 |
| + Scribe | +€14 | €42 |
| + Knowledge | +€26 | **€68** |

Jaarlijkse korting: ~20% (2 maanden gratis). Standaard weergave op pricing-pagina.

### Prijstabel (wat op de site staat)

| Module | Maandelijks | Jaarlijks early adopter (-30%) | Jaarlijks regulier (-20%) |
|---|---|---|---|
| Chat + Focus | €28/seat | **€20/seat** | €22/seat |
| + Scribe | €42/seat | **€29/seat** | €34/seat |
| + Knowledge | €68/seat | **€48/seat** | €54/seat |

Jaarprijs = default weergave. Maandelijks = toggle. Betaling: jaarbedrag in één keer (seat × maandprijs × 12 × korting).

Early adopter: eerste cohort (~3 maanden na launch). Geen tijdslot gepubliceerd bij launch.

### Rationale per stap

**€28 — Chat + Focus (base)**

Focus is bewust gebundeld in de base, niet apart verkocht. Rationale: ChatGPT Teams kost $25/seat zonder document Q&A. Copilot kost $28/seat zonder document Q&A. Klai geeft op €28 beide — privé, EU-gehost. Dit is het sterkste concurrentievoordeel op het instapmoment. Focus apart laten betalen maakt de vergelijking met concurrenten lastiger en verlaagt de conversie.

**€42 — + Scribe (+€14)**

Fireflies Business kost $29/seat voor alleen transcriptie. Fathom $32/seat. Bij Klai kost Scribe €14 extra bovenop Chat+Focus. De koper die vergelijkt met Fireflies ziet: €42 voor Chat+Focus+Scribe vs. $29 voor alleen Scribe. Klai wint die vergelijking makkelijk. Scribe is ook de primaire upgrade-trigger naar Knowledge (meeting notes delen → gedeelde kennisbank).

**€68 — + Knowledge (+€26)**

Notion Business + Notion AI kost ~$26/seat en doet alleen docs en kennisbeheer — geen chat, geen transcriptie, geen EU-hosting, geen RAG-feedback loop. De kennislaag van een organisatie is meer waard dan wat Notion levert. €26 extra voor organisatiegeheugen met Docs→RAG feedback loop (architecturaal uniek) is een eerlijke prijs, geen oplichting. Glean vraagt $45-65/seat met een $50K/jaar minimum voor 500+ medewerkers — voor de 20-100 persoons markt bestaat er geen vergelijkbare concurrent.

### Waarom modulair per user uniek is

Vrijwel alle concurrenten werken met vaste tiers: iedereen op hetzelfde plan. Klai laat per gebruiker aan/uitzetten:
- De accountant hoeft niet voor Scribe te betalen
- De support medewerker hoeft niet voor Knowledge te betalen
- De kennismanager kan Knowledge hebben zonder dat alle 50 collega's mee hoeven

Dit verlaagt de drempel om Knowledge te proberen (zet het aan voor 2 mensen, zie de waarde, rol uit naar het team), en het voelt eerlijk voor de koper. Geen padded seats, geen "iedereen moet upgraden."

### Wat dit communiceert naar de koper

- €28 entry: geen goedkeuring nodig, creditcard, direct starten
- €42 Pro: iemand die vergadert en notities beheert ziet direct de ROI vs. Fireflies standalone
- €68 all-in: "de kennislaag van mijn org kost me €26/maand per gebruiker die het gebruikt" — dat is minder dan wat Notion aanrekent voor een tool die een fractie van de waarde levert

---

## Aanbevolen Structuur bij Launch

### Optie A: Two-axis (aanbevolen voor correcte waarde-alignering)

| | Starter | Pro | Business |
|---|---|---|---|
| **Bevat** | Chat, Focus | Chat, Focus, Scribe | Chat, Focus, Scribe + Knowledge-tier naar keuze + Docs |
| **Per seat (maandelijks)** | €28/seat | €42/seat | €42/seat + Knowledge org fee |
| **Per seat (jaarlijks)** | €23/seat | €35/seat | €35/seat + Knowledge org fee |
| **Knowledge Starter org fee** | — | — | +€99/maand (jaarlijks) |
| **Knowledge Business org fee** | — | — | +€249/maand (jaarlijks) |
| **Min seats** | 1 | 1 | 3 |

### Optie B: Per-seat all-in (eenvoudiger voor eerste launch)

| | Starter | Pro | Business |
|---|---|---|---|
| **Bevat** | Chat, Focus | Chat, Focus, Scribe | Chat, Focus, Scribe, Knowledge (Starter), Docs |
| **Per seat (maandelijks)** | €28/seat | €42/seat | €98/seat |
| **Per seat (jaarlijks)** | €23/seat | €35/seat | €82/seat |
| **Min seats** | 1 | 1 | 3 |

Knowledge en Docs worden altijd samen verkocht. Docs is geen apart item. Jaarlijkse pricing is de standaard weergave; maandelijks is de toggle.

### Competitief anker voor conversie-copywriter

| Vergelijking | Klai Starter | Klai Pro | Klai Business (€98) |
|---|---|---|---|
| vs. ChatGPT Teams ($25) | Zelfde prijs, EU-gehost | Adds Scribe vs. niets | + org geheugen, onbetaalbaar bij Glean |
| vs. Copilot ($28) | Zelfde, maar geen M365 vereist | Adds privé transcriptie | + volledig kennisplatform |
| vs. Fireflies ($29 alleen transcriptie) | Goedkoper + Chat + Focus | Zelfde prijs, maar alles erbij | — |
| vs. Glean ($50K+/jaar) | N.v.t. | N.v.t. | 6x goedkoper bij 30 personen, zelfbediening |

---

## Concurrenten: Directe Prijsbenchmarks

### Chat-vergelijking
| Concurrent | Prijs | Opmerkingen |
|---|---|---|
| Mistral Le Chat Pro | $14.99/seat | Persoonlijk, niet team |
| Mistral Le Chat Team | $24.99/seat ($19.99 jaarlijks) | EU-model, cloud-gehost |
| ChatGPT Teams | $25/seat (jaarlijks), $30 maandelijks | US-gehost |
| ChatGPT Enterprise | $45-75/seat, 150-seat minimum | Onbetaalbaar voor 20-100 target |
| Microsoft Copilot | $28/seat/maand | Vereist kwalificerende M365 basis |
| Perplexity Enterprise Pro | $40/seat/maand | Breder, web-focused |

### Focus-vergelijking (document Q&A)
| Concurrent | Prijs | Opmerkingen |
|---|---|---|
| NotebookLM (Google) | Gratis (consumenten) | US-gehost, geen EU-garantie |
| NotebookLM Plus | ~$14 via Google Workspace Standard | Niet standalone, Google-ecosysteem |
| NotebookLM Pro | $19.99/maand | Persoonlijk, US |
| Perplexity Enterprise | $40/seat | Web + docs gecombineerd |

### Scribe-vergelijking (meeting transcriptie)
| Concurrent | Prijs | Opmerkingen |
|---|---|---|
| Otter.ai Pro | $16.99/seat | Alleen transcriptie |
| Fireflies Business | $29/seat | Alleen transcriptie + AI-samenvatting |
| Fathom | $32/seat | Transcriptie + summaries |

### Knowledge-vergelijking (org geheugen / RAG)
| Concurrent | Prijs | Opmerkingen |
|---|---|---|
| Guru | $25-30/seat, 10-seat minimum | Wiki + AI, geen feedback loop naar docs |
| Confluence Premium + Atlassian Rovo AI | $12.30/seat | Beperkte AI-calls, geen geïntegreerde RAG |
| Microsoft Copilot for M365 (kennis) | Inbegrepen in $28/seat add-on | Zoekt door SharePoint, geen feedback loop |
| Glean | $45-65/seat, $50K/jaar minimum | 500+ medewerkers target, buiten ons segment |
| Sinequa | Custom / enterprise | Fortune 500, implementatietraject |
| Vectara | Custom / developer-first | Toolkit, niet turnkey |
