# Klai Launch TODO — 12 mei → 8 juni 2026

> Owners: Jantine, Mark, Steven.
> Reference: `SPEC-LAUNCH-SOFTLAUNCH-001/spec.md` (readiness gaps) + `marketing/campaigns/klai-launch/output/run-2026-05-11T11-49-15/` (full calendar + brand book).
> Success target (from `strategy.json`): **100 sign-ups week 1, 150 cumulative week 2, 250 week 3, 400 week 4.**

---

## Doelgroepen (uit strategy.json)

1. Compliance officers / DPOs in mid-market
2. IT leads / CISOs die enterprise-AI evalueren
3. Department heads in gereguleerde sectoren (banking, healthcare, gov, legal, accountancy, insurance, education)
4. Knowledge workers die ondergronds ChatGPT gebruiken — empowered door hun team

Iedere outreach moet één van deze vier in het zicht hebben — niet "iedereen die ik ken".

---

## Week 0 — Vandaag, 12 mei (pre-launch)

### Jantine
- [ ] Calendar-preview doorlopen: `https://getklai.com/_launch-internal-2026-05-12/` — bevestig elke post-tekst en variant-keuze voor week 1
- [ ] LinkedIn launch-post inplannen voor 09:00 morgen (gebruik variant_1 thumbnail uit calendar)
- [ ] X launch-post inplannen voor 09:00 morgen (variant_1, score 9/10)
- [ ] OG share-image testen door één LinkedIn-draft te previewen met `https://getklai.com/` als URL
- [ ] Persoonlijke LinkedIn-bio update naar "Founder, Klai. Hosted here. Owned here. Built here."

### Mark
- [ ] **E2E prod-tenant CI fixen** — 4 laatste runs failen op missing secrets `E2E_USER_EMAIL/PASSWORD/TOTP_SECRET/BASE_URL`. Vandaag fixen of expliciet accepteren dat we morgen blind deployen
- [ ] **B-3 free-email block** — eerste invitees uit jouw netwerk komen via gmail/hotmail. Of B-2 token-bypass shippen, of B-3 tijdelijk soepelder voor de softlaunch
- [ ] **S-3 mock-billing guard** (15 min) — pydantic `@field_validator` in `klai-portal/backend/app/core/config.py` die bootkill geeft als `MOCK_BILLING=true AND ENVIRONMENT=production`
- [ ] **S-1 alerts**: verifieer dat `launch-softlaunch-rules.yaml` actief is op core-01 Grafana en dat de notificatiekanaal niet `/dev/null` is. Test-fire één rule
- [ ] Test-signup zelf doen om 17:00 met je persoonlijke gmail. Wat breekt?

### Steven
- [ ] **Legal copy review** op productie `https://getklai.com`:
  - `/docs/legal/privacy` (EN+NL)
  - `/docs/legal/terms` (EN+NL)
  - `/docs/legal/dpa`
  - `/docs/legal/sub-processors` — laatste audit-datum kloppend?
- [ ] **Steward-ownership pagina** (`/docs/company/steward-ownership`) reflecteert actuele statuten?
- [ ] Eigen X-post `2026-05-24-x-steven-legal` reviewen
- [ ] Lijst opstellen: **20 namen voor first-100 outreach** uit jouw legal/Voys netwerk (mag scheef verdeeld zijn over week 4)

### Gezamenlijk (vandaag eindigen)
- [ ] **Slack/WA-thread "Klai launch month"** opzetten — alle launch-meldingen daarheen
- [ ] **First-100 outreach-lijst** afspreken: ieder levert 30 namen uit eigen netwerk (Jantine 30, Mark 30, Steven 20+, plus 20 via Klai-CRM). Doel: ≥100 warm contacts gecontacteerd in week 1
- [ ] **On-call rooster** 13 mei → 8 juni: wie reageert binnen 1u op signup-issues
- [ ] **Stop-criterium** afspreken: wat triggert "we pauzeren een post" (bv. crashende signup, lege KB voor alle nieuwe tenants, prod-down)

---

## Week 1 — 13–19 mei: plant the flag (steward-ownership + legal structure)

**Calendar (publiceren 09:00 dagelijks, allen via Klai-pagina):**
- 13 mei wo — Founder voice: Why we exist (Jantine) — LinkedIn portrait
- 14 mei do — Steward-owned, written into our articles — X landscape
- 15 mei vr — EU-hosted, no CLOUD Act — LinkedIn square
- 16 mei za — Every answer comes with sources — X square
- 17 mei zo — Your documents, one search — LinkedIn portrait
- 18 mei ma — Synced from where you work — X landscape
- 19 mei di — Founder voice: Mark, everyday user — LinkedIn square

### Jantine
- [ ] **Posting** 09:00 elke dag (kies één: zelf, scheduler, of Mark als jij niet kunt)
- [ ] **Reageren** op alle comments op de Klai-pagina + repost van Klai-pagina vanaf eigen account
- [ ] Founder-quote DM-replies: prioritize legal/structural questions (jouw domein)
- [ ] **Outreach** 30 warm contacts uit eigen netwerk met persoonlijke uitnodiging → waitlist of magic-link
- [ ] **Wekelijkse check** (vrijdag): signup count vs target (week 1 = 100). Notuleer in thread.

### Mark
- [ ] **On-call** vanaf 08:30 elke werkdag: VictoriaLogs + Grafana "launch-softlaunch" open
- [ ] **08:45 zelf-signup** elke ochtend met fris test-account: post-deploy regressie check
- [ ] **17:00 daily mini-retro** in thread: signups vandaag / activatie-rate / fouten in logs
- [ ] Founder-quote post 19 mei (Mark "everyday user moment") — eigen reflectie + 5 collega-tags
- [ ] **Outreach** 30 warm contacts uit Voys / engineering / product netwerk
- [ ] B-2 (waitlist→signup bridge): hard plannen voor week 2 als signups > 10/dag

### Steven
- [ ] Compliance/legal-vragen op LinkedIn binnen 4u beantwoorden (jouw expertise: steward-ownership, CLOUD Act, sub-processors)
- [ ] Eigen LinkedIn-post 18 mei (jouw founder-voice komt 24 mei, maar deelt elke dag in week 1)
- [ ] **Outreach** 20 warm contacts uit Voys / banking / accountancy / legal-tech netwerk
- [ ] **Friday signup-call**: jouw eerste 5 acceptaties uit het netwerk → 30 min onboarding-call met Klai-team aanwezig

---

## Week 2 — 20–24 mei: product depth + competitor comparison

**Calendar:**
- 20 mei wo — ChatGPT Enterprise: trained on your data? Not contractually promised — X square
- 21 mei do — Microsoft Copilot: sub-processors published? We do — LinkedIn portrait
- 22 mei vr — CLOUD Act: if data is in the US, so is the risk — X landscape
- 23 mei za — Open source means auditable. Read the code — LinkedIn square
- 24 mei zo — Founder voice: Steven on why legal finally said yes — X square

### Jantine
- [ ] **Posting + reacties** zelfde cadence als week 1
- [ ] **Mid-week check** (woensdag): cumulative signups vs target (week 2 = 150). Bijsturen als afwijking > 20%
- [ ] **Outreach round 2** — 15 nieuwe warm contacts, focus op design/UX-leads die ChatGPT-shadow-IT-frustraties hebben

### Mark
- [ ] **Daily** signup health check + retro
- [ ] **Eerste 10 onboarding-Loom**: zelf opnemen voor users die geen 1-op-1 sessie willen
- [ ] B-2 ship: waitlist→signup magic-link bridge zodat self-serve werkt voor week 3+
- [ ] **Outreach round 2** — 15 nieuwe contacts (IT-leads, CISOs)

### Steven
- [ ] **24 mei** jouw founder-post live + persoonlijke LinkedIn-amplifier
- [ ] **Cohort planning**: lijst van 50 namen voor first-100 closing (4 juni) opstellen. Daarvan in week 2 al 10 gecontacteerd
- [ ] Sub-processors page update als er iets verandert (week 2 = comparison week, mensen gaan kijken)
- [ ] **Outreach round 2** — 10 nieuwe contacts uit banking/legal/accountancy

---

## Week 3 — 26–30 mei: social proof + industry verticals

**Calendar:**
- 26 mei di — Banking: first cohort live, early adopter stories — LinkedIn portrait
- 27 mei wo — Healthcare: patient data, AI stays in-house — X landscape
- 28 mei do — Legal: client files, privilege, AI respects the boundary — LinkedIn square
- 29 mei vr — Transparent pricing: €28/mo or €68/mo. That's it — X square
- 30 mei za — One month live. What we learned from first 100 — LinkedIn portrait

### Jantine
- [ ] **One-month retrospective post** 30 mei: schrijf je deze persoonlijk (founder voice). Concreet: wat geleerd, welke aannames klopten/braken, wat is het volgend kwartaal
- [ ] Posting + reacties
- [ ] **Pricing page review** voor de 29 mei post: `/getklai.com` pricing-blok klopt en `€28/€68` is helder gepositioneerd
- [ ] **Wekelijkse check** vrijdag: target week 3 = 250 cumulative

### Mark
- [ ] **First-100 dashboard** in Grafana: hoeveel tenants nu live, wat hun activatie-status is (KB ingest geslaagd, eerste query, etc.)
- [ ] Ondersteuning eerste 100 (jij + Jantine + Steven roteren als support-bench)
- [ ] **Self-hosted offering** voorbereiden voor 2 juni post — landing op `/self-hosted` of FAQ-update

### Steven
- [ ] **26 mei banking-post** → DM-outreach naar 5 banking-contacts (eigen netwerk)
- [ ] **27 mei healthcare** → DM-outreach naar 5 healthcare-contacts
- [ ] **28 mei legal** → DM-outreach naar 5 legal-contacts
- [ ] **First-100 closing** voorbereiden: lijst checken wie warm/lauw/koud, wie nog niet gereageerd → reminder schrijven voor 4 juni

---

## Week 4 — 2–8 juni: closing + cohort 2 priming

**Calendar:**
- 2 jun ma — For the most regulated: self-hosted deployment available — X landscape
- 3 jun di — Accountancy: tax documents, compliance-first AI — LinkedIn square
- 4 jun wo — **First 100 closing. 30% off annual for early adopters** — X square (urgency)
- 5 jun do — Cohort 2 opens next month — LinkedIn portrait
- 8 jun zo — Compliance isn't a checkbox — X landscape

### Jantine
- [ ] **4 juni closing-day plan** — wat is de tijdslot? Komt er een email naar de hele waitlist? Wat is de fallback bij minder dan 100?
- [ ] **Cohort 2 priming** post 5 juni — wachtlijst-opening-mechaniek beschrijven
- [ ] Wekelijkse check: target week 4 = 400 cumulative

### Mark
- [ ] **Conversion mechanic** klaarzetten: 30% off annual code in Moneybird, expiry-datum 4 juni 23:59
- [ ] **Waitlist closure** technisch: na 4 juni 23:59, waitlist form zegt "Cohort 1 gesloten, schrijf je in voor cohort 2"
- [ ] **Post-launch retro draft** voor 8 juni: nummers verzameld, wat ging goed/fout, week 5+ planning

### Steven
- [ ] **3 juni accountancy-post** → DM-outreach naar 5 accountancy-contacts
- [ ] **First-100 closing** persoonlijke calls / reminders naar je sector-contacts die nog niet zijn geconverteerd
- [ ] **Compliance-deepdive** 8 juni post — op de closing van de campagne een sterke compliance-statement (jouw founder-voice)

---

## Cross-cutting (alle vier weken)

### Posting hygiene
- [ ] 09:00 publiceren (consistente tijd matters voor LinkedIn-algoritme)
- [ ] Alle posts vanaf Klai-pagina + repost vanaf founder account met persoonlijke toevoeging
- [ ] Comments binnen 4u beantwoorden (gedeeld op-call schedule)
- [ ] Geen weekend-posts forceren als ze niet klaar zijn — kwaliteit > volume

### Tracking & meting
- [ ] **Wekelijkse signup-count** in shared sheet of Grafana panel (Mark bouwt)
- [ ] **Conversie-funnel**: waitlist-submit → magic-link-sent → signup-completed → first-KB-query → first-week-active
- [ ] **UTM tagging** voor elke launch-post zodat we per-kanaal (LinkedIn vs X) attribution hebben
- [ ] **Twenty CRM stage transitions** correct ingericht (NEW → INVITED → ACTIVE → CONVERTED)

### Issue triage
- [ ] Stop-criteria (afgesproken vandaag) gerespecteerd
- [ ] Iedere productie-issue krijgt een Klai-CRM ticket + thread-melding binnen 30 min
- [ ] Hotfixes naar `main` (`gh run watch` voor portal-frontend), zoals we vandaag deden

### Comms-hygiëne
- [ ] Geen overlappende posts vanuit founder-accounts dezelfde dag (cannibaliseert reach)
- [ ] Tone-of-voice in DM-replies = editorial, principled, calm (zelfde als post-copy)
- [ ] Bij interne kritiek/zorgen: in `Slack/WA-thread`, NIET op LinkedIn

---

## Wat AL gedaan is (12 mei)

- ✓ getklai.com SEO: schema (Organization+WebSite), hreflang, llms.txt, sitemap lastmod, og-image 1200×630 on-brand, noindex op blog tag pages, footer NL-localisatie
- ✓ Brand cleanup: 6 stale logo-bestanden naar `/legacy/`, 6 stale fonts verwijderd, OrbitingTerms naar Parabole/Decima Mono
- ✓ Portal UI consistency v1: `/admin` + `/app` home van card-grid → rows (knowledge bases pattern); 10 pages naar `max-w-3xl`
- ✓ S-1 launch-killer alerts gedeployed (`deploy/grafana/provisioning/alerting/launch-softlaunch-rules.yaml`)
- ✓ B-1 signup password hint EN+NL gefixt naar ≥12 chars guidance
- ✓ Launch-preview live op `https://getklai.com/_launch-internal-2026-05-12/index.html` (calendar-view voor alle drie)

---

## Out of scope deze maand (post-launch)

- Onboarding wizard / first-run walkthrough (N-3 in spec)
- Zitadel email branding (N-4)
- Pricing-page complete rewrite
- Cohort 2 detailled mechanics (komt na 5 juni)
- Self-serve B-2 bridge productieklaar (komt eind week 2 ipv launch)
- Trial state UX (S-2) — manueel onboarden zolang we onder ~50 sign-ups blijven
