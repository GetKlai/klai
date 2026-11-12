# De evolutie van Klai

**Stand: 12 november 2026.** Dit document beschrijft hoe het platform als geheel is geworden wat het nu is: welke onderdelen kwamen en gingen, welke besluiten de vorm bepaalden en waarom, en waar elk besluit vandaag staat. Het is het verhaal van de maanden maart tot en met november 2026, van de eerste commit op 4 maart tot nu. Wat er vandaag draait staat in de beschrijvende documenten; dit document zegt hoe het zover kwam.

Het detail zit elders:

- De chat, met alle metingen: [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md).
- De kennisbank (inname, zoeken, graaf): [knowledge-base-ingestion-and-retrieval-plan.md](knowledge-base-ingestion-and-retrieval-plan.md).
- Wat er nu draait: [platform.md](platform.md), [chat-system.md](chat-system.md), [knowledge-ingest-flow.md](knowledge-ingest-flow.md), [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) en [klai-knowledge-architecture.md](klai-knowledge-architecture.md).

Bronnen: een getal met `#` is een pull request op de hoofdlijn van deze repository, een hash zonder `#` is een commit (`git show <hash>`). Een datum is de datum waarop het op de hoofdlijn landde. Een getal staat er alleen als de PR-tekst, de commit of een bestaand document het noemt. Een uitspraak over vandaag draagt een pad met regelnummer dat bij het schrijven is geopend; een besluit zonder anker staat als historie en niet als huidige stand. De hoofdlijn telt 4397 commits (van 5652 in totaal), verdeeld over de maanden maart tot november als 635, 928, 993, 380, 22, 394, 551, 396 en 98 (`git log origin/main --first-parent`).

## 1. In één scherm

| Periode | Wat het systeem toen was | Wat het verder bracht |
|---|---|---|
| 4 tot 21 maart | Losse repositories. Een portal met inloggen via Zitadel, een aanmelding die per afnemer een eigen Zitadel-omgeving, een eigen LibreChat-container en een eigen LiteLLM-sleutel aanmaakt, en Mistral als enige modelleverancier achter LiteLLM | Afnemers vroegen om documenten in de chat (de trigger van fase 2 in `platform.md`), en op 22 maart werden alle repositories één monorepo (412fd49bf) |
| 22 tot 31 maart | Monorepo met een kennisplatform ernaast: knowledge-ingest, retrieval-api, Qdrant, een graaflaag, een connector-service, de docs-app, een MCP-server en een LiteLLM-hook die kennis in de chat bracht. GPU-werk verhuisde naar gpu-01 | De Partner API (13 april, 4df370b91) en de chatwidget (16 april, 68a56bf7d) |
| April | Open oppervlakken (Partner API, widget) en een eerste verharding: cookiesessie in plaats van bearer-token in de browser, transactionele provisioning, beveiligingsgolven, alerting, en een reeks nieuwe bronsoorten voor de kennisbank | De isolatie-audit van 5 mei (de auditmap met die datum onder `reports/`) |
| Mei en juni | Een product met een levenscyclus: afnemers kunnen verdwijnen (deprovisioning), rollen en prijs per gebruiker, de repository open source (13 mei), HubSpot en de browserextensie erbij, Klai Focus weg. De LiteLLM-hook groeide tot 7 juni door | De hook werd een grote module die splitsen vroeg (9abe19dca, 7 juni) |
| Juli en augustus | Juli is stil (22 commits). Augustus verhardt de laag eronder: een eigen LibreChat-image, Vexa 0.12, een controle op container-aanmaak, maskering van persoonsgegevens, afgeronde experimenten | Elk lopend experiment in zoeken en antwoorden kreeg een eindtoestand (#1118, #1122, 20 augustus) |
| September | Eén chatpijplijn voor widget, partner en interne chat; Coolify uit; kostenbeheersing op de modelaanroepen; kennisactiviteit en gaten met herkomst | De interne chat op het portalpad, per werkruimte omgezet (#1735) |
| 1 tot 29 oktober | De LiteLLM-hook verdwijnt (21 oktober, #1912); zoeken wordt per stap gemeten; Slack erbij; automatisch terugdraaien van een mislukte uitrol; bewaking tegen namen van afnemers en hosts in de repository | Het besluit "automatisch tenzij een machine het niet kan bewijzen" (#2139) |
| 30 oktober tot 12 november | Bewijzen vooraf voor dataopslag (kopie eerst), één scherm voor kennisgaten, verwijderen dat in alle opslagen doorwerkt (#2336, #2338, #2339, 12 november) | De stand van vandaag |

## 2. Chronologie per maand

Per maand staan de veranderingen die ertoe deden, onder vaste kopjes. Chat en kennisbank krijgen één regel met een verwijzing; platform, portal, inloggen, uitrol, privacy en de nieuwe oppervlakken (widget, Partner API, Slack, de browserextensie, de HubSpot-app, Scribe en vergaderbots, de docs-app, MCP) staan hier uitgebreider omdat geen ander document ze bijhoudt.

### Maart 2026

- platform en uitrol: eerste commit met gedeelde configuratie (3ccbe37ed, 4 maart); geheimen met SOPS en age op core-01 (2e650dec4, 6 maart); portal-api bouwt en deployt zichzelf naar core-01 na een imagebuild (4cd81f94a, 10 maart); alle repositories samengevoegd tot één monorepo (412fd49bf, 22 maart), GitHub Actions voor portal en docs (93d079228) en een GHCR-workflow per service (e34df5f48, 23 maart); mappen hernoemd naar `klai-*` (af38cbbc8, 30 maart); GPU-inferentie verhuisd naar gpu-01 met een SSH-tunnel naar core-01 (f5b9601fc, SPEC-GPU-001, 30 maart) en de TEI/Infinity-splitsing hersteld (0849abd37).
- platform en uitrol, nieuwe oppervlakken: de Vexa-vergaderbot met meetings-pagina's in de portal (22 maart), Scribe (in de monorepo opgenomen op 23 maart, 4d47d468d), de docs-app als lezer en REST-API voor kennisbanken (in de monorepo opgenomen op 22 maart, f9a85851c) en `klai-knowledge-mcp` als MCP-schrijfserver, op 30 maart naar de rootmap gehaald (30df9fa77).
- portal en inloggen: portal-scaffold met Zitadel (fc10c27f7, 6 maart); aanmelding die een tenant aanmaakt, met chat-integratie (bb30eaa3c, 7 maart); een eigen loginscherm op de Zitadel Session API (bf9abc951, 9 maart); TOTP en een SSO-sessie voor de chat in een iframe (2d09bba6e, 290e54c56, 10 maart); billing-API met mockmodus (a03e09871); groepen en productrechten via groepsovererving (94bc27b01, SPEC-AUTH-004, 24 maart); kennisbanken en doc-bibliotheken samengevoegd (1372f7fd4, #27, SPEC-KB-001, 25 maart).
- kennis-inname: de connector-service (f2c5460c5, SPEC-CONN-001, 23 maart), de webcrawler via Crawl4AI (25 maart) en artefacttracking (#29, 26 maart). Zie het kennisbanklogboek.
- zoeken: de LiteLLM-hook kreeg retrieval-api, autorisatie en persoonlijk bereik (9cdd427bb, 26 maart); de GPU-reranker (253bb769a, 31 maart); de specificatie van de graaflaag Graphiti met FalkorDB (a5613dda6, 26 maart); evidence-tier scoring gebouwd (38dc732e8, 30 maart), aangezet en nog op 31 maart teruggezet naar schaduw (fe46b31c0, ede7639e8). Zie het kennisbanklogboek.
- chat: LibreChat als compose-service met LiteLLM-aliassen (23 maart); een "smalle modus" met strikt kennisbankgebruik (17c3f05de, 31 maart), de oorsprong van Strict en Open. LiteLLM schakelde op 31 maart naar Anthropic omdat de Mistral-quota op was (8d7deb352) en op 1 april terug (e4b200106). Zie het chatlogboek.
- meten: detectie van kennisgaten met een dashboard (ea5930874, SPEC-KB-014, 27 maart); Grafana-dashboards (24 maart).
- privacy en beveiliging: NEN 7510-maatregelen met auditlog en afgedwongen MFA (e3fdc6f1a, 27 maart); Trivy-scan op alle imagebuilds (cabee2412, 27 maart), op 30 maart niet-blokkerend gemaakt (8c0c98954); de inzageverzoek-export (4682a491e, 28 maart); vergaderopnames worden na transcriptie verwijderd (SPEC-GDPR-002, 28 maart).

### April 2026

- platform en uitrol: transactionele tenant-provisioning met terugdraaien (125b4530b, SPEC-PROV-001, 21 april); persistentie van stateful services verhard, met `deploy/volume-mounts.yaml` als inventaris (068b23400, SPEC-INFRA-005, 22 april); alerting met een ops-mailadres als contactpunt (7336359ee, SPEC-OBS-001, 22 april); de Docker-socket achter een proxy met beperkte rechten (3949eace2, SPEC-SEC-021, 22 april); Vexa 0.10 met Scribe in de uitgestelde laag (ee11dacf5, SPEC-VEXA-003, 19 april), waarmee `whisper-server` uit productie ging.
- platform en uitrol, nieuwe oppervlakken: de Partner API (4df370b91, SPEC-API-001, 13 april); de insluitbare chatwidget (68a56bf7d, SPEC-WIDGET-001, 16 april) en de splitsing van API-sleutels en widgets (72be55f51, SPEC-WIDGET-002); de eerste widgetprompt met alleen kennisbankantwoorden (dfb7f9578, 17 april).
- portal en inloggen: SSO-zelfbediening met domeinlijst en toetredingsverzoeken (c3dbfac69, SPEC-AUTH-006, 16 april); cookiesessie (BFF) in plaats van bearer-token in de browser, in fasen A2 tot A4 op 19 april (21b870851, 9e7e44fbb, 45fc1b900), met het oude bearer-pad en de oude SPA-app weg op dezelfde dag (e2199a04e, 553bc9fd5); MFA sluit bij een fout (#181, 27 april); sessie- en cookievastheid (#197, 29 april); verscherpt intern geheim (#201, 29 april) en een toegestane-hostlijst voor callbacks (c8c5ff349, 29 april); de chat-templates (SPEC-CHAT-TEMPLATES-001, 81d075c96, 23 april).
- kennis-inname: Notion, Google Drive, Confluence, Airtable en Microsoft 365 als connectors, bestand-, URL- en YouTube-bronnen en de tweelaagse crawler. Een regel per onderdeel staat in het kennisbanklogboek.
- zoeken: taxonomie, bronbewuste selectie (bf97d2022, SPEC-KB-021, 17 april). Zie het kennisbanklogboek.
- chat: de prompts herschreven met Klai-stem (08e5523ef, 5 april); Anthropic tijdelijk als leverancier voor alle lagen (3f237f881, 1 april; 33505ed3e, 8 april), Mistral hersteld op 30 april (#236).
- meten: observability-alerts (SPEC-OBS-001); het zelflerend zoeken uit feedback (bae5e5bb7, SPEC-KB-015, 4 april).
- privacy en beveiliging: beveiligingsgolven SEC-005 tot SEC-023, met dekking op de loginroutes (#195, 28 april) en een mailer die tegen injectie is gehard (4c6a4caeb).

### Mei 2026

- platform en uitrol: tenant-deprovisioning in drie rondes (#273, #330, #343, 3 tot 5 mei) en de reparaties van 13 mei (#660); de Trivy-poort met één centraal beleid (#410, #417, 6 mei; zie het retro van die dag); de repository opgeschoond voor open source (3ba741ac6, SPEC-REPO-SANITIZE-001, 13 mei); de werkruimte voor platformbeheer (68d1dbef1, SPEC-PLATFORM-ADMIN-001, 22 mei).
- platform en uitrol, nieuwe oppervlakken: Klai Focus en research-api verwijderd (#368, 5 mei, na het uitzetten van de service op 23 april); het bijhouden van de widgetgesprekken (4286fd78f, 21 mei); de HubSpot-koppeling met realtime overdracht (8025237a5, c5825ff53, 27 mei) en de kennisbewuste partnerchat voor HubSpot-concepten (93c60ae02); de `search_knowledge`-tool in de MCP-server voor externe taalmodellen (#489, 7 mei).
- portal en inloggen: werkruimte-ontdekking op het e-maildomein van de oprichter en overdracht van beheer (#259, SPEC-AUTH-009, 4 mei), die de domeinlijst van SPEC-AUTH-006 vervangt; rollen en profielen (#300, SPEC-PORTAL-RBAC-001); prijs per gebruiker, waarmee de ladder van vier abonnementen naar twee ging (#575, #599 tot #621, 12 en 13 mei); eigendom van kennisbanken en overdracht bij het vertrek van een medewerker (#615, 13 mei); Klai-merk op de e-maillinks van Zitadel en inloggen na het zetten van een wachtwoord (da039619d, 11cfa816e, 13 mei).
- kennis-inname: de bestandsupload via docling-serve (#553 tot #558, 10 en 11 mei), de verzoening van connectors met volledige dekking (#440, 6 mei), parent-child-chunking (#338) en `rebuild_kb` (#341). Zie het kennisbanklogboek.
- zoeken: contextuele retrieval (#329), een taxonomiefilter (#340) en herschrijven van de zoekvraag (#334). Zie het kennisbanklogboek.
- chat: een antwoordlaag die elke taal aankan (#454, 6 mei), terughoudendheid bij weinig vertrouwen (merge van #516, 7 mei), de vragen over het systeem zelf (#577, 12 mei), de widget die zonder citeerbare bron niet antwoordt (6a1ee1163, 14 mei), bewijspakketten en één citatiearchitectuur (dd426ac7c, 25 mei) en veiligheidsrails (63795f584, 28 mei). Zie het chatlogboek.
- meten: de RAGAS-meetset en de nachtelijke run (#303, #369, 4 en 5 mei).
- privacy en beveiliging: rij-niveau-beveiliging op de kennis- en connectorschema's (#375, #376, 6 mei); het retro over een week zonder kennisbankcontext (zie sectie 3); de dwars-tenant-opvolging (168e917c1, 25 mei); een opruimlus die de tekst van zoekvragen na zeven dagen verwijdert (c1059b358, 8 mei; de modi staan in `docs/privacy/telemetry-modes.md`).

### Juni 2026

- platform en uitrol: LibreChat v0.8.6 over alle tenants (2324c82a1, 6 juni); tenant-zoekindexen in Meilisearch afgebakend (69c314f73, 6 juni); de MCP-serverbeheerkant (a05c131c5, 11 juni); dode GPU-operatie uit de publieke repository gehaald (175bfe8bc, 8 juni).
- platform en uitrol, nieuwe oppervlakken: Shield 0.1, de Chrome-extensie voor beheerders (26e38a86e, SPEC-SHIELD-001, 3 juni); de OpenAI-compatibele partnerchat en de Responses-API (2bb517ff8, 906dcd181, 25 juni); expliciete Klai Docs-tools in de MCP-server in plaats van `save_to_docs` (0af41f298, 26 juni).
- portal en inloggen: herstel van uitnodigingen die vastliepen en een modern wachtwoordbeleid (#762, #767, #770, 5 juni); Google-aanmelding zonder bestaand account (#753, 4 juni).
- kennis-inname: Zie het kennisbanklogboek.
- zoeken: de poort voor retrieval in schaduw (#818, 11 juni). Zie het kennisbanklogboek.
- chat: het kennisbankbeleid uit de hook gehaald in een eigen module (a2137e842, 6 juni), de hook in stukken (#807) en `partner.py` in stukken (#811), strikt-kennisbank met een vaste weigering in code (38493785a). Zie het chatlogboek.
- meten: een synthetische Mistral-probe met alert (ec70502f1, 18 juni).
- privacy en beveiliging: JWT per service voor de overgang naar retrieval-api vervangen door het interne geheim (#804, 7 juni).

### Juli 2026

De stilste maand: 22 commits op de hoofdlijn.

- platform en uitrol: afhankelijkheden en imagepinnen ververst (9dace818a, 8 juli); platformgebruik in het beheerscherm (000232fec, 7 juli).
- chat: rangschikkingscontract van schaduw naar actief (#828, 7 juli), bronvermelding (#830, #831, 8 juli) en de taalafsluiting van elke prompt (#823, 7 juli). Zie het chatlogboek.
- zoeken: een bescherming tegen herschrijven dat de vraag verstoort en een expliciet `coreference_resolved`-contract (90710dc90, 0b04e5eeb, 9 juli). Zie het kennisbanklogboek.

### Augustus 2026

- platform en uitrol: LibreChat v0.8.7 met herbaseerde patches, daarna de Klai-eigen LibreChat-image in vijf fasen (#874 tot #947, 13 en 14 augustus; zie sectie 4); Vexa 0.10 weggehaald nadat 0.12 een echte vergadering ketenbreed had gehaald (#914, #938, 14 augustus); Renovate als eigen GitHub-app met autonoom mergen van patches en minors (#912, 14 augustus); de route van de ontwikkelomgeving uit Caddy gehaald (#1017, 17 augustus); `klai-docker-authz` controleert aanmaakverzoeken van containers per aanvrager (#950, 14 augustus); vergaderbots kunnen de host niet meer bereiken (#1027, 17 augustus).
- portal en inloggen: domeingebaseerde toetreding in de aanmeldflows (#841, SPEC-AUTH-010, 12 augustus) en koppeling van een IdP-identiteit aan een bestaand account (#844).
- kennis-inname: crawlen op sites met een verborgen body 5 keer sneller (#838), de crawl4ai 0.9-client (#873), snelheidsbeperking die nu echt remt (#1052, #1056), de JSON-feed-connector (#1087) en structuurbewuste inname (#1224). Zie het kennisbanklogboek.
- zoeken: de graaf maakt feiten citeerbaar (#1146, #1147), canonieke entiteitsnamen (#1175), meerdere episodes per document (#1181). Zie het kennisbanklogboek.
- chat: geplakte derde-partijcorrespondentie als bewering behandeld (#1059), uitwaaieren per deelvraag (#1060). Zie het chatlogboek.
- meten: de retrieval-experimenten (poort, evidence tier) opgeruimd tot een eindtoestand (#1118, #1122, 20 augustus).
- privacy en beveiliging: Presidio fase 0 tot 3 (#1121, #1125, #1128), activatie voor één organisatie (#1135, 21 augustus), voor alle organisaties (#1219, 25 augustus) en uitgebreid naar de EU (#1222); content-logs 7 dagen bewaard via een gesplitste VictoriaLogs-instantie (fb3595cbf, 26 augustus).

### September 2026

- platform en uitrol: oude Vexa-hostnamen uit de Caddy-upstreamlijsten gehaald (c04f7ae90, 1 september); bovengrenzen op de modelkosten: prompt-caching per organisatie (#1676), een tweede organisatie als reserve (#1685, #1690), een daglimiet zonder terugval naar Medium (#1742), een eigen alias voor bulkwerk van de inname (#1752); de schijf van core-01 die niet meer volloopt en deploys die voor de helft landen vallen vooraf af (#1534, 18 september); serieel deployen van portal-api (#1547); Calnode neemt de boekingen over van een zelfgehoste cal.com (#1611, #1621, 23 en 24 september); Twenty CRM, Umami, Fider en Uptime Kuma verhuizen van Coolify naar de compose op core-01 (#1636 tot #1662, 24 september) en Coolify gaat uit (#1665, #1671, 25 september).
- platform en uitrol, nieuwe oppervlakken: de widget wordt één asynchroon scripttag (#1362, 10 september); de connector-scheduler haalt cron uit de portal (#1360); de HubSpot-tickets uit een beoordeeld gesprek (#1773, 30 september).
- portal en inloggen: de interne chat per tenant op het portalpad (#1735, 26 september); een beheerde sleutel voor de interne chat die alleen provisioning aanmaakt (zie sectie 5, regel over beheerde sleutels).
- kennis-inname: begrenzing van graafextractie (#1745), annuleren per resource met generatiebewaking (#1264), de crawler die pagina's op de uiteindelijke HTTP-status beoordeelt (#1764). Zie het kennisbanklogboek.
- zoeken: het spoor van elk zoekverzoek (#1265, #1276) en kandidaten voor de graaf via ANN (#1263, #1272, #1278, #1287). Zie het kennisbanklogboek.
- chat: één pijplijn voor widget, partner en interne chat (#1663, 24 september), de vraagstap (#1471 tot #1475), de beoordelaars (#1480), de controle per zin met reparatie (#1495). Zie het chatlogboek.
- meten: kennisactiviteit fase 0 tot 3 met beoordeling van gesprekken en gaten met herkomst (#1434, #1441, #1446, 15 september); een gesimuleerde bezoeker voor hele gesprekken (#1519).
- privacy en beveiliging: PII-maskering standaard aan voor elke tenant (25523de4b, 3 september); interne aanroepen naar Mistral dragen de tenant zodat ze worden gemaskeerd (700e1935a); een bewaking die vooraf controleert of klantgegevens in de publieke repository belanden (#1531, #1556).

### Oktober 2026

- platform en uitrol: één hostslot voor elke containeruitrol op core-01 in plaats van een GitHub-concurrencygroep (#1921, 21 oktober); LibreChat v0.8.8 (#1946); Postgres 18, Redis 8 en RabbitMQ 4 voor de ondersteunende services (#1964); back-up van Meilisearch en een tweede back-uprun die weigert te starten zolang een andere het slot heeft (#1966, #1977); automatisch terugdraaien van stateless uitrol, per service (#2089, 28 oktober), van portal-api met een expand-then-contract-controle (#2106), van data stores met een snapshot en herstel (#2109, #2122), met een foutpercentagebewaking als vierde poort (#2108) en melding aan mensen (#2101); een kopie-eerst-bewijs voor een nieuw data-store-image (#2210, 30 oktober).
- platform en uitrol, nieuwe oppervlakken: Klai in Slack, een eigen service `klai-slack` met een beheerde sleutel en een beheerpagina (#2073, SPEC-SLACK-001, 28 oktober), verbinden vanuit de portal in twee klikken (#2084) en het token via een formulier-POST in plaats van in een URL (#2088); Scribe transcribeert op de achtergrond (#2207).
- portal en inloggen: de LiteLLM-hook weg en de interne chat alleen via de partnerroute van portal-api (#1912, 21 oktober); een partnersleutel of sessietoken van een zacht verwijderde organisatie authenticeert niet meer (#1918).
- kennis-inname: linklijsten en stubs buiten de index (#1994) en een sparse vector op elk chunk (#1995); inkomende linkteksten gefilterd voor het embedden (#2154); één URL per crawlverzoek, één verzoek per seconde per host (#2078). Zie het kennisbanklogboek.
- zoeken: zoeken gemeten per stap met 60 reranker-kandidaten (#1997); vertaal- en aliaszoekbenen (#2004); 250-tokenpassages (#2005); productlabels en gelijkende producten (#2020); linkexpansie verwijderd (#2009). Zie het kennisbanklogboek.
- chat: één pad voor widget en partner (#1866, 14 oktober), de interne chat op dezelfde stroom (#1855), één schrijfstap (#1945), één antwoordcontrole (#1953), de taal in de schrijver vastgezet (#2180, #2208). Zie het chatlogboek.
- meten: de `meetlat`-CLI (#1934); een synthetische bezoeker die de klantwidget elke vijf minuten iets vraagt (#1998), later elke 30 minuten (#2051); kennisgat-clusters per week (#2072); de eis dat elke PR-tekst een `Meting`-regel draagt (#2087).
- privacy en beveiliging: klant- en hostnamen in de repository geblokkeerd, met een namenlijst die elke nacht uit de portal wordt gegenereerd (#2151, #2155, #2160, #2162, 29 oktober); de klantspecifieke evaluatiesets uit de publieke repository (#2028).

### November 2026

- platform en uitrol: Postgres-rollen in elke snapshot en herspeld in een wegwerpomgeving bij herstel (#2313, 10 november); de rollback-oefening vergelijkt image-lagen in plaats van het image-ID (#2311).
- portal en inloggen: de instellingen voor contactwegen per soort vraag in de widget (#2322, #2326, 11 november), de oude instellingen weg (#2324).
- kennis-inname: een verwijderd bronbestand verdwijnt bij de volgende complete synchronisatie uit de kennisopslag (#2336, 12 november); verwijderen werkt door in alle opslagen en vervangen houdt de geschiedenis (#2338); een Notion-databasefilter accepteert een link en laat de synchronisatie mislukken als de database niet te lezen is, in plaats van leeg af te ronden (#2340, 12 november). Zie het kennisbanklogboek.
- zoeken: de graaf houdt alleen de huidige extractie van een document en verlopen feiten komen niet meer terug (#2339, 12 november); een graafextractie die bij een herstart van de graafdatabase verloren ging wordt voorkomen met een langere afsluittijd, 's nachts gevonden door de reconcile en per document hersteld met een script (#2341, 12 november). Zie het kennisbanklogboek.
- chat: gesprekken die op een contactroute eindigen heten voortaan doorverwezen (#2328, 11 november). Zie het chatlogboek.
- meten: zichtbare en telbare antwoordbeoordelingen in de widget (#2319, 11 november); één scherm voor kennisgaten met een gegroepeerde lijst en voorstellen, de oude inbox weg (#2321, #2325), en een gat blijft vier weken bewaard op volle telemetrie (#2332).

## 3. Incidenten die de vorm veranderden

De volgende gebeurtenissen staan als retro in `docs/retros/`, of in de PR-tekst die het herstel bracht (de twee van 23 oktober). Hier staat per geval alleen wat er daarna anders is.

| Datum | Wat er gebeurde | Wat er daarna anders is |
|---|---|---|
| 5 mei | Een verschil tussen de eisen van de aanroepers en de eisen van retrieval-api liet zeven dagen lang elk chatantwoord zonder kennisbankcontext lopen, zonder foutmelding (retro van 2026-05-05) | Elke aanroeper heeft een test op de header die hij meestuurt, retrieval-api heeft een contracttest aan de ontvangende kant, een mislukte zoekopdracht is een fout in plaats van een waarschuwing, en er is een alert die binnen 15 minuten afgaat |
| 6 mei | Twee blokkades bij de uitrol van de verzoening van connectors: twee migratieketens met elk een eigen kop, en een controle met een subquery die Postgres weigert. Beide containers draaiden in een herstartlus tot het herstel binnen ongeveer 20 minuten kwam; het viel op bij het herstarten na de merge en niet in CI (retro van 2026-05-06) | Een samenvoegmigratie (#443) en een herschreven controle zonder subquery. Het retro noemt een CI-controle op één migratiekop per service en een test tegen echte Postgres als vervolg; de bron zegt niet dat ze gebouwd zijn |
| 6 mei | De Trivy-poort bleek deels schijn: negen van tien scans waren waarschuwend en het ernstfilter werd voor de uitkomst van de scan genegeerd (retro van 2026-05-06) | Eén centraal beleid (`.trivy.yaml`, #410), een poort die op hoge en kritieke bevindingen echt faalt (#417) en uitzonderingen met een vervaldatum van twee maanden |
| 7 mei | Een nieuwe mount in de LiteLLM-uitrol is met `docker compose restart` niet doorgevoerd, waardoor de chat via pad A ongeveer 15 minuten uitviel (retro van 2026-05-07) | De uitrol van de hook gebruikt `compose-up.sh`, de verpakking die elke andere service al gebruikte (#475), en de lint blokkeert de ingebakken constante die de storing veroorzaakte |
| 8 juni | Het temporele filter in retrieval deed niets door een veldnaamverschil tussen ingest en retrieval (retro van 2026-06-08) | Een filter dat beide veldnamen leest en een bijwerking van de payload bij vervanging; opgelost op de serveerlijn op 11 juni (statusnotitie in het retro) |
| 23 oktober | Vanaf 02:01 eindigde elke nachtelijke crawl van elke kennisbank met 0 pagina's: crawl4ai 0.9.5 (een beveiligingsrelease, sinds 22 oktober in productie) weigert een bepaald paginascript en de crawler stuurde dat bij elke pagina mee. De taak noteerde "de site blokkeert misschien" en geen alert keek naar crawltaken; zes nachten, drie kennisbanken (#2071) | De kopregel-strip loopt via configuratie zonder paginascript, een geweigerde crawl heet `crawler_config_rejected`, er zijn vier alerts op crawlresultaten en een CI-test stuurt de echte crawlconfiguratie naar het vastgepinde image |
| 23 oktober | De klantwidget antwoordde van ongeveer 03:00 tot 10:40 UTC met een foutmelding en het eerste signaal kwam van een afnemer (#1998) | Een synthetische bezoeker haalt een token op en stelt een vraag; ok betekent een antwoord met minstens één bron binnen 30 seconden. Eerst elke vijf minuten, sinds 26 oktober elke dertig minuten (#2051) |

## 4. Besluiten en waar ze nu staan

De volledige oorspronkelijke tekst van `platform.md` (de stack, de fasen, de modelstrategie en het compatibiliteitsonderzoek van 3 maart) staat in de gitgeschiedenis van dat bestand vóór deze consolidatie: de laatste versie vóór deze consolidatie is te lezen met `git show f2b71ca96:docs/architecture/platform.md` (#1912). Hieronder staat één rij per besluit. "Geldt" betekent dat een ankerbestand dat bij het schrijven is geopend het besluit nog laat zien. "Vervangen" noemt de datum en wat ervoor in de plaats kwam. Een plan dat nooit is uitgevoerd staat als gepland, niet gebouwd, met wat er in plaats daarvan draait.

### 4.1 Stack en inrichting

| Besluit | Wanneer | Waarom (en verworpen alternatief) | Stand vandaag |
|---|---|---|---|
| Portalfrontend als Vite + React-SPA met Tailwind v4, zonder server-rendering | maart | Het is een ingelogde app, SSR is niet nodig; het ontwerpsysteem van een partner zou er direct in werken | Geldt voor Vite, React en Tailwind (`klai-portal/frontend/package.json:59,104,108`). Het ontwerpsysteem van de partner is niet gevonden en staat niet in de code; de UI gebruikt Mantine (`klai-portal/frontend/package.json:37`) |
| TanStack Router en Query; React Router v7 en SWR als alternatief | maart | Typeveilige routes en zoekparameters; caching en retry voor dashboard en mutaties | Geldt (`klai-portal/frontend/package.json:47,48`) |
| FastAPI voor alle backends, in één taal | maart | Provisioning, billing, Scribe en later RAG in dezelfde taal | Geldt (`klai-portal/backend/pyproject.toml:6`) |
| Openbare API als Partner API op portal-api | 13 april (4df370b91) | Kennisgrondede chat en een aparte OpenAI-compatibele doorgang, sleutels met kennisbankbereik | Geldt (`klai-portal/backend/app/api/partner.py:3`); uitgebreid met een OpenAI-compatibel eindpunt en Responses (2bb517ff8, 906dcd181, 25 juni) |
| Embedbare widget in SolidJS, geserveerd vanaf de portal-oorsprong | 16 april (68a56bf7d) | Eén tag, kleine bundel, JWT in plaats van een sleutel in de browser | Geldt (`klai-widget/package.json:17`); widgets en API-sleutels zijn sinds SPEC-WIDGET-002 twee tabellen (`klai-portal/backend/app/models/widgets.py:44`, `klai-portal/backend/app/models/partner_api_keys.py:19`) |
| Billing via Moneybird in plaats van een eigen Mollie-module | maart | Moneybird levert facturering, btw, SEPA-incasso en een klantportaal; de eerder geplande eigen module vervalt | Geldt (`klai-portal/backend/app/services/moneybird_client.py`, `klai-portal/backend/app/api/billing.py`) |
| LibreChat zonder fork als chat-UI; een eigen UI pas als LibreChat aantoonbaar blokkeert | maart | De bron noemt geen verdere reden | Gewijzigd op 14 augustus (#947): een door Klai gebouwde LibreChat-image met patches als broncode-diff, omdat de patch-mounts bij elke versie herbaseerd moesten worden (een bump werd teruggedraaid, #864). Het antwoord zelf komt sinds 13 oktober van portal-api en niet meer van LiteLLM; LibreChat blijft de interface van de interne chat (`deploy/docker-compose.yml:1089`) |
| Zitadel met Google en Microsoft als IdP | maart | Natuurlijke B2B-multitenancy; de aanbevolen code-flow met PKCE | Geldt (`deploy/docker-compose.yml:321`). De manier van inloggen is vervangen: de SPA met bearer-token door een cookiesessie (BFF) op 19 april (`klai-portal/backend/app/api/auth_bff.py:1`) |
| Domeinlijst en toetredingsverzoeken voor SSO-zelfbediening | 16 april (SPEC-AUTH-006) | Mensen met een bekend domein komen zelf binnen | Vervangen op 4 mei door werkruimte-ontdekking op het domein van de oprichter (SPEC-AUTH-009, #259); de tabel met toegestane domeinen is verwijderd. Op 12 augustus kwam domeingebaseerde toetreding in de aanmeldflows (#841). Het veld staat in `klai-portal/backend/app/models/portal.py:116,119` |
| LiteLLM OSS, naar Enterprise bij de eerste omzet | maart | Gratis starten; Enterprise voor een auditspoor | Geldt voor OSS: het compose-bestand bevat geen spoor van een Enterprise-licentie. De auditspoor-reden is niet ingelost |
| Eigen routering in `custom_router.py` (drie signalen: toolaanroep, tokendrempel, drie of meer URL's), niet de ingebouwde Complexity Router | maart | De bron noemt geen reden voor de eigen router; het compatibiliteitsonderzoek noemde de ingebouwde router nieuw en nog weinig beproefd | Geldt (`deploy/litellm/config.yaml:396`); de Complexity Router is niet ingevoerd |
| Caddy als reverse proxy | maart | Beter voor streamen van modeluitvoer dan Traefik, lichter | Geldt (`deploy/docker-compose.yml:182`). Wildcard-TLS gaat met een eigen Caddy-build en een DNS-provider-plugin (`deploy/caddy/Dockerfile:42`) |
| Een aparte routeringsdienst voor werkruimtes (FastAPI-dispatcher), aanbevolen in het compatibiliteitsonderzoek | maart | "Caddy regelt het automatisch" bleek een versimpeling | Niet gebouwd. In plaats daarvan schrijft portal-api bij provisioning een Caddyfile per werkruimte (`deploy/caddy/Caddyfile:659-666`) |
| public-01 met Docker en Coolify; core-01 met Docker direct | maart | Op public-01 beheerde Coolify vier diensten en de website, met de definitie in zijn eigen database (SPEC-INFRA-COOLIFY-EXIT-001) | Vervangen op 25 september (#1671): alles staat als compose-bestand in git en Coolify is verwijderd (sectie 5). core-01 draait direct op Docker met `compose-up.sh` |
| Eén Zitadel-omgeving, één LibreChat-container en één LiteLLM-sleutel per afnemer; een MongoDB-database per werkruimte op een gedeelde server | maart | Isolatie op opslagniveau en een rollback per afnemer; FerretDB op Postgres stond als alternatief in het onderzoek, met de kanttekening dat het minder beproefd is | Geldt voor de LibreChat-werkruimtes (`klai-portal/backend/app/services/provisioning/orchestrator.py:5`). De tabellen van de portal zijn daarnaast met rij-niveau-beveiliging per afnemer afgeschermd (SPEC-TI-002/003, #375, #376) |
| Meilisearch gedeeld met per werkruimte afgebakende indexen | juni (canary op 6 juni) | Globale indexen zijn onveilig met een database per werkruimte: start-synchronisatie en verwijderhaken muteren indexen op primaire sleutel | Geldt: `{slug}_messages` en `{slug}_convos` met een afgebakende sleutel (`klai-portal/backend/app/services/provisioning/generators.py:317-320`) |
| Ontwikkelomgeving als parallelle stack met eigen LibreChat, LiteLLM en database | april | De bron noemt geen verdere reden | Beëindigd: de route is op 17 augustus uit Caddy gehaald, vier maanden nadat de backend was verdwenen (#1017). Lokale ontwikkeling staat in `GETTING_STARTED.md` |
| Kubernetes: zo lang mogelijk uitstellen | maart | De oude lijst met bewuste uitsluitingen noemt geen drempel, alleen uitstel | Niet gebouwd: de uitrol is Docker Compose (`deploy/docker-compose.yml`) en `deploy/` bevat geen Kubernetes-bestanden |
| Zustand voor de frontend-toestand: React Context volstaat | maart | Auth- en gebruikersstatus verandert zelden; trigger voor heroverweging: aantoonbare prestatieproblemen | Niet toegevoegd: `klai-portal/frontend/package.json` bevat geen Zustand en de authenticatiestatus loopt via React Context (`klai-portal/frontend/src/lib/auth-context.ts:9`) |

### 4.2 Fasen en modelhosting

| Besluit | Wanneer | Waarom (en verworpen alternatief) | Stand vandaag |
|---|---|---|---|
| Fase 0 en 1: Caddy, Zitadel, MongoDB, LiteLLM, Mistral, LibreChat en observability; daarna de portal met billing en provisioning | maart | Trigger van fase 0: de eerste opzet; van fase 1: de eerste betalende afnemer | Gebouwd en in gebruik |
| Fase 2: kennisplatform met knowledge-ingest, Qdrant, retrieval-api, bge-m3-sparse en MCP | maart | Trigger: afnemers vragen om documenten | Gebouwd (`deploy/docker-compose.yml:1603,2120,2188`). De LiteLLM-hook uit die fase is vervangen op 21 oktober (sectie 5) |
| Fase 3: zelf hosten op een GPU-server ai-01 met vLLM en Qwen3-32B plus Qwen3-8B op één H100, bij het break-evenpunt van ongeveer 1,2 miljard tokens per maand (ongeveer 30.000 actieve gebruikers) | maart | Een vaste GPU wordt goedkoper dan de Mistral-API vanaf dat punt; Qwen3-32B scoort volgens het onderzoek hoger op redeneren dan Mistral Small 3.2 (62 tegen 46 procent GPQA) en twee modellen met routering sparen stroom en geheugen | Gepland, nooit gebouwd. Er draait Mistral via de API (`deploy/litellm/config.yaml:38-220`) en een GPU-server gpu-01 voor embeddings, reranker en spraak-naar-tekst (`deploy/docker-compose.gpu.yml:1`), geen ai-01 en geen vLLM |
| Fase 4 (SAML en SCIM bij de eerste enterprise-afnemer) en fase 5 (LiteLLM Enterprise bij audit-logs of meer dan vijf beheerders) | maart | Triggers staan in de kolom links | Niet gebouwd: de portal bevat geen SAML- of SCIM-code (zoekopdracht op `klai-portal/backend/app`) |
| Privacy-first: alles binnen de EU, geen gesloten API's van niet-Europese bedrijven; een beheerde API op open gewichten van een Europees bedrijf is een aanvaardbare tussenstap | maart | De bron noemt het een kernwaarde en geen technische keuze | Geldt: alleen Mistral staat in `deploy/litellm/config.yaml`. Onderbroken van 31 maart tot 30 april, toen LiteLLM tijdelijk naar Anthropic ging omdat de Mistral-quota op was (8d7deb352, 33505ed3e, #236) |
| Mistral als eerste modelleverancier met een Ollama-terugval | maart | Frans bedrijf, Europese infrastructuur, open gewichten, geen training op klantdata | Mistral geldt. De terugval is `klai-large`, niet Ollama (`deploy/litellm/config.yaml:365`); Ollama staat in het compose-bestand maar is niet aangesloten (`deploy/docker-compose.yml:690`) |
| Rolaliassen `klai-pipeline` (31 maart, af1145072) en `klai-embeddings` (in de config sinds 5 mei, #312) | maart | Een alias per taak (de bron noemt geen verdere reden) | Beide vervangen op 5 mei (#321) door directe modelaliassen; sinds 27 september is er een `klai-ingest` voor bulkwerk van de inname (#1752, `deploy/litellm/config.yaml:300`) |
| Aliassen `klai-fast`, `klai-primary` en `klai-large` op Mistral Small en Large | maart | Een snelle laag, een standaardlaag en een zware laag voor tools | Uitgebreid: vandaag `mistral-small-2603`, `mistral-medium-3.5` en `mistral-large-2512`, naast `klai-judge` en `klai-ingest` (`deploy/litellm/config.yaml:38,105,137,268,300`) |
| Modellen die zijn afgewezen: Llama 3.3 70B (past niet op één H100), DeepSeek V3.2 en Mistral Large 3 (te veel GPU's), Gemma 3 (de leverancier kan de uitrol op afstand stoppen), Llama 4 (EU-beperking in de licentie), Mixtral en Qwen2.5 (achterhaald), GPT-NL (uitgesteld tot eind 2026 of 2027) | maart | Zie de kolom links | Historisch; geen van deze modellen wordt gehost. Op eigen hardware (gpu-01, `deploy/docker-compose.gpu.yml`) draaien kleine modellen: de embeddings van BGE-M3 (`deploy/litellm/config.yaml:319`), de reranker bge-reranker-v2-m3 (`deploy/docker-compose.gpu.yml:34-42`) en de transcriptiewerkers met faster-whisper (`deploy/docker-compose.gpu.yml:93-148`); grote taalmodellen draaien niet op eigen hardware |
| Mistral Small 3.2 als stand-by naast Qwen3-32B | maart | Trigger: Qwen3-32B loopt tegen zijn contextgrens aan (128K tegen 32K) op dezelfde H100 | Trigger nooit bereikt, omdat Qwen3-32B nooit is gehost (fase 3, hierboven); de chat draait op Mistral Small via de API, nu `mistral-small-2603` (`deploy/litellm/config.yaml:38`) |
| LLM voor verrijking: Qwen2.5-14B zelf gehost als aanbeveling, Mistral Small 3.1 via API als start; geen Anthropic in de keten | maart | Gevoelige brondata mag niet naar een cloud-API, Mistral als instap bij laag volume | Vervangen: de verrijking draait op Mistral via LiteLLM (`klai-knowledge-ingest/knowledge_ingest/enrichment.py:2`). Een zelfgehoste Qwen-service staat niet in `deploy/docker-compose.gpu.yml` |
| Scribe: faster-whisper op ai-01 naast vLLM | maart | Eén GPU voor alles | Vervangen: spraak-naar-tekst draait als twee Whisper-workers op gpu-01 uit de Vexa-stack (SPEC-VEXA-003, 19 april); `whisper-server` draait niet in productie (`deploy/VERSIONS.md:11`) |

### 4.3 Kennisontwerp en zoeken

| Besluit | Wanneer | Waarom (en verworpen alternatief) | Stand vandaag |
|---|---|---|---|
| Kwaliteit wordt bij het opslaan bepaald, niet bij het zoeken | 25 maart | Contextuele metadata bij opslag geeft 49 procent minder zoekfouten (Anthropic-onderzoek, zoals `platform.md` het citeert); geen zoektechniek maakt een slechte inname goed | Geldt: de verrijking is een stap van de inname (`klai-knowledge-ingest/knowledge_ingest/enrichment.py:2`) |
| Drie opslaglagen: Postgres, Qdrant, FalkorDB met Graphiti | maart | Elke laag wint op iets anders; de combinatie verslaat een enkele aanpak | Geldt (`deploy/docker-compose.yml:2120,2142`; `GRAPHITI_ENABLED` op regel 1627 en 2221). FalkorDB boven Apache AGE omdat een grote Postgres-upgrade AGE breekt |
| Eerst alleen HyPE, geen contextuele retrieval | 25 maart | HyPE dicht het woordenschatgat tussen vraag en document | Vervangen: contextuele retrieval kwam een dag later (#31, 26 maart) en is op 5 mei herbouwd (#329). Beide staan nu in `klai-knowledge-ingest/knowledge_ingest/enrichment.py:2` |
| Hybride verwerking: 90 procent automatisch en 10 procent menselijke review | 25 maart | Volledig automatische extractie maakt 20 tot 45 procent fouten op zakelijke documenten | Niet als reviewstroom gebouwd. De evidence-tier scoring (38dc732e8, 30 maart) is op 20 augustus verwijderd na een schaduwrun die 2186 van 8946 volgordes veranderde zonder gepaarde antwoordkwaliteit (#1118, #1122). Het rangschikkingscontract is nu de enige volgorde (`klai-retrieval-api/retrieval_api/api/retrieve.py:645-646`) |
| De graaf pas toevoegen na een steekproef van 200 echte vragen | maart | Graaf-RAG helpt vooral bij multi-hop vragen en veel zakelijke vragen zijn enkelvoudig | Vervangen: de graaf staat aan in productie en wordt daar gemeten; hij is in april (Delta 2026-04) weer aangezet nadat LLM en reranker op gpu-01 draaiden (`deploy/docker-compose.yml:1627,2221`) |
| Eigen RAG-diensten (Track B) in plaats van de `rag_api` van LibreChat (Track A) | maart | LangChain-afhankelijkheid, geen Qdrant, zwakke tenantisolatie | Geldt: knowledge-ingest en retrieval-api (`deploy/docker-compose.yml:1603,2188`). Haystack en LlamaIndex zijn niet gekozen |
| BGE-M3 als embeddingmodel; Jina (CC BY-NC) en PyMuPDF4LLM (AGPL) uitgesloten | maart | De enige MIT-licentie met sterk meertalig dense plus sparse; de uitsluitingen zijn licentiegronden | Geldt voor BGE-M3 op gpu-01; PyMuPDF komt in geen enkel afhankelijkheidsbestand voor (zoekopdracht op `*.toml`, `*.txt`, `*.lock`) |
| docling-serve voor het parsen | maart | Layout- en tabelherkenning, lokaal | Geldt (`deploy/docker-compose.yml`, service `docling-serve`) |
| Webzoeken via een zelf gehoste SearXNG zonder Google en Bing | maart | Google blokkeert datacenter-adressen en Bing sloot zijn publieke API; Startpage en DuckDuckGo blijven werken | SearXNG draait (`deploy/docker-compose.yml:1591`); voor de partnerchat kwam webzoeken met een eigen citatielaag op 5 juni (1c173905c, 2468aeada) |
| Vijf universele entiteitstypen en zes relaties, plus een besluittype | 25 maart | Acht onafhankelijke kennissystemen kwamen op dezelfde vorm uit (`docs/research/knowledge-system-fundamentals.md`) | Onderzoeksprincipe zonder codeanker; niet als geldend of vervangen vastgesteld |
| Detectie van kennisgaten (SPEC-KB-014) | 27 maart | De bron noemt geen reden | Vervangen door de gegroepeerde kennisgatenlijst met voorstellen (#2072, #2321, 11 november); de oude inbox is verwijderd |

### 4.4 Portal, privacy en bedrijfsvoering

| Besluit | Wanneer | Waarom (en verworpen alternatief) | Stand vandaag |
|---|---|---|---|
| Templates als stijl-functie, niet als veiligheidslaag; toegepast in de LiteLLM-hook | april | Stijl en veiligheid zijn verschillende producten met verschillende cache | De opslag geldt (`klai-portal/backend/app/models/templates.py`, `klai-portal/backend/app/api/app_templates.py`); de toepassing in de hook is vervangen toen de hook op 21 oktober verdween |
| Guardrails als een apart `klai-pii`-microservice met Presidio en GLiNER | april | Strikte regels op het gebruikersbericht, gescheiden van stijl | Niet zo gebouwd. Veiligheidsrails kwamen op 28 mei (63795f584) en maskering van persoonsgegevens liep via Presidio-diensten en LiteLLM-callbacks (`deploy/docker-compose.yml:545`; `deploy/litellm/config.yaml:385-400`, vanaf 20 augustus) |
| Inzageverzoeken en dataminimalisatie | maart | AVG artikel 15 en 5, lid 1, onder c | Geldt (`klai-portal/backend/app/api/me.py:532`; `klai-portal/backend/app/models/meetings.py:43,44`) |
| Zoekvragen bewaren in drie privacymodi | 8 mei | Minimale gegevens tegenover genoeg signaal voor kwaliteit | Geldt; de tekst van een vraag staat nooit langer dan zeven dagen opgeslagen (`docs/privacy/telemetry-modes.md`) en content-logs zitten op een eigen zevendaagse instantie (`deploy/docker-compose.yml:131,1400`) |
| Back-up: nachtelijke MongoDB- en Postgres-dumps, configuratie en restic, naar objectopslag | maart | Chatgeschiedenis is onvervangbaar; de opslagkosten bleven volgens de berekening onder 20 euro per maand tot 500 werkruimtes | De opzet is veranderd: het draaiende script is `deploy/scripts/backup.sh` (dagelijks om 02:00, `deploy/scripts/backup.sh:7`) met een inventaris uit `deploy/volume-mounts.yaml`; restic komt in `deploy/` niet voor. Meilisearch is sinds 22 oktober mee (#1966) en herstel wordt geoefend (#2122) |
| Structured logging met structlog, Alloy, VictoriaLogs (30 dagen) en Grafana | maart | Eén `request_id` toont de hele keten | Geldt (`deploy/docker-compose.yml:1332,1356`) |
| Een extra Grafana-plug-in voor VictoriaLogs, omdat LogsQL en LogQL niet samenwerken | maart | De generieke Loki-bron werkt niet | Geldt; de provisioning staat in `deploy/grafana/provisioning/datasources/` |
| Patches en minors mergen automatisch na groene checks; majors en Docker-images door een mens | 14 augustus (#912) | Twee image-bumps (crawl4ai 0.9.x en LibreChat 0.8.7) hadden problemen gegeven; vergrendelingsbestanden mergen wel automatisch omdat vereiste review ze 28 weken liet verouderen | Vervangen op 29 oktober (#2139): data stores en majors mergen ook automatisch, met snapshot, probe en herstel als de poort |
| Een mislukte uitrol blijft staan tot een mens ingrijpt | tot 28 oktober | Er bestond geen script dat een vorig image, compose-bestand of snapshot terugzet (SPEC-INFRA-AUTO-ROLLBACK-001 §1) | Vervangen: `deploy/scripts/deploy-with-rollback.sh` probeert, houdt het vorige image vast en draait zelf terug (#2089) |

### 4.5 Wat niet is uitgezocht of niet is ingelost

- Het ontwerpsysteem van een partner voor de portal-UI: onbeantwoord gebleven in de code, zie 4.1.
- Cross-organisatorisch delen van kennis: uitgesteld naar een tweede versie en niet gebouwd; het ontwerp staat in `product-gaps-backlog.md`.
- Een tenant-routeringsdienst: nooit gebouwd, zie 4.1.
- Zelf hosten van grote taalmodellen op eigen GPU's: nooit uitgevoerd, zie 4.2. Kleinere modellen (embeddings, reranker, Whisper) draaien wel op gpu-01.

## 5. Wat niet meer bestaat

Elke rij is gecontroleerd op het bestaan van code of bestanden (`rg`, `ls`). Wat nog resten heeft, noemt de rest.

| Wat | Wanneer weg | PR of commit | Wat ervoor in de plaats kwam |
|---|---|---|---|
| De LiteLLM-kennishook (pad A van LibreChat): kennis, templates, kennisgatdetectie en antwoordbeleid in een callback | 21 oktober | #1912 | De partnerroute van portal-api; `chat-system.md` beschrijft het ene pad. In `deploy/litellm/` staat geen kennishookmodule meer, wel nog de callbacks voor het Mistral-sleutelbeheer, de routering en de PII-maskering (`deploy/litellm/config.yaml:385-400`). |
| Klai Focus en research-api | 5 mei (service uit op 23 april) | #368 | Kennisbanken in de portal. De map `klai-focus` bestaat niet meer; alleen oude auditrapporten onder `reports/`, een paar commentaren, een test die de oude aanroeper weigert (`klai-retrieval-api/tests/test_research_api_caller_rejected.py`), de controle `scripts/check-agent-knowledge.sh` en de changelog noemen research-api; de operatiehandleiding staat in `docs/runbooks/decommission-focus.md` |
| Coolify en alle diensten die erdoor liepen | 25 september | #1665, #1671 | Compose en Caddy op core-01 en een bouwpijplijn voor de website. Alleen commentaar in `deploy/docker-compose.yml`, `deploy/caddy/Caddyfile`, `deploy/volume-mounts.yaml` en `renovate.json5` noemt de oude situatie |
| Evidence-tier scoring, de semantische retrievalpoort en het "gecorroboreerde vertrouwen" | 20 augustus | #1118, #1122 | Het rangschikkingscontract (actief sinds 8 juli, #828) en citatieredding (actief sinds 20 augustus). Rest: `evidence_tier` staat nog als stapnaam in `klai-retrieval-api/retrieval_api/tracing.py:37`, en `klai-retrieval-api/tests/test_retired_retrieval_experiments.py` bewaakt dat de experimenten niet terugkomen |
| De `.moai`-framework en de agency-mappen | 18 augustus | 8f29b6fc9 (754 bestanden, 149252 regels weg) | Niets. Rest: sinds 1 september staan 13 bestanden van vier specificaties opnieuw onder `.moai/specs/` (6ea3a08f3, 6c0b85933, 232f5b9fa, 946e9a454), en `.gitignore` noemt het bestand .moai/status_line.sh en de map .moai/memory/ |
| Het generieke aanmaken van sleutels voor onderdelen van Klai zelf | 24 september tot 28 oktober | #1663, #1735, #2073, #2088 | Beheerde sleutels: de sleutel voor de interne chat maakt alleen provisioning (`klai-portal/backend/app/services/internal_chat_keys.py`) en de Slack-sleutel de Slack-koppeling. De beheerroute voor API-sleutels toont, maakt, wijzigt en verwijdert ze niet (`klai-portal/backend/app/api/admin_api_keys.py:214-222`). De route om een Slack-sleutel te plakken is weg (#2088) |
| De oude inbox voor kennisgaten, `GET /api/app/gaps` en de resolve-route | 11 november | #2321, #2325 | Het scherm met gegroepeerde kennisgaten en voorstellen (`klai-portal/backend/app/api/app_gap_clusters.py:154`). Rest: de samenvatting en de indeling per taxonomie (`klai-portal/backend/app/api/app_gaps.py:149,178`) en de tabel `portal_retrieval_gaps` (`klai-portal/backend/app/models/retrieval_gaps.py:23`) |
| Zelfgehoste cal.com | 24 september | #1621 | Calnode (`deploy/docker-compose.yml:2526`). Reden volgens de PR: cal.com is gesloten broncode geworden en de laatste image was verouderd |
| LibreChat-patch-mounts en de canary | 14 augustus | #931 | De Klai-eigen image met patches als broncode-diff (#947) |
| Vexa 0.10 | 14 augustus | #914, #938 | Vexa 0.12 (`deploy/docker-compose.yml:2025`) |
| `whisper-server` in productie | 19 april | ee11dacf5 (SPEC-VEXA-003) | De Whisper-workers van de Vexa-stack op gpu-01. De referentie-image wordt nog gebouwd voor zelfhosters (`deploy/VERSIONS.md:11`) |
| Het oude bearer-pad van de portal en de oude SPA-app in Zitadel | 19 april | e2199a04e, 553bc9fd5 | De BFF-cookiesessie (`klai-portal/backend/app/api/auth_bff.py`) |
| Domeinlijst voor SSO (`portal_org_allowed_domains`) | 4 mei | #259 | Werkruimte-ontdekking op het domein van de oprichter (`klai-portal/backend/app/models/portal.py:116`). Rest: een commentaarregel in `deprovisioning_steps.py` |
| De ladder van vier abonnementen, het `viewer`-accounttype en de oude seatflow | 12 mei en 6 juni | #575, #611, #621, 5c23b0d04 | Twee abonnementen met het accounttype afgeleid van het profiel |
| Een JWT per service tussen LiteLLM en retrieval-api | 7 juni | #804 | Het interne geheim |
| `klai-pipeline` en `klai-embeddings` als LiteLLM-aliassen | 5 mei | #321 | Directe modelaliassen; alleen commentaar in `deploy/docker-compose.yml:1644` noemt ze |
| `save_to_docs` in de MCP-server | 26 juni | 0af41f298 | Expliciete Klai Docs-tools; een test dwingt af dat de oude tool ontbreekt (`klai-knowledge-mcp/tests/test_update_docs_page.py:723`) |
| vmauth met Zitadel-OIDC voor VictoriaLogs | 5 mei | f2c24c309 | Eenvoudige toegang met basisauthenticatie; vmauth staat niet meer in het compose-bestand |
| De ontwikkelomgeving (parallelle stack) | 17 augustus voor de route; de backend was vier maanden eerder verdwenen | #1017 | Niets in productie; lokale ontwikkeling staat in `GETTING_STARTED.md` |
| Linkexpansie in retrieval | 23 oktober | #2009 | Niets; de graafzoekweg bleef (zoekopdracht op `klai-retrieval-api/retrieval_api` vindt geen linkexpansie) |
| De oude citatiemodus `links` en het herschrijven van de zoekvraag na de controle | 13 oktober | #1856, #1853 | `widget_turn()` en `internal_turn()`. Zie het chatlogboek |
| De tweede laag voor inloghosts in de crawlerwizard (gebouwd op 28 oktober in #2093) | 29 oktober | #2124 | Alleen sitecookies |
| De klantspecifieke evaluatiesets in de publieke repository | 23 oktober | #2028 | Een privé-locatie |
| De widgetinstellingen `booking_url`, `contact_email`, `contact_phone`, `off_topic_subjects` en `off_topic_reply` | 11 november | #2324 | `contact_routes` per soort vraag |
| Feitenranden van verwijderde graafepisodes en verouderde extracties | 12 november | #2339 | Eén huidige extractie per document |

## 6. Wat dit document niet is

- Het beschrijft niet wat er nu draait. Dat staat in `platform.md`, `chat-system.md`, `knowledge-ingest-flow.md` en `knowledge-retrieval-flow.md`.
- Het herhaalt de metingen van de chat en de kennisbank niet; die staan met bron in hun eigen logboeken, en hier staat één regel met een verwijzing.
- Het bevat geen serverinventaris, hostnamen, adressen of leveranciersaccounts. Rolnamen als core-01, gpu-01 en public-01 staan er omdat de repository ze zelf gebruikt.
- Het is geen volledige lijst van alle commits en geen onderzoeksverslag. Het compatibiliteitsonderzoek van 3 maart en de modelvergelijking zijn hier tot besluiten gecondenseerd; de volledige tekst staat in de gitgeschiedenis van `platform.md` en het onderzoek zelf onder `docs/research/`.
