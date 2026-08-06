# RAG-systemen voor communicatiedata — vergelijkingsrapport & toetsingskader

*Casus: superdock/TalkWithData (Jantine Doornbos) vs. Engram (Mark Vletter) vs. Klai — juli 2026*

> **Positie in de research-set** (toegevoegd aug 2026): dit rapport is het derde communicatie-onderzoeksdocument en levert de laag die de andere twee missen — het **datamodel** (conversation/message/participant, canonical entity layer, ontology-registry) en het elf-dimensies-toetsingskader.
> - `communicatie-als-kennisbron.md` — het basisdocument: extractie-aanpak per communicatietype + de onafhankelijk getoetste Cerebras-casestudy. De daar bevestigde verdicten (thread als indexeer-eenheid; expertise-features vereisen canonieke personen) onderbouwen D2/D3 hieronder.
> - `knowledge-pipeline-architecture.md` — §1–5: helpdesk-extractieschema; §5.5: de volledige telefonie-keten (ASR, aggregatie, evaluatie). De structure-first-route uit §7–8 hieronder gaat vooráf aan die pipelines: eerst het conversatiemodel, dan extractie, dan de bestaande RAG-stack.

## 1. Doel en gebruik

Dit rapport doet twee dingen:

1. Het vergelijkt superdock, Engram en Klai op hoe ze **communicatiekanalen (Slack, e-mail, Teams, …) structureren en er bruikbare informatie uit halen**.
2. Het definieert een **generiek toetsingskader van elf dimensies** waarmee je elk volgend RAG-systeem op dezelfde manier kunt beoordelen. Per dimensie staat: waarom het ertoe doet, welke vragen je stelt, hoe de systemen scoren, en wat de best practice is.

De rode draad uit deze casus: **structureren en extraheren zijn twee verschillende disciplines**, en vrijwel geen enkel systeem is in beide goed. Engram investeert in structuur (threads, personen, relaties) en laat extractie liggen; superdock investeert in extractie en retrieval en gooit structuur weg; Klai is production-grade op document-RAG, maar heeft voor communicatiedata nog geen first-class thread/person-laag. Gebruik het kader hieronder om te zien waar een systeem zijn geld op inzet.

Het aanvullende literatuuronderzoek scherpt dit aan: voor de volgende stap is "GraphRAG" niet precies genoeg. De relevante categorie is **entity-resolved hybrid RAG**: vector retrieval, graph traversal en relationele golden-record governance rond dezelfde canonieke entiteiten.

## 2. Systeemprofielen in het kort

| | **superdock (TalkWithData)** | **Engram** | **Klai** |
|---|---|---|---|
| Type | Multi-tenant SaaS: kennis injecteren in AI-tools via Chrome-extensie | Persoonlijk kennisarchief (single-user) | Open-source, self-hostable multi-tenant AI-platform: chat + kennisbank + partner/widget/MCP-integraties |
| Stack | Laravel + Vue (dashboard), Python FastAPI (RAG), Chrome MV3 | FastAPI + Jinja2/htmx, SQLite, markdown-vault (Obsidian) | FastAPI + React portal, aparte `klai-connector`, `klai-knowledge-ingest`, `klai-retrieval-api`, LiteLLM-hook |
| Opslag | ChromaDB (vectors) + PostgreSQL (metadata) + JSON-graaf | SQLite (incl. embeddings als BLOBs) + markdown datalake | PostgreSQL/RLS + Qdrant named dense/sparse vectors + FalkorDB/Graphiti graph + Redis/Grafana/VictoriaLogs |
| Modellen | OpenAI `text-embedding-3-small` + `gpt-4o-mini`, lokale cross-encoder | Lokaal via Ollama: `nomic-embed-text`, qwen3/gemma3 | BGE-M3 dense/sparse via TEI/sidecar, Infinity `bge-reranker-v2-m3`, LiteLLM-routes voor enrichment/query-rewrite/Graphiti |
| Kanalen | ~17 connectors: Slack, Gmail, Outlook, IMAP, Teams, Notion, Confluence, Drive, HubSpot, Salesforce, REST/GraphQL/SQL, websearch | Gmail (MBOX), Calendar, Contacts, LinkedIn | Document/KB-bronnen: GitHub, Notion, web crawler, Google Drive/Docs/Sheets/Slides, Microsoft 365/SharePoint/OneDrive, Airtable, Confluence, URL/text/file upload; HubSpot Help Desk POC gebruikt Klai voor supportmailconcepten |
| Kernbestanden | `python/mcp_connectors.py`, `enrichment.py`, `vectorstore.py`, `evidence_scoring.py`, `relevance_gate.py`, `knowledge_graph.py`, `eval/` | `scripts/extract_gmail_threads.py`, `scripts/gmail/*`, `app/domains/email/`, `app/domains/search/indexing.py` | `klai-connector/app/services/sync_engine.py`, `knowledge_ingest/enrichment_tasks.py`, `qdrant_store.py`, `retrieval_api/api/retrieve.py`, `services/search.py`, `services/evidence_tier.py`, `deploy/litellm/klai_knowledge.py`, `knowledge_ingest/eval/` |

## 3. Het toetsingskader — elf dimensies

Scoreschaal per dimensie: ●●● sterk / ●●○ redelijk / ●○○ zwak / ○○○ afwezig.

---

### D1. Kanaal-ingestie & synchronisatie

**Waarom:** communicatiedata stroomt continu; een RAG-systeem dat alleen snapshots kan laden, veroudert per dag.
**Toets:** Hoe komen berichten binnen (API, export, push)? Is sync incrementeel of full-reindex? Hoe wordt gededupliceerd? Wat gebeurt er bij token-expiry/failures?

- **superdock ●●○** — Breed connectorpalet met uniforme interface (`search(query)` / `get_recent(limit)`), OAuth-refresh via het Laravel-backend. Maar: geen incrementele sync — elke `/reindex` haalt opnieuw "de laatste ~50 items" op, geen expliciete deduplicatie (zelfde bericht in twee collecties = twee chunks), en een gefaalde token-refresh laat de connector stil falen.
- **Engram ●●○** — Diepe maar smalle ingestie: MBOX-export handmatig, batch-scripts met vaste pipeline-volgorde. Deduplicatie is goed (thread-ID's, dedup-migraties), maar er is geen live API-sync en geen Slack/chat-kanaal.
- **Klai ●●○** — Brede document-ingest met connectorconfig in de portal en execution in `klai-connector`. Sync-runs hebben status, cursor_state, skip-reasons, per-connector locks en portal writeback. Google Drive gebruikt changes-cursors; Microsoft 365 gebruikt deltaLinks; GitHub kan tree-SHA skippen; Notion/Confluence/Airtable blijven grotendeels full-scan/reconcile. Voor communicatiekanalen is Klai nog smal: geen Gmail/Slack/Teams-thread-ingest als kennisbron; de HubSpot Help Desk POC leest ticketcontext en bewaart partner-support-sessies, maar indexeert geen volledige communicatiedraad als first-class KB-object.
- **Best practice:** breed én incrementeel: per bron een cursor/`history_id` bijhouden, alleen delta's ophalen, dedup op bericht-ID vóór indexeren.

### D2. Structurering: het conversatiemodel

**Waarom:** de betekenis van een bericht zit in zijn thread — antwoord-op-wat, wie deed mee, hoe liep het af. Wie dit weggooit kan het nooit meer terugbouwen.
**Toets:** Is er een first-class thread/conversatie-entiteit? Overleven `thread_ts` (Slack) en `In-Reply-To`/`X-GM-THRID` (e-mail) de ingestie? Zijn afzonderlijke berichten binnen een thread adresseerbaar?

- **superdock ○○○** — Bewust plat: elk bericht wordt `{title, content, metadata}`. De Slack-connector (20 regels, `mcp_connectors.py:1181`) pakt alleen kanaalnaam + tekst + gebruiker + timestamp; `thread_ts` wordt niet gevolgd. Gmail wordt per bericht één string `"From/To/Date/Subject + body"` (afgekapt op 3000 tekens). Threads bestaan niet als entiteit.
- **Engram ●●●** — Thread-first: groepering op `X-GM-THRID`, `email_threads`-tabel met eerste/laatste datum, message count, onderwerp genormaliseerd (Re:/Fwd: gestript), thread-markdown in het datalake, bidirectionaliteitsdetectie.
- **Klai ●○○** — Niet plat op superdock-niveau, maar ook niet thread-first voor communicatiedata. `PartnerSupportSession`/`PartnerSupportMessage`, widget-conversations en platform-message-threads bestaan voor productflows/audit, maar de kennis-ingest blijft document/chunk-first. `IngestRequest.extra` kan `participants` dragen en connector `DocumentRef` heeft `sender_email`/`mentioned_emails`, maar er is geen generiek `conversation → messages → participants` model voor Slack/e-mail/Teams dat door retrieval gebruikt wordt.
- **Best practice:** een genormaliseerd conversatiemodel over kanalen heen (conversation → messages → participants), waarbij kanaalspecifieke ID's bewaard blijven als bron-referentie.

### D3. Identiteit: berichten koppelen aan personen

**Waarom:** "wat weet ik over/via persoon X" is dé kernvraag bij communicatiedata; dat vereist identity resolution over adressen en handles heen.
**Toets:** Worden afzenders/ontvangers gekoppeld aan een personen-entiteit? Werkt dat over kanalen heen (zelfde mens op Slack én mail)? Worden relatiestatistieken bijgehouden?

- **superdock ●○○** — Alleen wat de LLM tijdens graafbouw als entiteit "person" herkent in tekst; geen deelnemer-administratie, geen koppeling van accounts aan personen.
- **Engram ●●●** — `email_thread_participants` met `person_id`-koppeling via e-mailmatch (incl. legacy-formaten), sent/received-tellingen, first/last contact per persoon, verrijking van people-profielen, wikilinks in interactie-bestanden.
- **Klai ●○○** — Sterk in tenant/user-scope, zwak in menselijke identiteit. Sommige connectoren capteren auteur/collaborator-e-mails (`sender_email`, `mentioned_emails`), maar `DocumentRef` documenteert expliciet: geen normalisatie, geen plus-tag stripping, geen role-mailbox denylist, entity resolution out of scope. Graphiti extraheert entiteiten uit tekst en schrijft `entity_names`/PageRank naar Qdrant, maar dat is geen betrouwbare personenadministratie over kanalen heen.
- **Best practice:** wat Engram doet, plus cross-channel identity (Slack-handle ↔ e-mailadres) en confidence-niveaus bij matching.

### D4. Signaal/ruis-filtering bij import

**Waarom:** 90% van communicatie is ruis (nieuwsbrieven, noreply, notificaties); wie alles indexeert, vervuilt zijn retrieval blijvend.
**Toets:** Wordt er gefilterd of gescoord vóór indexeren? Op welke regels? Is de drempel instelbaar en uitlegbaar?

- **superdock ●○○** — Geen import-filtering op berichten; wel een **relevance gate bij query-tijd** (`relevance_gate.py`): cosine-similarity tussen query en collectie-centroid, onder 0.25 geen injectie. Dat filtert ruis aan de uitgang, niet aan de ingang.
- **Engram ●●●** — `knowledge_score` 0–15 plus vijf sequentiële filterregels (noreply, nieuwsbrieven, korte body, geblokkeerde adressen) vóór opslag.
- **Klai ●●○** — Veel productieguards, maar niet communicatie-specifiek. Ingest weigert te korte docs (<50 chars), houdt `skip_reasons` bij, detecteert login-walls/auth-walls, dirty crawler-content en near-duplicates, en retrieval heeft quality-floor filtering plus een retrieval gate in shadow-mode. Voor e-mail/Slack-ruis ontbreken nog Engram-achtige regels zoals noreply/newsletter/notification scoring vóór opslag.
- **Best practice:** beide: scoren bij import (goedkoop, houdt de index schoon) én gates bij retrieval (vangt wat er toch doorglipt).

### D5. Chunking

**Waarom:** de chunk is de retrieval-eenheid; verkeerde grenzen betekent halve antwoorden.
**Toets:** Is chunking content-type-bewust? Worden natuurlijke grenzen (artikel, kop, bericht) gerespecteerd? Overlap?

- **superdock ●●●** — `chunking.py`: per content-type een strategie — regelgeving op artikelgrenzen (nooit mid-artikel), gestructureerde docs op koppen, tekst met ~1200 tekens en 15% overlap, beeldbeschrijvingen intact.
- **Engram ●○○** — Splitst knowledge-content op `## `-koppen; e-mail-bodies worden helemaal niet gechunkt of ge-embed — alleen een geformatteerde kopregel (+ `ai_summary`, indien aanwezig) gaat de index in.
- **Klai ●●●** — Markdown-aware chunking op koppen met code-block bescherming, child chunks van ~1200 chars met overlap en parent chunks van ~6000 chars zonder overlap (`chunk_markdown_with_parents`). Retrieval matcht op kleine children en vervangt de response door parent-text. Dit is uitstekend voor documenten; voor conversaties moet dezelfde techniek worden aangepast naar message/exchange-boundaries.
- **Best practice:** voor conversaties: chunk op berichten- of exchange-grens met thread-context in de metadata, nooit blind op tekenaantal.

### D6. Verrijking bij indexeren (de extractielaag)

**Waarom:** dit is waar "berichten opslaan" verandert in "informatie eruit halen". Ruwe berichttekst matcht slecht met hoe mensen later vragen stellen.
**Toets:** Wordt content vóór het embedden verrijkt (samenvatting, context, hypothetische vragen)? Worden entiteiten/relaties geëxtraheerd? Draait dit automatisch in de pipeline?

- **superdock ●●●** — Het paradepaardje. Twee parallelle stappen bij `/reindex`:
  1. **HyPE** (`enrichment.py`): per chunk genereert een LLM een context-prefix (1–2 zinnen, vóór de chunk geplakt vóór embedding → rijkere vector) en 3–5 hypothetische vragen (mee in de BM25-index → lexicale match ook bij andere woordkeus). Kostenbeheersing: max 50 chunks per document, 8 parallelle workers, chunks < 50 tekens overgeslagen.
  2. **Knowledge graph** (`knowledge_graph.py`): LLM extraheert entiteiten (person/organization/regulation/concept/…) en relaties als triples naar een JSON-graaf per bot; gebruikt bij retrieval (zie D7).
- **Engram ○○○** — De velden bestaan (`ai_summary`, `ai_analyzed`, `ai_model`, `ai_tags`, `ai_expertise`) maar **geen enkele code vult ze**. De extractielaag is ontworpen, niet gebouwd. Gevolg: e-mailsearch werkt alleen op onderwerp/deelnemers.
- **Klai ●●●** — Zeer sterk: per artifact eerst document-summary (Anthropic contextual retrieval pattern), daarna per chunk `context_prefix` + 3–5 HyPE-vragen via LiteLLM; enriched text voedt zowel dense als sparse embeddings; vragen krijgen een aparte `vector_questions`; Graphiti/FalkorDB extraheert entities/edges en schrijft `entity_uuids`, `entity_names` en PageRank naar Qdrant. Kritische nuance: dit is document- en chunk-enrichment, geen communicatiespecifieke extractie van afspraken, besluiten, commitments, open loops of relatiestatus.
- **Best practice:** HyPE-achtige verrijking is de hoogste ROI-ingreep in dit hele kader en werkt met kleine lokale modellen.

### D7. Retrieval-kwaliteit

**Waarom:** hier wordt de opgeslagen informatie "bruikbaar" — of niet.
**Toets:** Hybride (dense + sparse)? Rank fusion? Reranking? Wordt bron-betrouwbaarheid en leeftijd meegewogen? Entity-awareness?

- **superdock ●●●** — Vijftraps pipeline: (1) dense search in ChromaDB (top-k×3), (2) BM25 over chunk+HyPE-vragen (top-k×3), (3) Reciprocal Rank Fusion, (4) cross-encoder reranking (`ms-marco-TinyBERT`, lokaal, top-30 → top-8), (5) **evidence scoring** (`evidence_scoring.py`): `sigmoid(rerank) × content-gewicht × temporal decay`. Content-gewichten: regelgeving 1.00 > gestructureerd 0.95 > tekst 0.90 > beeldbeschrijving 0.80 > MCP/communicatie 0.75 > web 0.65. Decay in vijf leeftijdsklassen: <30d 1.00 → >365d 0.80. Plus PageRank-boost voor chunks gelinkt aan entiteiten uit de query.
- **Engram ●●○** — Vector-similarity (numpy brute-force, prima op ~13,5k chunks, <50ms) met keyword-boosts (titel +0.15, content +0.05) en dedup op bron. Degelijk, maar geen BM25, geen fusion, geen reranking, geen leeftijds- of bronweging — en de people-graaf die er al ís wordt niet gebruikt voor ranking.
- **Klai ●●●** — Sterker dan superdock op de document-RAG-stack: Qdrant named vectors met RRF over `vector_chunk`, `vector_questions` en `vector_sparse`; bij query-rewrite worden raw-query dense/sparse legs toegevoegd om exacte termen te redden; Graphiti-search loopt parallel en wordt via RRF gemerged; Infinity cross-encoder rerankt; source-aware selection, quality boost/floor, link expansion, PageRank/evidence-tier en confidence bands zitten in de pipeline. Evidence-tier met content-type weights, assertion weights, temporal decay en U-shape ordering draait standaard in shadow-mode tenzij geactiveerd.
- **Best practice:** de superdock-stack; voor communicatiedata is **temporal decay vrijwel verplicht** en zijn BM25 + RRF de goedkoopste eerste stap.

### D8. Generatie & grounding

**Waarom:** het antwoord is het product; hallucinatie of gelekte RAG-scaffolding ondermijnt vertrouwen.
**Toets:** Antwoordt het systeem alleen op basis van opgehaalde context? Bronvermelding? Veroudering gemeld? Zegt het "weet ik niet"?

- **superdock ●●●** — Expliciete principes: grounded inference ("geen kennis → I don't have that information"), verplichte bronvermelding, gegradeerde staleness-waarschuwing in het antwoord ("mogelijk verouderd >90 dagen / sterk verouderd >1 jaar — controleer bij de bron"), en eval-cases die controleren dat er géén frasen als "op basis van de beschikbare context" lekken.
- **Engram ●○○** — Chat is een tool-calling assistent voor vaultbeheer; geen e-mail-grounding, geen bronvermeldingsdiscipline, geen staleness-signaal.
- **Klai ●●●** — Sterk antwoordcontract: LiteLLM-hook prependeert KB-context, strict/open mode bepaalt of zonder citable sources geweigerd wordt, deterministic citation rendering voegt bronnen achteraf toe uit metadata/evidence-pack, low-confidence bands injecteren anti-hallucinatie-instructies, en URL/image guards voorkomen verzonnen links. Staleness is wel vooral ranking/metadata (`ingested_at`, `valid_from/until`, temporal decay), niet overal zichtbaar als expliciete eindgebruikerswaarschuwing.
- **Best practice:** grounding + bron + versheid als vast antwoordcontract.

### D9. Privacy, compliance & scoping

**Waarom:** communicatiedata is de gevoeligste data die er is.
**Toets:** Waar draaien de modellen (lokaal/cloud)? Multi-tenant-isolatie? PII-detectie? Auditability?

- **superdock ●●●** (voor zíjn context) — org_id/department-scoping op elke retrieval ("cross-tenant lekkage is een kritieke fout"), drietraps EU AI Act-checks (rule-based + LLM), PII-regexes incl. BSN met elfproef, auditlog per check, EU-hosting. Kanttekening: alle berichtinhoud gaat wél naar OpenAI voor embedding/enrichment.
- **Engram ●●●** (voor zíjn context) — Alles lokaal (Ollama), niets verlaat de machine. Geen tenancy nodig.
- **Klai ●●●** (voor zíjn context) — Sterke multi-tenant discipline: Postgres RLS, Qdrant `org_id` tenant-index, org/user/kb filters in retrieval, service-auth scopes, identity assertion tussen portal/retrieval/MCP, encrypted connector credentials, tenant telemetry-levels (`off`/`shadow`/`full`) en bounded query logging. Kanttekening: modelprivacy hangt af van deployment en LiteLLM-routes; Klai kan self-hosted/lokaal draaien, maar de code garandeert niet Engram-achtige "niets verlaat de machine" semantiek.
- **Toets voor Klai en elk volgend systeem:** vooral: *welke data verlaat de omgeving richting welke modelprovider, en is dat een keuze of een aanname?*

### D10. Evaluatie & observability

**Waarom:** zonder meetlat is elke retrieval-verbetering giswerk.
**Toets:** Is er een geautomatiseerde eval-suite? Regressietests op antwoordkwaliteit? Ingestie-metrics en logs?

- **superdock ●●●** — `eval/cases.yaml` + `run_eval.py`: declaratieve cases met `must_contain`, `must_not_contain`, `must_refuse`, `must_use_mcp`, lengte-grenzen; markdown/JSON-rapporten voor CI. Daarnaast ingestie-metrics per run (chunks, embedding-failures, graaf-entiteiten, metadata-compleetheid) als JSONL-logs.
- **Engram ●○○** — pytest voor code, maar geen kwaliteitsevaluatie van search/chat-output.
- **Klai ●●●** — RAGAS-harness met YAML suites (`chat.yaml`, `knowledge_org.yaml`), `reference_answer`, `expected_chunks` canaries, per-query metrics naar `knowledge.rag_eval_results`, Grafana dashboards/alerts en nightly Procrastinate tasks. Daarnaast veel unit/integration tests rond retrieval, citations, scope filters, sparse parity, parent-child chunking, Graphiti, low-confidence injection en connector sync.
- **Best practice:** een eval-suite is klein om te bouwen en verandert tuning van gevoel naar meting; bouw hem vóór je retrieval-verbeteringen doorvoert.

### D11. Beheer & schaalbaarheid

**Toets:** Herindexeer-kosten? Vector-opslag die meegroeit? Wat is het bekende breekpunt?

- **superdock ●●○** — ChromaDB schaalt; maar full-reindex per sync is duur bij groei, en de JSON-graaf wordt erkend als knelpunt (>10k entiteiten; upgrade-pad naar FalkorDB benoemd).
- **Engram ●●○** — Brute-force numpy-KNN is elegant simpel; breekpunt rond ~100k chunks. SQLite + markdown is robuust en versioneerbaar.
- **Klai ●●○** — Schaalpad is professioneel: Qdrant, Postgres, FalkorDB, Procrastinate queues, rebuild/backfill-taken, connector reconciliation en Grafana/VictoriaLogs. De keerzijde is operationele complexiteit: Graphiti coverage kan partieel zijn, evidence-tier staat deels shadow-gated, sommige connectoren doen full-scan/reconcile, en de fail-open filosofie bewaart beschikbaarheid maar kan kwaliteitsdegradatie maskeren als je observability niet actief leest.

## 4. Scorecard (invulbaar voor het volgende systeem)

| Dimensie | superdock | Engram | Klai |
|---|---|---|---|
| D1 Ingestie & sync | ●●○ | ●●○ | ●●○ |
| D2 Conversatiemodel | ○○○ | ●●● | ●○○ |
| D3 Identiteit/personen | ●○○ | ●●● | ●○○ |
| D4 Signaal/ruis-filtering | ●○○ | ●●● | ●●○ |
| D5 Chunking | ●●● | ●○○ | ●●● |
| D6 Verrijking/extractie | ●●● | ○○○ | ●●● |
| D7 Retrieval | ●●● | ●●○ | ●●● |
| D8 Grounding | ●●● | ●○○ | ●●● |
| D9 Privacy/scoping | ●●● | ●●● | ●●● |
| D10 Evaluatie | ●●● | ●○○ | ●●● |
| D11 Beheer/schaal | ●●○ | ●●○ | ●●○ |

Het patroon: superdock scoort op de **rechterkant van de pipeline** (D5–D8, D10), Engram op de **linkerkant** (D2–D4), Klai op de **production RAG-laag** (D5–D10) maar nog niet op het communicatiemodel (D2–D3). Voor "communicatie informatie maken" is de lat dus niet "bouw Klai's retrieval opnieuw", maar: voeg Engram's conversatie/personen-laag toe vóór Klai's bestaande enrichment/retrieval-stack.

## 5. Literatuur: entity-resolved hybrid RAG

Deze sectie haalt lessen uit onderzoek en praktijkartikelen over RAG-systemen waarin vector search, knowledge graphs en relationele/golden-record lagen worden gecombineerd. De vraag is niet alleen "welke retrieval werkt het best?", maar vooral: **hoe voorkom je dat dezelfde persoon, organisatie, plek, medicatie of gebeurtenis als vijf verschillende dingen in je systeem leeft?**

### 5.1 Bronnen die relevant zijn

- [Microsoft GraphRAG, "From Local to Global"](https://arxiv.org/html/2404.16130) bouwt uit chunks een entiteiten/relaties-graaf, clustert die in communities en maakt community summaries voor globale sensemaking-vragen. Les: graphs helpen vooral bij corpusbrede vragen, thema's en verbanden, niet alleen bij detailvragen.
- [HybridRAG](https://arxiv.org/html/2408.04948) combineert VectorRAG en GraphRAG in financiële call transcripts. De paper laat zien dat gecombineerde context uit vector database en KG beter presteert dan elk afzonderlijk, vooral bij domeinspecifieke termen en complexe documentstructuren.
- [LightRAG](https://arxiv.org/html/2410.05779) gebruikt graph-based text indexing, low-level retrieval voor specifieke entiteiten/relaties en high-level retrieval voor bredere thema's. Belangrijk voor Klai: LightRAG benadrukt incrementele updates, omdat een GraphRAG-index die steeds volledig herbouwd moet worden duur en traag wordt.
- [KAG](https://arxiv.org/html/2409.13731) is het meest relevant voor de eindvisie. Het combineert schema-vrije extractie met schema-gebonden domeinkennis, maakt mutual indexing tussen originele chunks en graph-structuren, en gebruikt logical forms om per vraag te kiezen tussen exact graph retrieval, tekstretrieval, numerieke berekening en semantische redenering.
- [KG2RAG](https://arxiv.org/html/2502.06864) start met vectorhits als seed chunks, breidt daarna via KG-relaties uit, en organiseert context met de graph als skelet. Les: vector retrieval is een goede ingang, maar graph expansion haalt gerelateerde bewijsstukken op die semantisch niet dicht bij de vraag hoeven te liggen.
- [Deg-RAG](https://arxiv.org/html/2510.14271v1) onderzoekt expliciet entity resolution voor LLM-gegenereerde KGs. De kernles: zonder entity resolution degradeert GraphRAG; direct mergen van equivalente entiteiten werkt vaak beter dan alleen synonym-links toevoegen.
- [Neo4j/Senzing over Entity Resolved Knowledge Graphs](https://neo4j.com/blog/developer/entity-resolved-knowledge-graphs/) en [Senzing over GraphRAG](https://senzing.com/knowledge-graphs-graphrag/) maken dezelfde praktische claim: duplicate nodes maken graphs minder bruikbaar en kunnen tot verkeerde downstream-analyses leiden.
- [Zalando over Knowledge Graph + Master Data Management](https://engineering.zalando.com/posts/2021/07/knowledge-graph-master-data-mdm.html) laat zien dat "golden record" vooral een master-data-probleem is: match, merge, cleanse, quality-assure en centraal opslaan volgens een canoniek model. De graph helpt om mappings en domeinconcepten begrijpelijk te maken.
- [Named Entity Resolution in Personal Knowledge Graphs](https://arxiv.org/abs/2307.12173), [EAGER](https://arxiv.org/abs/2101.06126), [Ditto](https://arxiv.org/abs/2004.00584) en de [End-to-End ER survey](https://ar5iv.labs.arxiv.org/html/1905.06397) plaatsen dit in de bredere ER-literatuur: blocking, matching, clustering/merging, provenance en schaalbaarheid zijn aparte stappen, geen bijzaak van vector search.
- [ODKE+ van Apple](https://machinelearning.apple.com/research/odke) en [Ontology Learning and KG Construction for RAG](https://arxiv.org/html/2511.05991v1) zijn belangrijk voor de dynamische eindvisie: open-domain fact extraction kan schaalbaar worden als je ontologische constraints, verificatie, normalisatie en chunk-grounding combineert.

### 5.2 Hoofdlessen uit het onderzoek

1. **Een knowledge graph garandeert geen "1 entiteit".** Een KG legt nodes en edges vast. Als extractie `Martijn`, `M. Aslander`, `Martijn Aslander` en een e-mailadres als losse nodes maakt, is de graph juist slechter geworden. Deg-RAG en Neo4j/Senzing zeggen hetzelfde: entity resolution moet expliciet gebeuren.
2. **Synonym-links zijn niet genoeg.** Alleen `(Martijn)-[:SYNONYM_OF]->(M. Aslander)` houdt redundantie in stand. Deg-RAG vindt dat direct mergen meestal efficiënter is, zolang je provenance en undo/merge history bewaart.
3. **Vector search is geen identity-laag.** Embeddings vinden vergelijkbare dingen, niet noodzakelijk dezelfde dingen. Twee verschillende personen met dezelfde naam kunnen dicht bij elkaar liggen; dezelfde persoon met verschillende contexten kan juist ver uit elkaar liggen.
4. **Relaties worden pas krachtig als nodes canoniek zijn.** PageRank, community detection, multi-hop traversal en "wie weet hier veel van?" worden vervuild als dezelfde echte persoon over meerdere nodes verspreid staat.
5. **Chunks moeten aan de graph gekoppeld blijven.** KAG en KG2RAG zijn hier duidelijk over: graph-facts zonder originele tekst verliezen grounding; chunks zonder graph verliezen relaties. Mutual indexing is de juiste term: chunk -> entities/relations en entity/relation -> supporting chunks.
6. **GraphRAG is vooral sterk bij multi-hop en global sensemaking.** Microsoft GraphRAG en LightRAG zijn nuttig voor vragen als "welke thema's spelen hier?", "welke clusters bestaan er?", "hoe hangen deze dossiers samen?". Voor simpele feitelijke lookup blijft vector/BM25 vaak voldoende.
7. **Domeinen vragen verschillende schema-strengheid.** KAG laat een bruikbare tweedeling zien: schema-free OpenIE voor snelle ontdekking, schema-constrained extraction voor domeinen waar correctheid belangrijk is, zoals medicatie, juridische claims, voedingswaarden of formele procedures.
8. **Een dynamisch systeem heeft een stabiele metalaag nodig.** Je voorkomt "honderden tabellen per tenant" niet door zonder schema te werken, maar door schema's zelf als data te modelleren: entity types, relation types, attribute definitions, extraction policies, validation rules en schema versions.

### 5.3 Wanneer gebruik je wat?

| Laag | Gebruik voor | Niet gebruiken voor |
|---|---|---|
| **Relationele database** | Canonieke entiteiten, golden records, source records, provider-ID's, merge/unmerge, audit, rechten, provenance, confidence, tenant-scope, lifecycle state | Vrije semantische nabijheid of lange ongestructureerde tekst doorzoeken |
| **Vector database** | Semantische retrieval over berichten, transcripts, documenten, samenvattingen, chunk context, hypothetische vragen | Beslissen dat twee records dezelfde entiteit zijn; harde waarheden afdwingen |
| **Knowledge graph** | Relaties, multi-hop vragen, community detection, expert finding, "hoe hangt dit samen?", graph expansion rond vectorhits | Governance van identiteit als er geen entity-resolution laag is |
| **Ontology/schema registry** | Dynamische domeinen: medicatie, voeding, oorlogsgeschiedenis, IA-onderzoek, klantdossiers; definieert welke types, relaties en attributen bestaan | Elk tenant/project meteen vertalen naar fysieke SQL-tabellen |
| **Document/chunk store** | Bewijs en citaties: originele mail, Slack-thread, call transcript, webpagina, PDF, notitie | Canonieke waarheid zonder verwijzing naar bron |

### 5.4 Communicatie naar informatie: vaste structuren eerst

Voor communicatiebronnen is het terecht om met vaste structuren te beginnen. E-mail, Slack, telefoon en meetings hebben stabiele operationele vormen. Die structuur moet je niet door een LLM laten "ontdekken"; die moet je deterministisch vastleggen.

Minimale vaste bronlaag:

- `communication_source`: Gmail, Slack, Teams, HubSpot, telefoon, meeting transcript.
- `conversation`: thread, channel thread, ticket, call, meeting, DM, group chat.
- `message`: stable provider id, timestamp, sender account, body, reply-to, attachments.
- `participant_account`: e-mailadres, Slack user id, telefoonnummer, CRM-contact-id.
- `transcript_segment`: spreker, tijdspanne, tekst, confidence bij STT.
- `source_artifact` / `chunk`: originele tekst en chunking met citatieanker.

Daarboven pas maak je informatie-objecten:

- `person`: canonieke mens achter e-mailadressen, Slack handles en telefoonnummers.
- `organization`, `project`, `place`, `restaurant`, `product`, `concept`.
- `claim`: iets dat beweerd wordt, met bron en confidence.
- `decision`: genomen besluit, met wie/wanneer/waar.
- `action` / `commitment`: afspraak of toezegging, owner, deadline, status.
- `topic`: waar de conversatie over ging.
- `relationship`: wie werkt met wie, wie noemt wat, wie weet waar veel van.

De belangrijke scheiding:

```text
Bronstructuur is vast:
conversation, message, participant_account, transcript_segment

Kennisstructuur is canoniek en uitbreidbaar:
entity, claim, relation, decision, action, topic

Domeinstructuur is dynamisch:
entity_type, relation_type, attribute_definition, ontology_version
```

### 5.5 De "alles naar 1" laag

De literatuur wijst naar een expliciete canonical entity layer:

```text
raw mention -> candidate generation -> matching -> merge decision -> canonical entity
```

Voor communicatie betekent dat:

```text
"Martijn" in Slack
"M. Aslander" in e-mail
LinkedIn-profiel
telefoonnummer
naam in transcript
        -> person_123
```

Voor andere domeinen:

```text
"paracetamol"
"acetaminophen"
"APAP"
merknaam X
        -> medication_456

"E-nummers"
"additieven"
specifieke stofnaam
        -> food_substance_789

"WOII"
"Tweede Wereldoorlog"
"World War II"
        -> historical_event_321
```

Dit is geen graph-feature maar een governance-feature. De graph mag daarna alleen canonical IDs gebruiken. Alle ruwe vermeldingen blijven bewaard als evidence, zodat je kunt terugzoeken waarom een merge is gemaakt.

### 5.6 Dynamische eindvisie zonder honderden tabellen per tenant

Martijns praktijk met honderden tabellen is een sterk signaal: hij probeert domeinstructuur expliciet te maken. Voor Klai is de les niet "maak ook honderden fysieke tabellen", maar "maak domeinstructuur first-class".

Een schaalbaar patroon:

1. **Stabiele relationele kern**
   - `entities`
   - `entity_mentions`
   - `entity_aliases`
   - `entity_external_ids`
   - `relations`
   - `claims`
   - `source_artifacts`
   - `chunks`
   - `merge_decisions`
   - `provenance`

2. **Dynamische schema/ontology-laag**
   - `entity_types`: person, organization, restaurant, medication, nutrient, battle, historical_person.
   - `relation_types`: works_with, mentions, treats, contraindicated_with, located_in, caused_by, part_of.
   - `attribute_definitions`: dosage, calories, birth_date, latitude, confidence, period.
   - `schema_versions`: per domein/project/tenant versieerbaar.
   - `validation_rules`: welke attributen verplicht zijn, welke relaties toegestaan zijn.

3. **Graph-projectie**
   - Nodes en edges worden uit de relationele kern + schema registry geprojecteerd.
   - De graph is geoptimaliseerd voor traversal en reasoning, niet voor audit of master-data governance.

4. **Vector-projectie**
   - Chunks, summaries, questions, entity descriptions en relation descriptions krijgen embeddings.
   - Metadata bevat `entity_ids`, `relation_ids`, `schema_version`, `source_id`.

5. **Promotie naar fysieke tabellen alleen bij echte noodzaak**
   - Een domein krijgt pas eigen SQL-tabellen als er harde transacties, rapportage, validatie, performance of product-UI voor nodig is.
   - Voor onderzoek, persoonlijke IA en veranderlijke domeinen blijft het bij typed entities + attributes + relations.

Deze aanpak volgt de lijn uit KAG en ontology-guided KG/RAG: combineer schema-free ontdekking met schema-constrained extractie zodra een domein volwassen genoeg wordt.

### 5.7 Besliskader voor Klai

Gebruik **vaste structuren** voor communicatie omdat de bronvorm vast is:

- e-mail thread
- Slack thread
- telefoontranscript
- meeting
- persoon/account
- bericht/segment

Gebruik **dynamische structuren** voor kennisdomeinen omdat de wereld open is:

- medicatie
- voeding
- oorlogsgeschiedenis
- IA-onderzoek
- organisaties
- restaurants
- conceptuele modellen

Gebruik **relationeel** om identiteit en bewijs te bewaken:

- dit is dezelfde persoon
- deze merge is handmatig bevestigd
- deze claim komt uit deze bron
- deze tenant mag dit zien
- deze actie is nog open

Gebruik **vector** om de juiste passages binnen te halen:

- waar werd dit ongeveer besproken?
- welke tekst lijkt inhoudelijk relevant?
- welke transcriptstukken passen bij deze vraag?

Gebruik **graph** om van passages naar samenhang te gaan:

- welke personen, projecten, organisaties en concepten hangen samen?
- via welke bronnen is deze conclusie ontstaan?
- welke kennis hoort bij deze mens?
- welke clusters/thema's ontstaan over het hele corpus?

De werknaam voor deze richting: **Canonical Entity RAG** of **Entity-Resolved Hybrid RAG**.

## 6. Checklist: vragen aan elk nieuw RAG-systeem

**Ingestie** — Hoe komt data binnen (push/pull/export)? Incrementeel met cursor per bron? Dedup op bericht-ID? Wat gebeurt er bij auth-failure — luid of stil?

**Structuur** — Bestaat "conversatie" als entiteit? Overleeft thread-informatie (thread_ts, In-Reply-To) de ingestie? Kan ik van een chunk terug naar het oorspronkelijke bericht én zijn thread? Worden afzenders geresolved naar personen, over kanalen heen?

**Extractie** — Wat gebeurt er tussen "bericht binnen" en "vector in de index"? Samenvattingen/context-prefixen? Hypothetische vragen of andere query-augmentatie? Entiteiten en relaties? Met welk model, tegen welke kosten, en gebeurt het automatisch?

**Retrieval** — Dense, sparse, of hybride met fusion? Reranking? Weegt leeftijd mee (temporal decay)? Weegt bron-betrouwbaarheid mee? Wordt off-topic injectie tegengehouden (relevance gate)? Wordt een bestaande entiteiten-/personengraaf benut bij het ranken?

**Antwoord** — Alleen grounded? Bronnen zichtbaar? Veroudering gemeld? Lekt er RAG-jargon?

**Vertrouwen** — Welke data gaat naar welke provider? Is er een eval-suite en draait die in CI? Zijn ingestie-runs geobserveerd (metrics/logs)? Wat is het bekende schaalbreekpunt?

## 7. Belangrijkste lessen uit deze vergelijking

1. **Structuur weggooien is onomkeerbaar; extractie kun je altijd nog toevoegen.** Superdock kan threads nooit meer terugbouwen; Engram kan `ai_summary` volgend weekend alsnog vullen. Bij twijfel: bewaar structuur, stel extractie uit.
2. **Verrijking vóór indexeren (HyPE) is de hoogste ROI-ingreep** — één klein prompt per chunk dicht het gat tussen hoe mensen vragen en hoe berichten geschreven zijn, en werkt met lokale modellen.
3. **Communicatiedata veroudert; de ranking moet dat weten.** Temporal decay + staleness-waarschuwingen zijn samen <200 regels code.
4. **Hybride zoeken is geen luxe** bij namen, projectcodes en jargon — embeddings missen die, BM25 vangt ze, RRF combineert ze.
5. **Een eval-suite hoort vóór de retrieval-verbeteringen te komen**, anders kun je verbetering niet van regressie onderscheiden.
6. **Voor Engram concreet, in volgorde:** (1) enrichment-script dat `ai_summary` + HyPE-vragen vult via Ollama, (2) thread-bodies chunken en embedden, (3) evidence scoring met temporal decay in `UniversalSearchService`, (4) BM25 + RRF naast vector search, (5) eval-suite naar `eval/cases.yaml`-model — als eerste bouwen, dan 1–4 meetbaar invoeren.
7. **Voor Klai concreet:** niet beginnen met retrieval verbeteren. Klai heeft die laag al. Begin met een communicatie-ingest contract:
   - `conversation`: kanaal, source IDs (`thread_ts`, `X-GM-THRID`, HubSpot ticket id), subject/topic, first/last message, status.
   - `message`: stable provider id, author account, recipients, timestamp, reply-to, body, attachments, source URL.
   - `participant/account/person`: raw account identifiers eerst bewaren; identity resolution daarna expliciet uitvoeren met confidence, merge history en provenance.
   - `conversation_summary` + `decision/action/commitment/open_loop` extractie als enrichment-output.
   - message/exchange chunks met thread-context, daarna pas door Klai's bestaande contextual/HyPE/dense+sparse/Graphiti/rerank pipeline.
8. **Voor de eindvisie:** maak geen aparte fysieke tabellenset per domein of tenant. Maak een stabiele canonical entity/provenance-kern en zet domeinstructuren in een versioned ontology/schema registry. Promoveer pas naar fysieke tabellen als het domein productmatig stabiel en transactioneel wordt.

## 8. Kritische conclusie voor Klai

Klai is voor generieke kennisretrieval al voorbij superdock: parent-child contextual retrieval, dense+sparse RRF, HyPE-vragen, Graphiti, reranking, confidence bands, deterministic citations en RAGAS-evaluatie zitten er al in. De grootste fout zou zijn om communicatie-RAG te behandelen als "nog een documentconnector".

De ontbrekende Klai-laag is precies wat Engram sterk doet: **communicatie eerst modelleren als interacties tussen mensen**, niet als losse stukken tekst. Als Slack/e-mail/HubSpot/Teams rechtstreeks als documenten in `knowledge.artifacts` landen, verlies je threadstructuur, deelnemersrollen, reply-relaties en relatiegeschiedenis. Klai kan daarna nog steeds uitstekende antwoorden geven op basis van chunks, maar niet betrouwbaar vragen beantwoorden als:

- "Wat heb ik Jantine beloofd?"
- "Welke open acties liggen nog bij Voys?"
- "Wie weet hier waarschijnlijk het meest van?"
- "Waar is dit besluit ontstaan en wie was erbij?"

De praktische route is daarom:

1. **Kopieer Engram's structure-first gedachte naar Klai:** bouw eerst conversation/message/participant-tabellen of een equivalent ingest-artifact contract. Laat ruwe provider-ID's intact.
2. **Voeg een canonical entity layer toe:** resolve accounts, mentions en extracted entities naar golden records voor personen, organisaties, plekken, restaurants, projecten en later domeinspecifieke types.
3. **Kopieer superdock's extractie-les naar Klai:** laat elke conversation automatisch samenvatten naar vragen, besluiten, acties, commitments, topics en entiteiten.
4. **Gebruik Klai's bestaande RAG-stack als execution engine:** converteer conversation/message/extractie naar parent-child chunks met `context_prefix`, HyPE-vragen, sparse/dense vectors, Graphiti en evidence scoring.
5. **Maak communicatie-evals vóór tuning:** een suite met vragen over commitments, personen, thread-afloop, recency en bronverwijzing. Zonder zo'n suite wordt "werkt beter" onmeetbaar.
6. **Maak schema's dynamisch, niet tabellen wildgroeiend:** bewaar domeintypes, attributen, relaties en validatieregels als data. Zo kan Klai later medicatie, voeding of oorlogsgeschiedenis verwerken zonder per tenant honderden SQL-tabellen te beheren.

Kort: **Engram moet Klai's retrieval leren; Klai moet Engram's communicatiemodel leren.** Superdock bewijst dat enrichment/retrieval veel waarde geeft, maar ook dat structuur weggooien later niet meer te repareren is.
