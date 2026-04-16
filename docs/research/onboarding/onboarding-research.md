# Onboarding Research: AI Chat Onboarding voor Klai

> Aangemaakt: 2026-04-16  
> Bijgewerkt: 2026-04-16  
> Status: Actief — beslissingen verwerkt uit sparring-sessie  
> Aanleiding: Sparring-sessie over AI chat onboarding als aha-moment trigger

---

## Onderzoeksvraag

Hoe ontwerp je een onboarding workflow die direct laat zien hoe krachtig Klai is — specifiek via een AI chat interface die de gebruiker meteen met zijn eigen kenniscontext in gesprek brengt?

---

## Target gebruiker voor onboarding

**Niet:** individuele kenniswerker die AI ontdekt  
**Wel:** AI-enthousiast (soms IT-buyer of tech lead) die het product zelf wil testen om intern te zeggen "ja, dit is wat we zoeken"

Dit is een specifiek persona: hij/zij weet al wat AI kan, wil geen discovery-uitleg, maar wil direct het onderscheidend vermogen van Klai ervaren. De onboarding hoeft niet uit te leggen wat AI is — hij moet laten zien waarom Klai anders is dan ChatGPT.

---

## Het aha-moment

**Klai's aha-moment:** "Ik ga in gesprek met mijn eigen kennis en vind in seconden wat me vroeger dagen kostte."

Het sleutelwoord is *eigen kennis*. Generieke AI-demo's tonen geen aha-moment voor deze gebruiker. Het product moet zijn context kennen.

---

## Kernbevindingen uit de research

### 1. Tweesporig onboarden is industrie-consensus

Alle grote B2B AI-platforms (Microsoft 365 Copilot, Slack AI, Notion Enterprise, Claude Enterprise) splitsen de onboarding strict:

| Spoor | Doelgroep | Focus |
|---|---|---|
| Admin/IT spoor | IT-buyer, compliance-verantwoordelijke | Governance, SSO, data-soevereiniteit, EU data boundary |
| Eindgebruiker spoor | Kenniswerker, AI-enthousiast | Snelheid, direct resultaat, productiviteitswinst |

**Conclusie voor Klai:** Compliance-messaging (Europees, PII-safe, geen training op data) hoort uitsluitend in het admin-spoor. De eindgebruiker-onboarding mag maximaal één zin bevatten: *"Jouw data verlaat nooit je eigen omgeving."* Daarna direct naar waarde.

---

### 2. Lege states zijn activatie-killers

Uit meerdere PLG-bronnen: **~60-70% van gebruikers die de eerste sessie verlaten zonder concrete waarde-ervaring, komt nooit meer terug.**

Producten die dit succesvol oplossen:
- **Canva:** start met template, geen lege canvas
- **Humata:** upload PDF → direct vragen stellen
- **Crayon/Klue:** scrapet concurrenten bij signup → eerste waarde vóór handmatige actie
- **Slite:** importeert direct vanuit Confluence/Notion → bestaande kennis is meteen beschikbaar

**De patroon:** hoe meer de initiële data van de gebruiker zelf is, hoe sterker het aha-moment. Maar hoe hoger ook de setup-friction. De beste producten automatiseren de data-acquisitie om de friction weg te nemen.

---

### 3. Website-scrapen is de brug, niet het eindstation

Het scrapen van de website van de aanmelder (via URL/domein bij signup) is een bewezen patroon voor competitive intelligence tools (Crayon, Klue, SEMrush). Het werkt omdat:

1. Setup-friction = nul
2. Context is direct persoonlijk ("dit is mijn bedrijf")
3. De gebruiker ziet AI werken op zijn eigen context

**Kritisch punt:** Een website is publieke kennis. Het aha-moment dat Klai wil bieden — "AI werkt op mijn private kennis" — wordt niet volledig getriggerd met websitedata. De webscrape is de brug die de lege-state drempel wegneemt en de gebruiker warm maakt voor stap 2: het koppelen van echte private kennis.

**De juiste framing:** "Dit was je publieke website. Wil je zien wat er gebeurt als ik ook je interne documenten ken?"

---

### 4. Conversationele onboarding werkt alleen onder strikte condities

Producten die chat AS onboarding gebruiken (niet chat naast onboarding) werken alleen als:

1. **De vragen leiden direct naar waarde** — geen persona-quiz zonder direct resultaat
2. **Max 3-4 exchanges** — daarna drop-off
3. **Het product demonstreert zichzelf via het gesprek** — de chat IS het product, niet een uitleg over het product
4. **Context is al aanwezig** — een lege prompt-box zonder pre-loaded context is voor enterprise-gebruikers een activatie-killer ("wat moet ik hier mee?")

Microsoft's eigen analyse van Copilot-uitrolling: de meest gehoorde reactie op een lege prompt-box is "wat moet ik hiermee?" Oplossing: rol-specifieke prompt-voorbeelden of pre-loaded context.

---

### 5. De shadow AI-gebruiker is onbenut

**Blinde vlek in de markt:** Vrijwel geen enkel platform heeft een expliciete onboarding voor de gebruiker die al ChatGPT of Claude gebruikt op het werk — de shadow AI-gebruiker.

Die persoon heeft geen "ontdek AI"-onboarding nodig. Die heeft een **migratie-onboarding** nodig:  
*"Je gebruikt AI al. Doe nu hetzelfde, maar dan op jouw eigen bedrijfsdata, zonder dat die data buiten jouw omgeving gaat."*

Dit is exact Klai's AI-enthousiast target. De onboarding hoeft hem niet te overtuigen van AI — alleen van het verschil dat eigen kenniscontext maakt.

---

### 6. MCP-servers en standaardprompts zijn niet voor dag-1 onboarding

De waarde van managed MCP-servers en standaardprompts is pas begrijpelijk voor gebruikers die al weten wat ze missen. Voor dag-1 onboarding creëert dit cognitieve belasting zonder directe waarde.

**Wanneer wel:** Na activatie, als de gebruiker al waarde heeft ervaren. Dan is "je hebt ook toegang tot [tool] en [tool] zonder configuratie" een versterker, geen introductie.

---

### 7. EU-data residency is conversiefactor voor IT-buyers — niet voor eindgebruikers

Europese eindgebruikers gedragen zich niet fundamenteel anders dan Amerikaanse bij adoptie. De privacy-differentiatie zit bij de kopers (IT, Legal, Compliance), niet bij de gebruikers. 

**Wat converteert bij IT-buyers in Europa:**
- Aantoonbare EU data boundary (niet "wij zijn GDPR-compliant" maar "data verlaat de EU niet, aantoonbaar")
- EU AI Act compliance roadmap (vroeg-koper differentiator)
- Geen afhankelijkheid van Amerikaanse cloudproviders

**Risico van over-compliance-marketing:** Trek je alleen kopers die al actief zoeken. Kopers die shadow AI hebben maar nog niet zoeken, worden aangetrokken door snelheid en resultaat — niet door certificaten.

---

## Synthese: het onboarding-model voor Klai

### Uitgangsprincipe

Klai's onboarding is een tweespoor dat parallel loopt maar nooit mengt:

```
Admin spoor     → IT-buyer setup: governance, SSO, EU-data, connectors
Eindgebruiker   → AI-enthousiast: direct aha via eigen kenniscontext
```

De AI chat onboarding is het eindgebruiker-spoor. De Admin-flow is een apart pad.

---

### Het eindgebruiker-onboarding-model (4 fasen)

**Fase 0 — Signup (30 seconden)**  
Vraag alleen naam + werk-e-mail + bedrijfsdomein. Geen wachtwoord aanmaken vóór aha-moment. Klai scrapt automatisch het opgegeven domein.

**Fase 1 — Chat met je publieke context (dag 1, minuten)**  
Direct na signup: geen dashboard, geen checklist — een chat-interface.  
Pre-loaded context: gescrapete websitedata.  
Welkomstbericht: *"Ik heb je website gelezen. Stel maar een vraag over je bedrijf."*  
Max 3-4 exchanges. De chat IS het product. Het doel is niet nuttige antwoorden geven — het doel is laten voelen hoe het is om AI te bevragen die je context kent.

**Fase 2 — Voeg private kennis toe (dag 1-2)**  
Na de chat, organisch: *"Dit was je publieke website. Wil je zien wat er gebeurt als ik ook je interne documenten ken?"*  
Upload of connector. Dit is het echte aha-moment. De overgang van publieke naar private kennis is de kernbelofte van Klai.

**Fase 3 — Migratie-narratief voor de shadow AI-gebruiker**  
Voor gebruikers die al ChatGPT/Claude gebruiken (de target AI-enthousiast): geen discovery-onboarding.  
Boodschap: *"Je gebruikt AI al. Doe nu hetzelfde, maar dan op jouw eigen bedrijfsdata — en die data verlaat nooit jullie omgeving."*

---

## Productbeslissingen (vastgelegd uit sparring-sessie)

### Focus wordt geschrapt als apart product

Focus (notebook LLM-alternatief op losse documenten) en de bredere kennislaag draaiden al op dezelfde technische architectuur. De interface-splitsing voegde geen waarde toe en creëerde verwarring: Chat werkte op de kennislaag, Focus op geüploade artikelen, en de overgang was niet organisch.

**Beslissing:** Focus wordt geïntegreerd in de standaard chat-interface als "scoped knowledge" — gebruikers voegen docs/URLs toe aan een kennisbank en chatten daar direct mee. Het onderscheid Focus/Knowledge verdwijnt als apart product-concept.

---

### Freemium-model: gratis laag met zichtbare upgrade

**Gratis tier limieten:**
- 20 documenten totaal
- 5 kennisbanken
- Alleen losse documenten en losse URLs (geen geavanceerde connectors)
- Geavanceerde connectors (Confluence, Google Drive, SharePoint, etc.) zijn zichtbaar maar uitgegrijsd

**Telregel:** 1 websitepagina = 1 document. Een website van 15 pagina's telt als 15 documenten.

**Onboarding-sandbox uitzondering:** De gescrapete websitepagina's bij signup tellen als aparte onboarding-sandbox en gaan niet af van het document-cap. Zo bereikt de gebruiker het aha-moment (chat met eigen context) vóórdat zijn cap wordt aangesproken.

**Upgrade pad:**
- Geavanceerde connectors willen → upgrade naar betaald plan
- Meer dan 20 documenten willen → upgrade
- Meer dan 5 kennisbanken willen → upgrade

De uitgegrijnde connectors dienen als intern sales tool: de AI-enthousiast die test, ziet "oh, we kunnen onze Confluence koppelen" en wordt automatisch champion richting IT-buyer.

---

### Twee groeimodellen

**Er is geen gratis tier.** De testgebruiker betaalt direct.

| Model | Profiel | Mechanisme |
|---|---|---|
| **Olievlek** | AI-enthousiast met beslissingsbevoegdheid | Koopt zelf, rolt uit naar team zonder IT-traject. Vriendelijkste pad. |
| **IT/Purchasing** | Formeel inkooptraject | Via IT of purchasing, contract, SSO, governance. |

Het olievlek-model is het primaire groeipad: één betaalde gebruiker die het product ervaart, het intern aanbeveelt, en collega's uitnodigt zonder formeel procurement.

---

### Herzien onboarding-pad (na beslissingen)

```
Signup: naam + werk-e-mail + bedrijfsdomein
  ↓
Website gescraped (telt NIET mee in doc-cap)
  ↓
Chat-interface met pre-loaded website context
"Ik heb je website gelezen. Stel maar een vraag."
Max 3-4 exchanges → aha-moment (lite)
  ↓
"Voeg je eigen docs toe" → upload of URL
(telt nu WEL mee in de 20-doc cap)
  ↓
Chat met private kennis → echt aha-moment
  ↓
Ziet uitgegrijnde connectors → intern champion wordt actief
  ↓
Upgrade (individueel) of "deel met IT" (org-pad)
```

---

## Betaalmuur timing en onboarding modaliteit (research resultaten)

### Betaalmuur: na het aha-moment, niet ervoor

Uit B2B SaaS benchmarks (meerdere bronnen, april 2026):

- Paywall na waarde-ervaring converteert **25% beter** dan paywall ervoor
- Gebruikers die binnen 3–5 dagen activeren, converteren **60–80% vaker** dan gebruikers die dat niet doen
- Wie binnen 5 dagen het aha-moment niet bereikt, converteert vrijwel nooit meer

**Creditcard bij signup:** Geen CC vereisen is het juiste model voor PLG/SMB. CC upfront geeft hogere conversie per aanmelding (49% vs. 18%), maar 70% minder aanmeldingen — netto-effect op omzet is negatief. Notion, Linear, Loom en Intercom vragen geen CC bij initiële toegang.

**Beste model voor Klai: reverse trial**
Gebruiker start met volledige toegang, na 14 dagen terugval naar beperkte laag tenzij er betaald wordt. Stockpress: van 10% naar 25% conversie na invoering. Werkt omdat het loss aversion triggert — wat je al hebt wil je niet kwijt.

**Achievement-based upgrade trigger:** Upgrade prompt na een specifieke waardevolle actie (eerste kennisbank aangemaakt, eerste query gesteld) converteert **258% hoger** dan generieke "je trial verloopt morgen"-mails. Kortere trials (7–14 dagen) presteren **71% beter** dan 30-daagse trials, mits de gebruiker het aha-moment haalt.

---

### Onboarding modaliteit: hybride wint

Benchmarks over 62 B2B SaaS-bedrijven: gemiddelde activatierate is **37,5%**. Bij AI/ML-producten is dit **54,8%**. Een activatieverbetering van 25% levert 34% MRR-groei op over 12 maanden.

| Model | Activatie-effect | Wanneer zinvol |
|---|---|---|
| Pure self-serve | Basis | Altijd als laag, nooit alleen |
| Human (high-touch) | ~2x hoger | Boven ~€1.000 ACV/jaar; anders eet het de marge op |
| AI-begeleide onboarding | +35–50% | SMB, PLG, schaalbaar |
| Hybride (AI + optioneel mens) | Beste totaalresultaat | -30% churn vs. pure self-serve |

**Superhuman (high-touch benchmark):** Verplichte 30-min 1-op-1 bij elk betaald account → bijna 2x hogere activatie, 2x hogere referral rates. Werkte omdat hun product diepe gewoonte-verandering vereiste (weg van Gmail). Niet overdraagbaar naar alle producten.

**TheyDo (AI-onboarding benchmark):** AI-avatar walkthrough → **59% completion op een 42-stappen demo**, meer stakeholders volledig geactiveerd. Click-tours en kennisbank-chatbots leverden nul uplift.

**Completion rates per tour-lengte (Chameleon-data):**
- 3 stappen: 72% completion
- 7 stappen: 16% completion

**Concierge onboarding (optionele menselijke hulp):** Flowjam bood bij signup een 15-minuten Calendly-slot aan → +37% activatierate bij het segment dat het accepteerde. Werkt als tijdgebonden, persoonlijk aanbod — niet als standaard voor iedereen.

---

### Aanbeveling voor Klai

1. **Geen CC bij signup** — volledige toegang, geen barrière
2. **Reverse trial van 14 dagen** — start met alles, daarna paywall
3. **AI-begeleide onboarding als standaard** — max 3–5 stappen per fase, achievement-based voortgang
4. **Upgrade trigger op actie**, niet op kalender — na eerste echte output of eerste kennisbank
5. **Optionele concierge-laag** — niet standaard aangeboden, maar beschikbaar voor wie vastloopt of na dag 5 nog niet actief is
6. **Setup-drempel wegnemen is prioriteit 1** — leeg scherm = geen activatie; website-scrape of template als startpunt

---

## Openstaande vragen

1. **Fase 0 friction:** Vraag je het domein bij signup, of detecteer je het automatisch via e-mailadres (mark@klai.nl → klai.nl)?

2. **Fase 1 kwaliteit:** Wat als de website weinig informatie bevat (one-pager, outdated)? Is er een fallback voor de onboarding-sandbox?

3. **Achievement-trigger definitie:** Wat is de concrete actie die de upgrade-prompt triggert — eerste kennisbank aangemaakt, eerste query gesteld, of iets anders?

---

## Bronnen

Research uitgevoerd via web-agents op 2026-04-16. Geverifieerde bronnen:

- Microsoft Learn: Copilot setup, privacy docs, Work Trend Index 2025
- Slack AI productpagina
- Notion Enterprise
- Claude Enterprise (Anthropic)
- Google Workspace Security
- EU AI Act (Europees Parlement)
- Pendo onboarding glossary
- PLG-literatuur via meerdere bronnen (Paddle/ProfitWell, Lenny's Newsletter)
- Productdocumentatie: Humata, Slite, Guru, Glean, Coda, Crayon, Klue
