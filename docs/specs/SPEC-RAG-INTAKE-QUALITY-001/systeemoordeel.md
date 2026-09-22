# RAG-keten: systeemoordeel en verbeterwerkwijze

Stand: 22 september 2026. Dit document verbindt de startopdracht, het
[meetlog](evolutie.md) en de [intake-eval](spec.md). Het is geen algemene
nauwkeurigheidsscore en geen verklaring dat alle onderzoeksvragen zijn afgerond.

## Oordeel over de totale keten

De keten bevat gemeten deelverbeteringen, maar de eerdere conclusie over de
bevraging was te positief. De eerste-vraagherformuleringen werden in de echte
browserwidget overgeslagen, omdat de test en de deployproef een andere
berichtgeschiedenis gebruikten; antwoordlog §2.41 legt de reparatie vast. Ook bewezen de antwoordbeoordelingen niet dat noodzakelijke
verduidelijking behouden bleef. Zowel herstelde broninhoud als een vierde gevonden
bron leverde in de volledige proeven geen herhaalbaar betere antwoorden op.
De concrete prijsreparatie uit §18 staat live en behoudt alle achttien gemeten
bedragen. De groeperingsproef uit §19 valt af: betere vindbaarheid leidde ook tot
extra onbewezen details. Een volgende ingreep vereist een aangetoonde fout op
een concrete antwoordroute, met een controle die zo'n fout ook werkelijk afwijst.

| Onderdeel | Bewijs | Oordeel en grens |
|---|---|---|
| Bevraging | Antwoordlog §2.29–2.34: herformuleringen winnen twee antwoordrondes. De controle van 22 september toonde dat de browsergeschiedenis de functie oversloeg; §2.41 repareert dat. | De gemeten winst is nog geen bewijs van werking voor echte widgetgebruikers; dat volgt uit de nameting op echt verkeer. Zie de correctie hieronder. |
| Antwoordcontrole | §18: 18/18 bedragen behouden na reparatie. §19: de letterlijke controle blokkeert een onterecht positief modeloordeel. | Tekstbehoud bewezen; modeloordelen zijn geen zelfstandige grondwaarheid. Betere zoekcijfers kunnen slechtere antwoorden opleveren. |
| Gap detection | Intake-meetlog §5: scores missen 15/29 missing/incomplete-bevindingen; 5/34 covered krijgt een signaal. §10: vijf onbeoordeelbare detectorproeven. | Geschikt voor onderzoek door een inhoudseigenaar; nauwkeurigheid zonder menselijke referenties onbekend. |
| Bronintake | §3 en §7: linktekstverlies vóór chunking. §11: 18 linklabels hersteld, maar antwoordvoorkeur 49–47 met tegengestelde rondes. | Herstel is gemeten en afgewezen voor uitrol; bronbehoud alleen garandeert geen betere antwoorden. |
| Chunking en context | §4 en §7: 30 prefixen zonder duidelijke tegenspraak; acht gekozen antwoordspans passen in één child, vijf bereiken de antwoordcontext. | Geen bewijs voor een nieuwe chunker. Langere antwoorden, taxonomie en historische samenvattingen zijn niet afdoende beoordeeld. |
| HyPE | §9: 35 voorkeuren voor aan, 29 voor uit, acht gelijk; richting wisselt per ronde. §12–13: twee foutieve navigatiechunks bereiken in de gekozen diagnoses niet de eerste twintig kandidaten. | Geen overtuigende winnaar en geen aangetoonde antwoordschade van deze twee fouten. Hiervoor nu geen nieuwe prompt bouwen; inschakeling per contenttype blijft onbeoordeeld. |
| Bronselectie | §14: vierde bron zonder stabiele antwoordwinst. §19: groepering vindt de gemiste prijsregel, maar voegt drie antwoorden met een onbewezen valuta toe. | Beide kandidaten afgewezen voor uitrol. Meer gevonden tekst is hier geen aantoonbare antwoordwinst. |

De vragenjudge bleek procedurevragen bij linklijsten te gemakkelijk goed te keuren
(§4). Modeloordeel alleen is dus geen referentiewaarheid. Leg echte vragen,
letterlijke bronpassages en verwachte inhoud vooraf vast; onderscheid controles
door een agent van labels door een menselijke inhoudseigenaar.

## Correctie op eerdere oplevering: 22 september

Deze problemen waren al onderzocht. De tekortkoming zit in de vertaling naar
productgedrag en de controle daarop, niet in ontbrekende onderzoeksdocumentatie.

- **Herformuleringen, PR #1548:** `first_question_variants` sluit iedere historie
  met een assistantbericht uit. De widget voegt sinds mei een assistantwelkomstbericht
  toe. Zowel `test_query_paraphrase.py` als `simulate_conversations.py` begonnen
  daarentegen met alleen een gebruikersbericht. Antwoordlog §2.34 noemde deze
  rechtstreekse API-proef de live widget. De logs bewezen uitvoering voor die
  vereenvoudigde invoer, niet voor het browserpad. De helper van vóór de reparatie
  reproduceerde het verschil: welkomstbericht plus vraag gaf nul varianten; alleen
  de vraag gaf er twee. De functie was uitgerold maar bereikte de browserroute niet.
  Antwoordlog §2.41 legt de reparatie vast: "eerste vraag" telt nu de berichten van
  de bezoeker, en test en harnas sturen de browservorm.
- **Doorvragen, PR #1486:** de sturende instructie is bewust verwijderd op basis
  van algemene modelvoorkeur (§2.4). De vervangende test levert zelf al een
  verduidelijkingsvraag als modelantwoord aan. Hij bewijst niet dat een echte
  onduidelijke hulpvraag nog tot doorvragen leidt. Een menselijke referentieset
  voor noodzakelijke diagnose ontbrak als behoudseis. Alleen deze instructie
  herstellen volstaat niet: ook de classificatie van onduidelijkheid kan falen.
- **Onterechte commerciële afwijzing:** §2.18 beschrijft dezelfde foutklasse en
  vermeldt expliciet dat de drie onderzochte oplossingen afvielen. Dat probleem
  is dus nooit opgelost. Relevante gevonden bronnen kunnen nog steeds worden
  vervangen door de vaste afwijstekst na de onderwerpclassificatie.
- **Beoordeling:** brongetrouwheid en het oplossen van de bedoelde hulpvraag zijn
  afzonderlijke eisen. §2.24 en §2.25 documenteerden al beperkingen van de
  beoordelaars. Een onderbouwde procedure kan de verkeerde handeling adviseren;
  verwijderen van onbewezen tekst bewijst evenmin dat een bruikbaar antwoord
  overblijft. Succesclaims moeten beide eisen en het werkelijke browserpad dekken.

De eerstvolgende prioriteit is het herstellen en toetsen van deze bestaande
gedragsafspraken, vóór nieuwe intakevarianten. Historische meetresultaten blijven
staan met hun oorspronkelijke bereik; zij gelden niet als bewijs dat deze
problemen in productie zijn opgelost. Deze correctie is nog geen productfix.

## Prioriteit en open onderzoek

1. **Bronwaarheid en beoordelingscontrole:** het prijsverlies is hersteld en live;
   de JSON-groepering is afgewezen. De volledige herbouw vervalt voor die kandidaat.
   Een afgeleid graaffeit kan dezelfde onbewezen aanvulling bevatten als het
   antwoord en mag die niet als onafhankelijk bewijs bevestigen. Bronreferenties
   blijven vooraf onafhankelijk gecontroleerd. Inhoudseigenaren
   kunnen via de bestaande reviewvelden vastleggen welke antwoorden en gaps
   werkelijk kloppen; agentreferenties vervangen die menselijke labels niet.
2. **Gerichte bronselectie en vraaggeneratie:** pas een ingreep meten wanneer echte
   vragen een concrete fout in vraagvectoren of bronselectie aantonen. De huidige twee
   navigatiefouten rechtvaardigen na §12–13 geen implementatie; de algemene vierde
   bron valt af in §14. Een nieuwe proef vereist een specifieke bronselectiefout.
3. **Context en meetbaarheid:** gebruikte samenvattingen via bestaande opslag
   bewaren wanneer dit nodig blijkt voor een concrete proef. Historische
   samenvattingen zijn niet terug te halen door nieuwe te genereren.
4. **Contentprofielen:** werkelijke uploadtypen en vraagvectoren toetsen met
   vooraf vastgelegde vragen per type; geen algemene inschakeling op basis van
   een supportartikelproef.
5. **Gap detection en echt verkeer:** onafhankelijke referenties verzamelen,
   detectie opnieuw meten en zoek-/late-controle-events op echt verkeer volgen.
   Bestaande clustering, review en publicatie gebruiken voor de inhoudslus.

Selectieve bronpassages zijn geen representatieve succespercentages voor hele gesprekken.

## Werkwijze per verbeterslag

De opdrachtgever heeft autonome uitvoering, afzonderlijke Sol-agents, onderzoek
met echte tenantdata en directe uitrol van aantoonbare verbeteringen toegestaan.
Werk in kleine wijzigingen; de eerder goedgekeurde totale omvang is klasse L.

1. **Extern onderzoek eerst.** Gebruik primaire bronnen: documentatie, geïnstalleerde
   broncode en oorspronkelijke onderzoeken. Noteer URL, datum, relevante versie,
   bewering en de grens van overdraagbaarheid naar deze keten.
2. **Toets lokaal.** Verifieer die bewering in code en echte data. Controleer
   bestaande helpers, configuratie, meetmiddelen en recente wijzigingen op main.
   Baken één oorzaak, één ingreep en de geraakte paden af.
3. **Leg de proef vooraf vast.** Bewaar de selectie van echte vragen, bronversies,
   model-/imageversies, oude en nieuwe variant, beoordelingsrubriek, volgordezaad,
   uitsluitingsregels en winst-/stopcriteria vóór de nameting. Ruwe data blijft privé.
4. **Nulmeting en wijziging.** Hergebruik een passende nulmeting alleen als code,
   brondata en modelconfiguratie vergelijkbaar zijn; anders opnieuw meten. Schrijf
   bij een fout eerst één representatieve falende regressietest. Bouw minimaal.
5. **Nameting.** Volg onverkort antwoordlog §6: dezelfde echte vragen en historie,
   drie antwoordpogingen, minstens twee blinde willekeurige beoordelingsrondes,
   richting in beide rondes gelijk en een verschil boven de afgesproken ruisgrens.
   Meet ondersteunde antwoorden, onbewezen details, latency en kosten naast de
   primaire uitkomst. Een eerder ondersteund antwoord mag niet verdwijnen.
6. **Besluit.** Geen aantoonbare winst of een verslechterde veiligheidsgrens:
   niet uitrollen, oorzaak en resultaat vastleggen. Wel winst: volledige relevante
   controles, onafhankelijke review, kleine PR, groene CI, direct uitrollen en
   draaiende versie, gezondheid en echte werking verifiëren. Geen uitgeschakelde
   productvlag als eindresultaat. Een noodzakelijke dataverversing hoort bij de uitrol.
7. **Leren.** Voeg per proef datum, bronnen, hypothese, nulmeting, resultaten per
   ronde, beperkingen, besluit, commit/PR en uitrolbewijs toe aan het meetlog.
   Actualiseer prioriteiten; herhaal geen afgewezen proef zonder nieuw bewijs.

Sol-agents krijgen afzonderlijke afgebakende opdrachten; de hoofdagent bewaakt
de meetafspraak, gegevensgrenzen, integratie en uitrol. Eén agent beheert de
gedeelde snelheid van herspelingen en controleert zowel aanvragen als tokens
per minuut. §19 gebruikte minimaal dertig seconden tussen starts, in eigen
containers. Productiegegevens worden alleen gelezen; wijzigingen aan
de klantketen volgen pas na de meetpoort. Geen nieuwe provider of dependency
zonder akkoord. Blogconcepten blijven ongepubliceerd.

## Verwijzingen uit de startopdracht

Paden hieronder zijn relatief aan de repositoryroot. Dit is een bronregister,
geen claim dat elk genoemd onderdeel volledig gevalideerd is. De startopdracht
bevatte verkorte of verplaatste paden; hier staan de aangetroffen locaties.
Operationele toegangspaden en klantidentiteiten blijven in de private opdracht.

**Bevraging**

- `docs/specs/SPEC-RAG-ANSWER-JUDGES-001/evolutie.md`: §2.1–2.40, open werk §5, meetafspraken §6.
- `docs/specs/SPEC-RAG-ANSWER-JUDGES-001/spec.md`.
- `klai-retrieval-api/retrieval_api/api/retrieve.py`: queryvarianten en zoekbesluiten.
- `deploy/litellm/klai_kb_query_rewrite.py`, `deploy/litellm/klai_knowledge.py`.
- `klai-portal/backend/app/services/partner_chat.py`, `query_paraphrase.py` in dezelfde map.

**Gap detection**

- `docs/architecture/support-gap-detection.md`.
- `docs/specs/SPEC-KNOWLEDGE-ACTIVITY-001/spec.md`, inclusief HISTORY.
- `docs/specs/SPEC-VOYS-HELPBOT-001/spec.md`, REQ-1.
- `klai-portal/backend/app/services/`: `gap_classification.py`, `support_case_analysis.py`, `gap_rescorer.py`, `support_gap_grouping.py`, `support_cases.py`, `support_case_reviews.py`.
- `klai-portal/backend/app/api/app_gaps.py`, `app_activity.py` in dezelfde map.
- `klai-connector/app/services/support_source.py`.
- `klai-portal/backend/scripts/evaluate_support_gaps.py`.
- Inmiddels beschikbaar: `klai-portal/backend/app/services/ingest_gap_evaluation.py` en `klai-portal/backend/scripts/evaluate_ingest_gaps.py`; hergebruiken, niet opnieuw bouwen.

**Intake**

- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`.
- Onder `klai-knowledge-ingest/knowledge_ingest/`: `enrichment_tasks.py`, `enrichment.py`, `contextual.py`, `chunker.py`, `content_profiles.py`, `taxonomy_classifier.py`, `content_labeler.py`, `sparse_embedder.py`, `embedder.py`, `config.py`, `enrichment_policy.py`, `fingerprint.py`, `crawl4ai_config.py`.
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py`; ook de connectoradapters onder `klai-connector/app/adapters/`.
- Onder `klai-portal/backend/app/services/`: `docling_client.py`, `kb_upload_poller.py`, `file_upload.py`.
- `klai-retrieval-api/retrieval_api/services/search.py`; `quality_floor.py` en `quality_boost.py` staan direct onder `retrieval_api/`.
- `klai-knowledge-ingest/knowledge_ingest/eval/`: `ragas_runner.py`, `suites/*.yaml`, `generate_voys_seed_queries.py` en de toegevoegde `intake_quality.py`.
- `docs/audit-ingest-pipeline-2026-05-06/findings.md`.
- `docs/research/chunk-type-retrieval-value.md`, `knowledge-deduplication-and-conflict-resolution.md`, `knowledge-pipeline-architecture.md` in dezelfde map.
- `docs/specs/SPEC-KB-JSON-FEED-001/spec.md`.

**Data en meetpunten**

- LibreChat: tenantgebonden Mongo-berichten; leesexport `klai-portal/backend/scripts/export_librechat_messages.py`.
- Widget: `widget_messages.answer_signals`, `widget_conversations`, `answer_reviews`, `portal_retrieval_gaps`, `gap_events`, `portal_support_cases` en reviews.
- Operatorscripts onder `klai-portal/backend/scripts/`: `grounding_report.py`, `simulate_conversations.py`, `evaluate_support_gaps.py`.
- VictoriaLogs: `retrieval_decision_record`, `query_variants_run`, `query_variants_added`, `query_variants_failed`, `answer_grounding_late`; correleer op `request_id`. Redis-retrievallogs zijn kortlevend en geen historisch archief.
- Qdrant: tekst, contextprefix, vragen, koppen, contenttype, taxonomie en kwaliteit; PostgreSQL: `knowledge.artifacts`, met controle op daadwerkelijk opgeslagen `extra.document_summary`.
- Platform → Status → Meetpunten: `klai-portal/backend/app/services/observability_signals.py`.

**Blog, instructies en oplevering**

- `.claude/rules/gtm/klai-brand-voice.md`, `klai-humanizer.md`, `mark-tone-of-voice.md` in dezelfde map; `.claude/agents/gtm/gtm-blog-writer.md`.
- Eigen checkout van `klai-website`: `src/content/blog-nl/`, `src/content/blog-en/`. De actuele frontmatter gebruikt `pubDate`, niet het in de startopdracht genoemde `publishDate`.
- Lokale conceptbranch `draft/rag-intake-quality-20260919`: bevraging → gaten herkennen → intake; mislukkingen benoemen, anonimiseren, geen gedachtestreepjes, niet pushen/publiceren.
- Root- en service-`AGENTS.md`; `make code-tools` en `codebase-memory-mcp cli index_repository --repo-path .` voor codeoriëntatie.
- `scripts/audit-public-tenant-data.py`; ruwe data en klantresultaten buiten deze publieke repository. Geen GitHub-issues muteren.
- Dit document, `spec.md` en `evolutie.md` in deze map; eindrapport in het gesprek met resultaten per as, oorzaken, vervolgprioriteit, blogbranch en niet meetbare onderdelen.
