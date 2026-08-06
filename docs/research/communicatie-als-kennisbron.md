# Communicatie als kennisbron — basisdocument

> **Status en herkomst.** Dit is het basisdocument voor communicatie (e-mail, chat, calls, vergaderingen, 1-op-1) als input-laag van de kennisbank. De inhoud is in augustus 2026 verplaatst uit `knowledge-system-fundamentals.md` (Bevinding 12, oorspronkelijk maart 2026) en hier samengebracht met de datamodel-basis en de verwijzingen naar de verdieping. De verdieping zelf leeft bewust in aparte documenten:
>
> | Verdieping | Waar |
> |---|---|
> | Telefonie-keten: ASR/WER, diarisatie vs. dual-channel, GEC, aggregatie, evaluatie, Voys-scenario's | `knowledge-pipeline-architecture.md` §5.5 |
> | Helpdesk-extractieschema, promptstrategie, PII/GDPR, frameworks | `knowledge-pipeline-architecture.md` §1–5 |
> | Datamodel-onderbouwing, entity-resolved hybrid RAG-literatuur, elf-dimensies-toetsingskader | `rag-vergelijking-superdock-engram-klai.md` |
> | Omliggende fundamentals (entiteitstypen, graph-laag, retrieval-verrijking, evaluatie) | `knowledge-system-fundamentals.md` |


Naast documenten en websites is communicatie de tweede grote ingest-bron. Empirisch zijn er vijf communicatietypen die voor een organisatiebrein relevant zijn — elk met eigen kennisprofiel, extractiebenadering en privacycontext.

**Universele regel:** elk communicatietype heeft een eigen signaal/ruis-verhouding en vraagt om een type-specifieke extractiepipeline. Dezelfde aanpak voor alles werkt niet.


---

### E-mail

**Uniek aan e-mail:** geschreven commitments met tijdstempel en naam. Goedkeuringsketens zijn volledig traceerbaar (wie CC'd, in welke volgorde, wie goedkeurde). Cross-organisatie afspraken met klanten en leveranciers staan hier en nergens anders.

**Het ruisprobleem:** 98.4% van alle e-mails bevat ruis voor kennisextractie (Microsoft Research, Enron-corpus). Negen zones per e-mail: geciteerde replies, handtekeningen, juridische disclaimers, doorgestuurde inhoud, serverblokken. Zonder zone-filtering extraheer je dezelfde entiteit meerdere keren — één keer per geciteerde kopie in de thread.

**Vereiste pipeline:**
1. Thread-reconstructie via JWZ-algoritme (op basis van `Message-ID`, `In-Reply-To`, `References` headers)
2. Zone-classificatie (87-91% nauwkeurigheid, Zebra/ACL 2009)
3. Extractie alleen op `author_content` zones — niet op geciteerde tekst
4. Per-bericht extractie + thread-niveau aggregatie (beslissing, commitment, goedkeuringsketen)

**Entiteiten die natuurlijk ontstaan:** Party (afzender, ontvanger, CC), Thread (container), Commitment (actie-item met eigenaar en deadline), Decision (wat werd goedgekeurd), Artifact (bijlage).

**Wat je eruit haalt:** commitments met eigenaar en tijdstempel, goedkeuringsketens, beslissingen, entiteiten (personen, bedrijven, producten, deadlines).

**Bijlagen:** PDF/DOCX-bijlagen gaan door de document-pipeline (Docling), niet de e-mailpipeline.

**Scope:** gedeelde inboxen (support@, info@, sales@) als organisatie-connector. Individuele inboxen zijn persoonlijke keuze — die gaan naar de persoonlijke kennisbank van het individu.

**Privacy:** gedeelde inboxen hebben lage privacyverwachting en duidelijke bedrijfsdoelstelling — geen DPIA-complexiteit. Individuele inboxen zijn buiten scope voor de organisatieknowledgebase.

**Productie-benchmark:** LLM-extractie op e-mail + agenda + chat + documenten gecombineerd: 92% entity accuracy, 89% relationship accuracy (arXiv:2503.07993, 6-maands pilot, 78% adoptie).

---

### Chat (Slack / Microsoft Teams)

**Uniek aan chat:** micro-beslissingen die nooit worden opgeschreven ("we gebruiken Postgres", "skip die migratie voor nu"). Emoji-reacties als lichtgewicht goedkeuringssignaal. Expliciete deliberatietrails: vraag + discussie + reacties + beslissing zijn allemaal zichtbaar en tijdgestempeld in één thread.

**De extractie-eenheid is de thread, niet het bericht.** Individuele berichten zijn gemiddeld 3-8 woorden — te kort voor betrouwbare extractie. Pas na thread-aggregatie ontstaat voldoende context. Dit geldt voor zowel Slack als Teams.

**Signaal/ruis per kanaaltype:**

| Kanaaltype | Schatting signaal |
|---|---|
| `#decisions`, `#architecture` | 40-60% |
| `#dev-team`, `#product` | 15-25% |
| `#general`, `#announcements` | 5-15% |
| `#random`, `#fun` | 1-3% |

Kanaalnaam is een eerste-klas feature in de extractiepipeline: hetzelfde bericht in `#architecture` verdient agressievere extractie dan in `#random`.

**Vereiste pipeline:**
1. Thread-assemblage op `thread_ts` (Slack) of `conversationId` (Teams)
2. Pre-filter: berichten korter dan 10 tekens, bot-notificaties, emoji-only replies weggooien
3. Thread-classificatie: Q&A / beslissing / aankondiging / social chatter
4. Extractie op thread-niveau (niet bericht-niveau)
5. Trigger: thread-close-detectie (geen activiteit voor 2 uur) als primaire batch-trigger

**Kostenwaarschuwing:** aggregatieve queries over grote chat-corpora kosten 17-362 miljoen tokens per query zonder gespecialiseerde retrieval (arXiv:2505.23765). Goede indexering bij opslag is verplicht — niet optioneel.

**Extractie-pipeline:**
1. Thread-assemblage op thread-ID (Slack: `thread_ts`, Teams: `conversationId`)
2. Pre-filter — berichten onder 10 tekens, bot-notificaties, emoji-only weggooien
3. Kanaal-prior — verwachte signaaldichtheid op basis van kanaalnaam (`#decisions` = hoog, `#random` = laag)
4. Thread-classificatie — beslissing / Q&A / aankondiging / sociaal
5. **Pad A: beslissingen** — zelfde aanpak als vergadering-pipeline: claims per spreker → filtering → synthese
6. **Pad B: expertise-mapping** — wie beantwoordt wie, over welk onderwerp → `Person -[KNOWS_ABOUT]-> Topic` in de graaf
7. Trigger — 2 uur geen activiteit = thread gesloten, extractie start

**Wat je eruit haalt:** micro-beslissingen met eigenaar en tijdstempel, expertise-signalen (wie de go-to persoon is per onderwerp), issue-oplossing paren uit Q&A-threads, gelinkte resources met context.

**Indexeringsvereiste:** embed op thread-niveau, niet bericht-niveau. Sla kanaalnaam op als Qdrant-payload-filter. Zonder dit: 17-362 miljoen tokens per aggregatieve query (arXiv:2505.23765).

**Privacy:** Legitimate Interest-grondslag. DM's standaard uitsluiten — hogere privacyverwachting dan kanaalberichten.

---

### Support- en salescalls

**Uniek aan customer calls:** de klant is de primaire informatiebron. Pijnpunten in eigen woorden, concurrentiegenoemingen, bezwaren, beslissingshiërarchieën, churnsignalen — dit staat nergens anders in de organisatie.

**Support vs sales zijn fundamenteel verschillende kennisprofielen:**

| Dimensie | Salescall | Supportcall |
|---|---|---|
| Richting | Outbound, proactief | Inbound, reactief |
| Primaire kennistype | Kwalificatie, bezwaren, concurrentiesignalen | Issuepatronen, oplospaden, productfrictie |
| Kennisverval | Hoog (dealcontext verandert snel) | Laag (issuepatronen zijn stabiel over maanden) |
| GDPR-grondslag | Moeilijker voor prospects | Verdedigbaar voor bestaande klanten |

**De kernbevinding: de waarde zit in het patroon, niet het individuele gesprek.** Eén klant die zegt "de onboarding is verwarrend" is een notitie. 340 klanten die dat in zes maanden zeggen is een productroadmap-input. Gong bouwt een "revenue graph" waarbij waarde pas zichtbaar wordt bij aggregatie over honderden gesprekken.

**Nieuw entiteitstype vereist: externe Party.** Voor het eerst komen er entiteiten voor die buiten de organisatie staan. Twee subtypes:
- `external_party` (klant, prospect) — neemt deel aan het gesprek
- `mentioned_entity` (concurrent, product van concurrent) — wordt *genoemd*, neemt nooit deel

Die twee moeten expliciet gescheiden zijn in het datamodel. Een concurrent is geen deelnemer.

**Real-time vs post-call:** alle productiesystemen (Gong, Chorus, CallMiner) doen post-call analyse als standaard. Real-time is zinvol voor agent-assist en compliance-monitoring, maar niet voor kennisextractie. Start altijd met post-call.

**Extractie-taxonomy (cross-platform consensus: Gong, Chorus, CallMiner):**
1. Transcriptie + speaker-diarisatie
2. Sentiment-arc per spreker per segment
3. Named entities: personen, bedrijven, producten, datums, bedragen
4. Topics en thema's
5. Objections en buying signals (intent-classificatie)
6. Actiepunten en commitments
7. Competitor mentions
8. Compliance flags
9. LLM-samenvatting
10. Gestructureerde velden via LLM-extractie naar schema

**Call-type:** geen input maar output. Het LLM bepaalt het type (`support`, `sales`, `onboarding`, etc.) op basis van inhoud — als veld in de extractie-output, niet als aparte classificatiestap. Geen vooraf gedefinieerde taxonomie nodig.

**Patroonwaarde:** de werkelijke strategische waarde zit in aggregatie over alle calls. Eén klacht is een notitie; 340 klachten over hetzelfde in zes maanden is een productroadmap-input. Afgeleide patroonknopen worden periodiek berekend en zijn zelf kennisknopen in de graaf.

**Voys-voordeel:** Voys is een telecomplatform met bestaande call recording-infrastructuur en de juridische randvoorwaarden (GDPR-grondslag, consent-flows) zijn al geregeld op platformniveau. De connector is een integratie op bestaande opnames — geen nieuw opnamesysteem nodig.

**Verdieping (aug 2026):** de volledige keten audio → transcript → structuur → patronen → evaluatie is uitgewerkt in §5.5 van `knowledge-pipeline-architecture.md`, inclusief realistische telefonie-WER-cijfers (20–30%, niet de marketing-6–10%), het dual-channel-alternatief voor diarisatie, de aggregatie-architectuur (HDBSCAN + outlier-cluster-trenddetectie + bi-temporele patroonknopen in Graphiti) en de evaluatie-aanscherpingen (field- vs. record-level, LLM-judge-kalibratie).

---

### Vergadering (groep)

**Uniek aan vergaderingen:** beslissingen met sociale bewijskracht — verbaal genomen in aanwezigheid van de groep, met een ander gewicht dan asynchrone goedkeuringen. Consensus-type is meetbaar: opgelegd, toegejuicht, basis, of deliberatief (Cambridge Handbook of Meeting Science). Dat onderscheid vertelt hoe duurzaam een beslissing is. Dissent en minderheidsstandpunten bestaan alleen in het transcript — ze verdwijnen uit samenvattingen.

**De optimale extractie-aanpak: vier stappen, niet twee.**

Het onderzoek (FRAME, 2025) toont dat modulaire extractie hallucinaties met 3 punten op een 5-puntsschaal reduceert ten opzichte van directe samenvatting. De pipeline:

**Stap 1 — Claim-extractie per spreker** *(nieuw, vóór alles)*
Elke uitspraak wordt een gestructureerd tuple:
`{ speaker, claim, type: [voorstel | vraag | beslissing | commitment | bezwaar], confidence }`
Sprekerattributie wordt hier vastgelegd en nooit meer losgelaten.

**Stap 2 — Relevantiefiltering**
~40% van claims valt af: filler, herhaling, sociaal commentaar. Alleen inhoudelijke claims gaan door.

**Stap 3 — Verificatie**
Contradicteer claims elkaar? Spreekt dezelfde spreker zichzelf tegen? Is een commitment bevestigd of twijfelachtig uitgesproken ("misschien", "ik denk")?

**Stap 4 — Synthese**
Pas nu: decisions, action_items (met owner), open_questions, next_steps, summary markdown.

Dat zijn drie LLM-calls in plaats van twee — maar valse attributie (de meest schadelijke fout) wordt structureel voorkomen.

**Drie lagen bewaren:**
- Ruwe transcript — audittrail, geschillenresolutie
- Samenvatting (markdown) — menselijke consumptie
- Gestructureerde extractie (decisions + action_items + claims) — kennisbank input

**Sprekerattributie is kritiek.** De meest schadelijke fout in meeting-extractie is valse attributie: "Alex stelde X voor" terwijl Alex vroeg "wat als we X zouden doen?" — vraag vs. voorstel. Systemen die sprekers samenvoegen of tijdstempels weggooien forceren het LLM te raden wie wat zei. Dat maakt valse organisatierecords.

**Drie lagen bewaren, niet één kiezen:**

| Laag | Wat het bewaart | Verliest | Gebruik |
|---|---|---|---|
| Ruwe transcript | Alles: twijfeltaal, dissent, sociale dynamiek | Moeilijk doorzoekbaar | Audittrail, geschillen |
| Samenvatting | Hoofdonderwerpen, kernbeslissingen | Rationale, minderheidsstandpunten | Menselijke consumptie |
| Gestructureerde extractie | Beslissingen + eigenaren + deadlines | Context, redenering | Taakopvolging |
| Kennisgraaf | Relaties over vergaderingen heen | In-meeting dynamiek | "Wie heeft X besloten?" |

**Het correctievenster:** een Slack-bot implementatie toonde dat een 15-minuten correctievenster na de vergadering de actiepunt-capture rate van 22% naar 100% bracht en false positives van 14% naar 3% reduceerde. Automatisch extraheren haalt 60-80%; menselijke mini-validatie direct na afloop maakt het compleet.

**Productie-benchmark:** Amazon Nova meeting pipeline: gemiddeld onder 6 minuten voor een 1-uur vergadering, 15% verbetering in actiepunt-identificatie ten opzichte van baseline.

**Aandachtspunt:** actiepunt-annotatie heeft een inter-rater agreement van kappa=0.36 — zelfs mensen zijn het maar voor 36% eens over wat een actiepunt is. Elk geautomatiseerd systeem erft die ambiguïteit. Menselijke bevestiging blijft nodig voor hoge betrouwbaarheid.

---

### 1-op-1 gesprekken

**Geen apart infrastructuurtype.** Een 1-op-1 gesprek gaat via een van de bestaande opname-pipelines: Google Meet (Vexa), telefoon (Voys), of losse opname (Scribe). De beslissing om op te nemen ligt bij de gebruiker — Klai voegt daar geen extra poort aan toe.

**Wat structureel anders is:** twee deelnemers in plaats van een groep. De extractie-pipeline is identiek aan een vergadering, maar de output verschilt:
- Geen groepsdynamica of meerderheidsbeslissingen
- Sterkere commitment-attributie: met twee sprekers is "ik regel het" altijd eenduidig van wie
- Hogere kennisdichtheid per minuut: geen coördinatie-overhead, 100% van het gesprek is inhoud

**Wat uniek is ten opzichte van vergaderingen:** persoonlijke commitments, coaching-inhoud, carrièresignalen, eerlijke feedback — kennis die in groepsverband zelden uitgesproken wordt. Dit is ook de reden dat de privacy-verwachting hoger is dan bij een vergadering, maar dat is aan de gebruiker om te wegen bij de keuze om op te nemen.

**Praktisch gevolg:** geen apart connector-type nodig. Het opname-platform bepaalt de pipeline. De kennisbank krijgt twee-spreker-transcripten als input — hetzelfde formaat als een groepsvergadering.

---

### Hoe elk communicatietype de pipeline inkomt

| Type | Extractie-eenheid | Verplichte voorbewerking | Pipeline | Primaire output |
|---|---|---|---|---|
| Vergadering | Transcript + sprekerlabels | Sprekerattributie bewaren | 4-staps FRAME: claims → filtering → verificatie → synthese | Decisions, Action items, Topics |
| 1-op-1 | Transcript (2 sprekers) | Zelfde als vergadering | Zelfde als vergadering | Commitments, Decisions |
| Call | Post-call transcript | Diarisatie, noise-filtering | Type-classificatie als output, 10-veld extractie | Issues, Commitments, Competitors, Call-type |
| E-mail | Thread (na JWZ-reconstructie) | Zone-filtering (87-91% nauwkeurigheid) | Per-bericht → thread-aggregatie | Commitments, Decisions, Approval chains |
| Chat | Thread (na 2u inactiviteit) | Pre-filter <10 tekens, kanaal-prior | Pad A: beslissingen / Pad B: expertise-mapping | Micro-decisions, Expertise-signalen, Issue-solution pairs |

**De universele stappen** voor elk type:
1. Ruwe audio of tekst → tekst (ASR of native)
2. Extractie-eenheid bepalen (thread, call, transcript)
3. Type-specifieke ruisverwijdering
4. Gelaagde extractie: claims per spreker → filtering → relaties → beslissingen → commitments
5. Lichtgewicht menselijke validatie (correctievenster: 22% → 100% capture rate, false positives 14% → 3%)
6. Opslaan in alle drie lagen: PostgreSQL + Qdrant + FalkorDB

**De rode draad:** automatische extractie haalt 60-80%. Menselijke mini-validatie direct na afloop maakt het compleet. De kwaliteit wordt bepaald bij opslag — niet bij zoeken.

---

## Het datamodel: structuur eerst

De vergelijking superdock/Engram/Klai (zie `rag-vergelijking-superdock-engram-klai.md`) legt bloot dat Klai's gat voor communicatiedata niet in retrieval zit maar in het **conversatiemodel en identiteit** (dimensies D2/D3 van het toetsingskader). De kernles: **structuur weggooien is onomkeerbaar; extractie kun je altijd later toevoegen.** Wie Slack/e-mail/Teams als platte documenten in `knowledge.artifacts` laat landen, verliest threadstructuur, deelnemersrollen en reply-relaties — en kan daarna nooit meer betrouwbaar antwoorden op "Wat heb ik Jantine beloofd?" of "Wie weet hier het meest van?".

**Vaste bronlaag** (deterministisch vastleggen, niet door een LLM laten "ontdekken"):

- `conversation` — thread, channel thread, ticket, call, meeting, DM; met kanaalspecifieke ID's (`thread_ts`, `X-GM-THRID`, ticket-id) als bronreferentie
- `message` — stable provider id, timestamp, sender account, body, reply-to, attachments
- `participant_account` — e-mailadres, Slack user id, telefoonnummer, CRM-contact-id
- `transcript_segment` — spreker, tijdspanne, tekst, STT-confidence

**Kennislaag daarboven** (canoniek en uitbreidbaar): `person`, `organization`, `claim`, `decision`, `action`/`commitment`, `topic`, `relationship`.

**Canonical entity layer** — de "alles naar 1"-laag: `raw mention → candidate generation → matching → merge decision → canonical entity`. "Martijn" in Slack, "M. Aslander" in e-mail, een LinkedIn-profiel en een telefoonnummer resolven naar één `person_123`; de graph gebruikt daarna alléén canonical IDs, en alle ruwe vermeldingen blijven bewaard als evidence met merge-history en provenance. Dit is een governance-feature, geen graph-feature — vector search is geen identity-laag en een knowledge graph garandeert geen "1 entiteit". Volledige onderbouwing, literatuur (GraphRAG, KAG, Deg-RAG, ER-survey) en het schema-registry-patroon voor dynamische domeinen: zie het vergelijkingsrapport, secties 5–8.

**Volgorde voor Klai:** eerst het conversation/message/participant-contract, dan de canonical entity layer, dan pas extractie (samenvattingen, besluiten, commitments, topics) — en Klai's bestaande contextual/HyPE/dense+sparse/Graphiti/rerank-stack als execution engine eronder.


---

### Casestudy: Cerebras Knowledge (juli 2026) — onafhankelijk getoetst (aug 2026)

Cerebras publiceerde in juli 2026 een casestudy van precies deze communicatie-inputlaag: een interne kennisbank die drie maanden na launch **15.000 vragen per dag** verwerkt, met Slack als belangrijkste bron.

> **Bewijsweging vooraf.** Dit is n=1: één vendor-blog, zonder gepubliceerde metingen ("accuracy increased significantly" is geen cijfer), over een intern single-tenant systeem — en kennissystemen zijn niet Cerebras' specialisatie. Daarom is elke ontwerpkeuze in augustus 2026 getoetst aan (a) partijen waarvoor dit wél de kernbusiness is — Slack AI zelf, Glean, Microsoft 365 Copilot, Atlassian Rovo, Notion, Uber, Coveo, Discord — en (b) de academische IR/NLP-literatuur. De status hieronder per claim is het resultaat van die toetsing; behandel Cerebras' concrete drempelwaarden en mechanieken als startpunt voor eigen kalibratie, nooit als gevalideerde defaults.

| # | Cerebras-keuze | Status na onafhankelijke toetsing |
|---|---|---|
| 1 | **Thread als indexeer-eenheid** (niet losse berichten) | **Bevestigd.** Glean verwoordt het vrijwel letterlijk identiek ("think about threads as documents"); Rovo groepeert gerelateerde berichten; Slack AI redeneert op threadniveau. Academisch: hele-thread-als-één-blob én los-bericht zijn beide inferieur aan fijnmaziger indexeren met aggregatie — met de kanttekening dat géén vaste granulariteit optimaal blijkt; adaptieve multi-granulariteit is de opkomende best practice (ICLR 2026-lijn). |
| 2 | **Distillatie embedden i.p.v. ruwe tekst** (vraag/samenvatting/resolutie) | **Tegengesproken.** Geen enkele specialist doet dit voor chat; Notion embed expliciet ruwe spans; multi-paper academische consensus dat summarize-then-index verliest op conversationele data (LoCoMo-ablaties arXiv:2603.02473, arXiv:2601.00821, plus onafhankelijke 2026-memory-papers). Het houdbare deel: een synthetische zoekvraag als **éxtra lexicale leg** — de doc2query-lijn meet dat generatieve toevoeging van vocabulaire betrouwbaar helpt (MS MARCO MRR@10 0,184 → 0,277), terwijl comprimeren detail vernietigt. Ruwe tekst embedden + zoekvraag ernaast, dus. |
| 3 | **Bursting met kwaliteitsdrempel** (IDF ≥ 4.0, ≥ 200 tekens, of reacties) | **Richting bevestigd, invulling n=1.** Laag-signaal-filtering vóór indexeren is bewezen nuttig (settled; Slack AI weegt beslispunten/actiepunten zwaarder, Glean filtert op kanaalniveau). Maar **reacties als kwaliteitssignaal is academisch tegengesproken**: het meest geciteerde onderzoek (Anderson et al., KDD 2012, Stack Overflow) vond dat votes langetermijnwaarde nauwelijks beter dan willekeur voorspellen, en vote-gebaseerd ranken benadeelt structureel nieuwe kwaliteitscontent (cold-start bias). De drempelwaarden zelf zijn Cerebras' interne tuning, nergens gevalideerd. |
| 4 | **Socket Mode real-time + hele-thread-herschrijf per event** | **Deels tegengesproken.** Slack's eigen developer-documentatie raadt Socket Mode af voor productie (webhooks zijn de productieroute). De specialisten kiezen bovendien anders: Glean's productie-Slackpad is federated live-query (helemaal geen persistente content-index), Discord koos op schaal bewust voor batched indexing, en Notion doet het omgekeerde van hele-thread-herschrijven (hash-vergelijking per span om herverwerking te vermijden). Wat overeind blijft: push-boven-poll als principe (Notion's Kafka-pad, Microsoft webhooks) en dedup op stabiel event-ID. |
| 5 | **Per kanaal eigen sync-cadans** (incident-kanaal vaker) | **Zwak bevestigd.** Per-source frequentie-configuratie is standaard (Microsoft-connectors); kanaalniveau-inclusie/exclusie ook (Glean, Coveo). Maar per-kanaal *cadans* op deze granulariteit documenteert niemand anders. Plausibel, niet onafhankelijk bewezen. |
| 6 | **Hybride retrieval: exacte lexicale leg + embeddings + IDF + age decay, gewogen RRF** | **Bevestigd als categorie, details afwijkend.** Hybride lexicaal+semantisch is industriestandaard (Rovo's BM25+KNN+cross-encoder is de dichtstbijzijnde architectuur; Uber meet +27% acceptabele antwoorden na toevoegen BM25-leg; Glean: 60–70% van enterprise-queries lost lexicaal op — de exacte-match-les is dus stevig). Twee details doet niemand na: IDF als áparte vierde leg (iedereen vouwt IDF in BM25), en naïeve age decay — Glean waarschuwt daar expliciet tegen en weegt recency met citatie-/tevredenheidssignalen. Extra confound: een LLM-reranker heeft zijn éígen ongecontroleerde recency-bias (tot 95 rangposities verschuiving, arXiv:2509.11353) — vendor-observaties over age decay kunnen dat effect niet scheiden van de ontworpen decay. |
| 7 | **`who_knows` expertise-tool** | **Bevestigd als categorie.** Glean (Enterprise Graph) en Microsoft (Viva People Skills) bouwen en vermarkten hetzelfde — beide op bredere signalen dan chat alleen (documenten, tickets, org-chart, meetings). Academisch fundament: document-centrische aggregatie verslaat kandidaat-centrisch (settled, TREC Enterprise/Balog); recency- en participatiebias zijn benoemde faalmodi om in het ontwerp mee te nemen; chat als expertise-substraat is peer-reviewed vrijwel onbestudeerd, en expert-finding-benchmarks blijken zelf constructie-vertekend (arXiv:2410.05018). |
| 8 | **Smalle, LLM-vrije retrieval-primitieven via MCP; agent orkestreert** | **Bevestigd als richting.** Federated/MCP-gebaseerde live-tools zijn precies de beweging bij specialisten (Glean RTS, Microsoft federated connectors, Guru's MCP-integratie), en het sluit aan op Anthropics eigen MCP-guidance. Consequentie voor ons blijft: elk nieuw communicatietype idealiter als eigen retrieval-primitief, niet als extra bron achter één brede search-tool. |

**Wat de specialisten tonen dat Cerebras níét doet** — de betere referentiepatronen voor een multi-tenant product: Notion's hash-and-skip (alleen gewijzigde spans herverwerken, permission-only-wijzigingen als PATCH zonder re-embedding), Glean's near-real-time ACL-sync via webhooks, en Notion's query-time permissie-hercheck met een gebonden staleness-venster (≤ 1 uur). Voor Klai — waar tenant-isolatie en ACL's contractueel zijn — zijn dát de maatgevende voorbeelden, niet een interne single-tenant tool.

**Wat Cerebras níét oplost — en ons onderzoek wel vereist:**

| Onze eis | Cerebras' situatie |
|---|---|
| PII-redactie + GDPR-grondslag (blocker in SPEC-KB-BACKLOG O6) | Intern, single-tenant, engineers — niet van toepassing |
| Correctievenster / menselijke validatie (60-80% → 100%) | Afwezig; volledig automatisch |
| Sprekerattributie via diarisatie (calls) | Niet nodig — Slack heeft expliciete auteurs |
| Consensus-type en dissent-behoud (vergaderingen) | Buiten scope |
| Multi-tenant isolatie, citatiecontract | Niet van toepassing |

**Implicatie voor implementatievolgorde.** Van de vijf communicatietypen blijft **chat (Teams/Slack) de goedkoopste eerste stap** — op eigen structurele gronden: expliciete auteurs (geen diarisatie), expliciete thread-structuur (geen JWZ-reconstructie), en event-API's. Kanttekening: Cerebras' signaalprofiel (engineers, Slack-cultuur) is niet ons ICP — kanaal-signaaldichtheden bij Nederlandse MKB-klanten moeten opnieuw gemeten worden, niet overgenomen. Calls hebben het Voys-voordeel maar blijven geblokkeerd op PII-redactie; e-mail draagt de zone-filtering-complexiteit. De aggregatie-inzichten (patroonwaarde, expertise-mapping) gelden voor alle drie.

*Primaire bron: [How Cerebras Built Its Enterprise Knowledge Base](https://www.cerebras.ai/blog/how-we-built-our-knowledge-base) (juli 2026). Toetsingsbronnen (aug 2026): Slack Engineering ("How We Built Slack AI", "Search at Slack"), Glean-connectordocs + engineering-interviews, Microsoft Learn (Semantic Index, Copilot-connectors, Viva People Skills), Atlassian Engineering (Rovo search relevance), Notion Engineering ("Two years of vector search"), Uber Engineering (Enhanced Agentic-RAG), Discord Engineering; academisch o.a. LoCoMo-ablaties (arXiv:2603.02473, arXiv:2601.00821), Anderson et al. KDD 2012, doc2query/docTTTTTquery, TREC Enterprise/Balog, expert-finding-benchmarkbias (arXiv:2410.05018), LLM-reranker-recency-bias (arXiv:2509.11353).*

---

## Verdieping: telefonie-keten

Voor calls is de volledige keten audio → transcript → structuur → patronen → evaluatie uitgewerkt in `knowledge-pipeline-architecture.md` §5.5. De hoofdpunten, hier alleen als samenvatting:

- **Realistische telefonie-WER is 20–30%** (niet de 6–10% uit marketing); een groter extractie-LLM compenseert ASR-ruis niet. Twee integratiescenario's met Voys: kant-en-klare transcripten via de API (→ leverancierschecklist: WER-sample, sprekerlabels, ITN, confidence-scores) of ruwe audio (→ dual-channel verifiëren, dan is diarisatie een non-probleem).
- **Aggregatie**: embed → HDBSCAN → LLM-label, met outlier-cluster-groei als vroege trenddetectie en bi-temporele `IssuePattern`-knopen in Graphiti; kandidaat-patroon (tientallen calls) versus bevestigde trend (~1.000 voor ±3pp).
- **Evaluatie**: field-level én record-level rapporteren, LLM-as-judge pas na κ-kalibratie, feedback-capture ín de bestaande review-workflow.

