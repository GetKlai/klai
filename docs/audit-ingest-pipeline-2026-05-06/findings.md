# Knowledge-ingest pipeline audit — 2026-05-06

> **Branch:** `docs/audit-ti-2026-05-05`
> **Onderzoeker:** Claude (Opus 4.7) i.s.m. Mark Vletter
> **Methodologie:** code-first (`klai-knowledge-ingest/knowledge_ingest/`,
> `klai-knowledge-ingest/alembic/versions/0001_baseline.py`,
> aanverwante callers in `klai-portal`, `klai-connector`, `klai-knowledge-mcp`,
> `klai-scribe`). Bestaande flow-docs zijn bewust **niet** als bron gebruikt
> tijdens deze ronde — pas in fase 3 vergelijken we met
> `docs/architecture/knowledge-ingest-flow.md`.
> **Indexed commit:** `d7e6de3`

---

## Context

Tijdens een walkthrough van de ingest-pipeline (Fase 1 sync + Fase 2 async
Procrastinate) heb ik een sceptische review uitgevoerd op het ontwerp.
Acht zorgen kwamen naar voren — sommige direct geobserveerd in de code,
andere afgeleid uit het samenspel van componenten. Dit document legt elke
zorg vast met:

- **Severity:** ingeschat impact op correctheid / kosten / observability
- **Status:** wat ik geverifieerd heb tegen de code
- **Code-locatie:** exacte file:line ankers
- **Observatie:** wat de code doet
- **Vraag voor onderzoek:** wat is industry-standard / best-practice in 2026?
- **Onderzoeksbevindingen:** [leeg — wordt gevuld door agent-onderzoek]

Doel van fase 2: per finding een agent laten verifiëren tegen de code +
state-of-the-art onderzoek doen (RAG-pipelines anno 2026, Procrastinate
patterns, Qdrant payload best practices, etc.). Daarna fase 3: integratie
en besluit hoe verder.

---

## Hoe Fase 1 en Fase 2 samenhangen (referentiekader)

Voor begrijpelijkheid van de findings hieronder, zonder de volledige
walkthrough te herhalen:

```
HTTP /ingest/v1/document  ──▶  Phase 1 (sync, in-request)
                                │
                                ├─▶ Qdrant: vector_chunk only
                                ├─▶ PG: artifact + parent_chunks placeholder
                                └─▶ enqueue: enrich-{interactive|bulk}
                                            graphiti-bulk

Procrastinate worker (LLM-lane)
  ├─▶ enrich-interactive  ──▶ Phase 2: LLM enrichment + sparse + HyPE
  └─▶ graphiti-bulk       ──▶ FalkorDB knowledge graph
```

Drie ingangen tot `/ingest/v1/document`:

| Ingang | Caller | Path |
|---|---|---|
| **Gitea-webhook** | portal-editor → Gitea → webhook | gedebounced via `ingest_from_gitea` task ([ingest_tasks.py:28](../../klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py#L28)) — **veilig**, re-fetch bij executie |
| **Direct POST** (MCP) | klai-knowledge-mcp `save_personal_knowledge` ([main.py:273](../../klai-knowledge-mcp/main.py#L273)) | synchroon, content in request body |
| **Direct POST** (connector sync) | klai-connector sync_engine ([sync_engine.py:180](../../klai-connector/app/services/sync_engine.py#L180)) | synchroon, per-document |
| **Direct POST** (portal partner / app) | partner_knowledge.py, knowledge_ingest_client.py | synchroon |
| **Direct POST** (scribe) | scribe-api knowledge_adapter | synchroon |

Findings die "direct-POST flow" noemen, slaan op de laatste 4 — niet op
de portal-editor flow.

---

## Bevindingen

### Finding 1: AlreadyEnqueued kan latere content laten "verdampen" via direct-POST flow

**Severity:** HIGH (correctheid, niet hypothetisch — alleen voor direct-POST)
**Status:** Code bevestigd, nog **niet runtime gerepro'd**
**Code-locatie:**
- [routes/ingest.py:524-560](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L524-L560) — defer met `queueing_lock` + `document_text=req.content` als bevroren arg
- [enrichment_tasks.py:108-175](../../klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py#L108-L175) — task signature accepteert `document_text` en geeft door aan `_enrich_document`
- [enrichment_tasks.py:231-345](../../klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py#L231-L345) — `_enrich_document` gebruikt `document_text` als argument, leest niet uit `pg_store.artifact`

**Observatie:**
1. **T=0**: direct-POST met content `c1` → Phase 1 schrijft raw vectors van `c1` naar Qdrant; enrichment-task voor `c1` enqueued met `queueing_lock=f"{org_id}:{kb_slug}:{path}"` en `document_text=c1` als arg
2. **T=δ** (binnen worker-pickup-window): tweede direct-POST met content `c2` → Phase 1 schrijft raw vectors van `c2` (overschrijft `c1`); enrichment-task BLOCKED met `AlreadyEnqueued`
3. **T=worker_pickup**: worker draait `c1`-task → leest `c1` uit task-args → schrijft enriched vectors voor `c1` → **overschrijft de raw `c2` vectors**

Eindstand: gebruiker schreef `c2`, ziet uiteindelijk een enriched `c1`.
Geen error, geen log, geen indicatie.

**Verschil met Gitea-flow:** Gitea-webhook gebruikt `schedule_in=ingest_debounce_seconds` + een task die `_fetch_gitea_file` doet bij executie ([ingest_tasks.py:28-72](../../klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py#L28-L72)). Geen content in args → altijd LATEST. Dat patroon werkt correct.

**Direct-POST callers die kwetsbaar zijn:**
- klai-knowledge-mcp `save_personal_knowledge` ([main.py:273](../../klai-knowledge-mcp/main.py#L273))
- klai-connector `sync_engine` ([sync_engine.py:180](../../klai-connector/app/services/sync_engine.py#L180))
- klai-portal `partner_knowledge.py`, `knowledge_ingest_client.py`
- klai-scribe `knowledge_adapter.py`

Het bug-venster is "tussen Phase 1 voltooid en worker pickt task op" —
typisch enkele seconden, maar tijdens een drukke LLM-lane (bulk crawl)
makkelijk 30–60s.

**Vraag voor onderzoek:**
1. Wat doen volwassen RAG-pipelines (LangChain, LlamaIndex, Pinecone-managed,
   commercial vendors) als dezelfde document-path twee keer snel achter
   elkaar wordt geschreven?
2. Standaard-patronen voor "queue debounce zonder content-staleness":
   - schedule_in + re-fetch from source-of-truth (Gitea-pattern)
   - replace-by-key (cancel oude task, enqueue nieuwe)
   - last-writer-wins op message-ID
3. Wat zijn de Procrastinate-best-practices voor dit type race?
4. Hoe verhouden CRDT / event-sourced ingest pipelines zich hier toe?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-1.md](research/finding-1.md))*

- **Verificatie:** alle vier sub-claims bevestigd. `document_text` is bevroren in Procrastinate JSONB args op enqueue-moment (regel 551), `_enrich_document` re-readt nooit uit pg_store (alleen `kb_config.get_kb_visibility` voor de visibility-flag).
- **Belangrijke nuance die mijn claim miste:** Procrastinate `queueing_lock` geldt **alleen voor `todo`-status** ([Procrastinate docs](https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html)). Als c1-task al in `doing` is wanneer c2 binnenkomt, wordt c2 wél correct ingepland — geen AlreadyEnqueued. Maar er is dan een **kortdurend tweede inconsistentievenster** (T=3→T=4) waar c1's enrichment-write c2's raw vectors overschrijft, totdat c2's task afloopt en herstelt.
- **Industry standard:** debounce-and-re-fetch is canoniek (LlamaIndex docstore-pattern, Inngest debounce-docs, BullMQ-deduplication). De Gitea-flow in deze codebase is een correcte implementatie van het patroon. De direct-POST-flow mist het canonical-table-leg (er is geen Gitea-equivalent om uit te re-fetchen).
- **Aanbevolen fixes (ranked):**
  1. **Fix 2 (structureel, aanbevolen):** Schrijf `document_text` naar `knowledge.artifacts` (al gebeurt in `extra->>'document_text'`) en geef alleen `artifact_id` aan de enrichment-task. Worker leest content op uitvoertijd uit PG. Matched LlamaIndex-pattern + Gitea-pattern. Vereist refactor van `_enrich_document` signature.
  2. **Fix 3 (defensieve safety net, kan nu zonder migratie):** Content-hash guard in `_enrich_document` — vergelijk hash van `document_text` arg met huidige `extra->>'content_hash'`; bij divergentie abort en laat de nieuwere task het werk doen.
  3. **Fix 1 (minimaal):** cancel-and-re-enqueue i.p.v. AlreadyEnqueued slikken. Heeft een eigen race-window (cancel + enqueue is niet atomair) — alleen geschikt als tussenoplossing.
  4. **Fix 4:** schedule_in toepassen op direct-POST flow — vereist Fix 2 als prerequisite.
- **Risico:** **low-to-medium** waarschijnlijkheid (venster typisch seconden, langer onder belasting), **medium** impact (silent quality-degradation tot volgende save). Niet data-loss, wél onzichtbare kwaliteitsregressie tot self-heal.

---

### Finding 2: Phase 2 ververst alleen `visibility`, niet andere muteerbare metadata

**Severity:** HIGH (data-consistency)
**Status:** Direct geverifieerd in code
**Code-locatie:** [enrichment_tasks.py:382-385](../../klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py#L382-L385)

**Observatie:**
```python
# Refresh visibility from kb_config at write time — catches any visibility
# change that happened while this task was queued or running.
pool = await get_pool()
extra_payload["visibility"] = await kb_config.get_kb_visibility(org_id, kb_slug, pool)
```

Alleen `visibility` wordt opnieuw uit de autoritatieve source gelezen.
NIET ververst:
- `taxonomy_node_ids` (kan tussen Phase 1 en 2 wijzigen als admin taxonomie reorganiseert)
- `tags` (vooral LLM-gegenereerde subset)
- `content_label`
- `kb_name`, `connector_type`, `source_domain`
- Frontmatter-metadata

Scenario: gebruiker hernoemt een KB tussen Phase 1 en Phase 2 → verrijkte
chunks krijgen oude `kb_name`. Voor `taxonomy_node_ids` is het ernstiger
omdat het search-time filtering beïnvloedt.

**Vraag voor onderzoek:**
1. Is dit een bewuste snapshot-keuze ("deze waarden waren waar op
   ingest-tijdstip") of een omissie?
2. Wat is best-practice voor tweelaags-RAG-pipelines (sync + async
   enrichment) m.b.t. metadata-refresh?
3. Welke velden zijn "snapshot" en welke "live"? Bestaat hier een
   industry-norm voor?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-2.md](research/finding-2.md))*

- **Verificatie:** claim volledig bevestigd. `visibility` wordt expliciet ververst met code-comment dat de keuze toelicht; alle overige mutable fields (taxonomy_node_ids, tags, content_label, kb_name, connector_type, source_domain) zijn bevroren vanuit Phase 1 snapshot. Geen retrieval-time PG-join om te compenseren — Qdrant matcht `taxonomy_node_ids` en `tags` direct als payload-filters. Geen design-doc toelichting voor de snapshot-keuze van non-visibility velden.
- **Industry standard:** dominante 2026-patroon is **metadata-only partial update API** (Pinecone, Weaviate, Databricks Mosaic AI Vector Search) — ACL- en tag-wijzigingen worden gepropageerd zonder re-embedding. Embeddings ↔ contentwijzigingen; metadata ↔ event-driven propagation bij config changes. Verschillende frequenties, verschillende correctheidsvereisten.
- **Risico-ranking per veld:**
  1. **`taxonomy_node_ids` — HIGH:** beïnvloedt retrieval correctness direct (Qdrant filter). Post-ingest taxonomy-reorganisatie laat documenten onzichtbaar.
  2. **`tags` — MEDIUM:** ook Qdrant filter; risico hangt af van wie tags muteert (portal vs adapter vs LLM).
  3. **`kb_name`, `content_label`, `source_domain`, `connector_type` — LOW:** display-only, geen filter-impact.
  4. **Frontmatter metadata — NONE:** definitionally snapshot.
- **Aanbevolen fix (Priority 1):** re-read `taxonomy_node_ids` uit PG bij Phase 2 write-time, exact zelfde patroon als de bestaande visibility-refresh. Eén PG-query extra per enrichment job. Voor `tags` afwegen of de LLM-output (alleen Phase 1 gegenereerd) wel of niet de PG-versie moet overschrijven bij refresh.
- **Long-term:** event-driven invalidation pipeline waarbij taxonomy-mutaties in portal een Qdrant batch-update triggeren voor alle affected chunks.

---

### Finding 3: `extra_payload` is een untyped 20-veld side-channel met CRIT-classified passthrough-pitfall

**Severity:** HIGH (architectuur-fragiliteit, al meermaals als pitfall vastgelegd)
**Status:** Code bevestigd; pitfall expliciet gedocumenteerd in repo
**Code-locatie:**
- Constructie: [routes/ingest.py:467-505](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L467-L505)
- Doorvoer: [routes/ingest.py:540-560](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L540-L560) (`defer_async(... extra_payload=extra_payload)`)
- Consumptie: [enrichment_tasks.py:402-419](../../klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py#L402-L419) (`upsert_enriched_chunks(extra_payload=extra_payload)`)
- Pitfall: `.claude/rules/klai/projects/knowledge.md` — "Procrastinate enrichment passthrough (CRIT)"

**Observatie:**
`extra_payload: dict` accumuleert minimaal 20 velden vanuit verschillende
bronnen (frontmatter, kb_config, connector-state, classifier-output,
adapter `extra`). Deze dict wordt JSON-geserialiseerd in Procrastinate
job-args. Op Phase 2 doet `qdrant_store.upsert_enriched_chunks` eerst een
`delete` van bestaande points en re-insert met `base_payload.update(extra_payload)`
— dus alles wat NIET in de dict zit verdwijnt definitief uit Qdrant.

De repo-eigen pitfall noemt deze fragiliteit als CRIT en is al door
meerdere bugs heen geleerd. Geen TypedDict, geen Pydantic-model, geen
schema — pure tribal knowledge.

**Vraag voor onderzoek:**
1. Wat is best-practice voor het overdragen van metadata tussen sync- en
   async-stages in een RAG-pipeline?
2. Pydantic-models / dataclass / TypedDict / Protocol — welke is meest
   geschikt voor Procrastinate task-payloads?
3. Hoe doen volwassen pipelines (Haystack, LangChain Expression Language,
   LlamaIndex IngestionPipeline) dit?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-3.md](research/finding-3.md))*

- **Verificatie + harder cijfer:** **30 velden** in `extra_payload`, niet 20. Verdeeld over vier bronnen:
  - 20 expliciete assignments in [routes/ingest.py:463-509](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L463-L509)
  - 6 adapter-injected via `req.extra.update()` (crawl-specifiek: `links_to`, `anchor_texts`, `incoming_link_count`, `image_urls`, `front_matter`, `source_connector_id`)
  - 4 frontmatter-passthrough via `fm_meta_for_payload.update()`
  - 2 worker-side gemuteerd (`document_summary`, `document_language`)
- **Bevestigde historische bug:** commit `cbdfdda5` (2026-04-06) — `content_label` verdween post-enrichment omdat het wel berekend maar niet in `extra_payload` gezet was voor `defer_async`. **Derde** bekende instantie van het patroon (taxonomy_node_ids was de eerste).
- **Nul tests** bewaken het volledige passthrough-contract.
- **Industry reality-check:** Celery 5.5+ heeft native Pydantic-support (`@app.task(pydantic=True)`). Procrastinate niet — custom `json_dumps/json_loads` is mogelijk maar fragiel voor geneste JSONB. **Belangrijk:** LlamaIndex / Haystack / LangChain hebben dit probleem **niet structureel opgelost** — allen gebruiken `metadata: dict[str, Any]` en mitigeren via consumer-side guards + integratietests, niet via producer-side schema enforcement. Mijn impliciete assumptie ("er is een industry standard fix") was te optimistisch.
- **Aanbevolen fixes (ranked, priority Low → Medium effort):**
  1. **Direct:** test `test_extra_payload_contract.py` die alle required fields assert voor crawl-, connector-, upload-pad. Vangt de volgende `content_label`-klasse-bug. Geen runtime overhead.
  2. **Volgende SPEC:** `EnrichmentPayload(TypedDict)` met `NotRequired` voor conditionele velden. Statische analyse (pyright) vangt ontbrekende assignments — de Klai pyright-CI bestaat al.
  3. **Later:** Pydantic-validatie aan begin van `_enrich_document()` — runtime ValidationError zichtbaar in Procrastinate job-status.
- **Afgeraden:** custom Procrastinate `json_dumps/loads` serializer — complexiteit van object_hook discrimination weegt niet op tegen de winst.
- **Risico-niveau:** HIGH (al meermaals gebeten, CRIT-pitfall, geen statische bewaking).

---

### Finding 4: `document_text` triple-duplicatie — Qdrant-kopie is dead weight

**Severity:** MED (storage cost, niet correctheid)
**Status:** Geverifieerd via code + `_ALLOWED_METADATA_FIELDS` analyse
**Code-locatie:**
- PG-kopie: [routes/ingest.py:417-418](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L417-L418)
- Procrastinate task-arg: [routes/ingest.py:540-549](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L540-L549)
- Qdrant payload (per chunk): [routes/ingest.py:470-471](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L470-L471) → `extra_payload.update` in [qdrant_store.py:160-170](../../klai-knowledge-ingest/knowledge_ingest/qdrant_store.py#L160-L170) en [:228-244](../../klai-knowledge-ingest/knowledge_ingest/qdrant_store.py#L228-L244)
- Read-side filter: [qdrant_store.py:362-369](../../klai-knowledge-ingest/knowledge_ingest/qdrant_store.py#L362-L369)
- Rebuild-consumer: [rebuild_tasks.py:241](../../klai-knowledge-ingest/knowledge_ingest/rebuild_tasks.py#L241)

**Observatie:**
Eén document-body komt terecht in **drie** stores:

| Locatie | Lezer | Status |
|---|---|---|
| `knowledge.artifacts.extra->>'document_text'` | `rebuild_tasks._reconstruct_document_text` (fallback) en hoofdpad | **Actief in gebruik** |
| Procrastinate `procrastinate_jobs.args` JSONB | `_enrich_document` als arg | **Actief in gebruik** |
| Qdrant payload van **elke** chunk | — | **Dead weight** |

`_ALLOWED_METADATA_FIELDS` ([qdrant_store.py:362-369](../../klai-knowledge-ingest/knowledge_ingest/qdrant_store.py#L362-L369)) filtert op read-tijd `document_text` (en `document_summary`) eruit. Niemand consumeert deze velden uit Qdrant. Voor een 100KB markdown met 50 chunks = ~5MB body-duplicatie in Qdrant per document.

**Vraag voor onderzoek:**
1. Wat is best-practice voor "raw document storage" naast vector
   embeddings?
2. Object storage (S3/Garage) vs PG `text` kolom vs Qdrant payload —
   welke kosten/latency tradeoff?
3. Hoe groot is de impact op Qdrant-collection-size bij 100K+ documents
   met deze duplicatie?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-4.md](research/finding-4.md))*

- **Verificatie + correctie:** alle drie storage-locaties bevestigd. **Verrassende vondst:** `document_text` staat **TWEEMAAL** in elke Procrastinate job-args row — één keer als top-level `document_text=req.content` parameter, één keer ingebed in `extra_payload`. Dus eigenlijk **vier** kopieën, niet drie. `document_summary` heeft hetzelfde patroon (Phase 2 plakt het in extra_payload → in elke chunk-payload). Retrieval-api codebase heeft **nul** referenties naar `document_text` of `document_summary` (grep returnde leeg).
- **Schaalimpact:** voor een 100KB doc met 50 chunks ~5 MB dead Qdrant-payload. Bij 100K docs ~250 GB verspilling — RAM bij Qdrant InMemory mode, SSD bij OnDisk.
- **Industry standard:** LangChain `Document.metadata`, LlamaIndex `Node.metadata`, Haystack `Document.meta` — chunk metadata bevat **nooit** de volledige parent-document text. Best-practice patroon: store-by-pointer (artifact_id + bytes_offset) of separate object store (S3/Garage met content-hash key). AWS RAG-architecture documenteert S3 als de canonical raw-document store, met vector store puur voor embeddings + lichte metadata.
- **Aanbevolen fixes (ranked):**
  1. **Rang 1 (zero-risk, direct):** strip `document_text` en `document_summary` uit `extra_payload` vóór de Qdrant upsert in `routes/ingest.py` en `enrichment_tasks.py`. Geen migratie nodig. Rebuild werkt onveranderd (leest uit PG), retrieval werkt onveranderd (al gefilterd via `_ALLOWED_METADATA_FIELDS`).
  2. **Rang 2:** dedupliceer `document_text` in Procrastinate task-args — geef alleen via `extra_payload` óf alleen als top-level arg, niet beide.
  3. **Rang 3 (lange termijn):** migreer raw document storage naar Garage S3 met content-hash keys; PG `extra` houdt alleen het S3-key + hash. Past in dezelfde patroon als de bestaande `artifact_images` bookkeeping.
- **Risico-niveau:** MED-cost, LOW-correctheid. Geen bug, wel signifcante storage-overhead op schaal.

---

### Finding 5: Centroid-classification-failure logt op `debug` — silent fallback

**Severity:** MED (observability)
**Status:** Direct geverifieerd in code
**Code-locatie:** [routes/ingest.py:362-364](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L362-L364)

**Observatie:**
```python
except Exception:
    logger.debug("centroid_lookup_failed", exc_info=True)
```

VictoriaLogs default level is INFO; debug-events zijn onzichtbaar.
Een corrupte centroid-blob, netwerkfout, numpy-versie-mismatch of
hashing-key collision leidt stilletjes tot het terugvallen op de
duurdere LLM-classificatie. Past in de `silent-degrade` familie van
pitfalls (`fail-open-auth` / `empty-secret-fail-open`).

**Vraag voor onderzoek:**
1. Welke log-levels horen bij "designed fallback" vs "unexpected
   degradation"?
2. Wat is best-practice voor observability van fast/slow tier RAG
   classification?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-5.md](research/finding-5.md))*

- **Verificatie:** alle claims bevestigd. Exception scope is volledige fast-path (`load_centroids` + TEI embed + `classify_by_centroid`); productie log-floor is INFO ([logging_setup.py:62](../../klai-knowledge-ingest/knowledge_ingest/logging_setup.py#L62)); geen Grafana alert dekt dit (`obs-001-ingest-error-rate-elevated` query't alleen op `level:error`).
- **Vergeleken met andere debug-in-except in deze service:** drie andere zijn acceptabel (user-supplied YAML/datetime/LLM-detection); de centroid-case is de enige die infrastructure-failure swallows.
- **Industry standard:** WARNING is canonical voor "fast path failed, fell back to slow path". Refs: Better Stack log-levels guide, structlog exception docs, Google SRE-book monitoring chapter, Langfuse RAG observability. DEBUG is voor "implementation details niet verwacht in productie log-streams" — dat past niet voor een silent-fail van een infrastructure component.
- **Aanbevolen fix (minimaal):** `logger.debug → logger.warning` met `exc_info=True, org_id, kb_slug` als structured fields. Eén regelwijziging.
- **Aanbevolen follow-up:** counter `centroid_error_total` + Grafana alert in `ingest-rules.yaml` op count > 5 in 10m, mirrorend bestaande `obs-001-ingest-error-rate-elevated`.
- **Risico:** subsystem kan stilletjes maandenlang broken zijn — tenzij iemand een spike in `klai-fast` LLM-cost correleert. VictoriaLogs 30d-retentie betekent retrospective diagnose is na >30d onmogelijk. Bevestigt `silent-degrade` familie pitfall.

---

### Finding 6: TEI retry-budget = 7s; Phase 1 raist na uitputting

**Severity:** MED (availability, contained)
**Status:** Direct geverifieerd in code
**Code-locatie:** [embedder.py:22-60](../../klai-knowledge-ingest/knowledge_ingest/embedder.py#L22-L60)

**Observatie:**
- Retry-budget: 3 attempts met `2**attempt` backoff = 1s + 2s + 4s = 7s
- Faalt op: `ReadTimeout`, `ConnectTimeout`, HTTP 5xx
- Bij uitputting: re-raise van `last_exc` → bubblet door naar
  `ingest_document` → faalt het hele Phase 1
- Outer timeout: `httpx.AsyncClient(timeout=settings.tei_timeout)` — geen
  zichtbare default in deze module

**Impact:**
- **Direct POST**: client krijgt 5xx → caller's verantwoordelijkheid om
  te retryen (klai-connector kan dat, MCP fire-and-forget niet)
- **Crawl-task** (`run_crawl_job`): Procrastinate retry vangt op, maar
  vereist dat het op een retry-enabled queue zit
- **Bulk re-embed** in Phase 2: zit ook in dezelfde retry-loop

Een TEI-restart of OOM-blip (>7s) faalt elke ingest in dat venster.

**Vraag voor onderzoek:**
1. Wat zijn typische retry-strategieen voor embed-services in productie
   RAG-pipelines (Cohere, OpenAI, Voyage, self-hosted TEI)?
2. Circuit-breaker pattern voor embed-services — wanneer gerechtvaardigd?
3. Hoe groot is een typische TEI-blip bij gpu-01-werkload?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-6.md](research/finding-6.md))*

- **Verificatie + correctie van mijn frame:** mijn "7s budget" was de **sleep-tijd**. Echte wall-time worst case is `3 × tei_timeout` (default ~120s) + 7s sleep — dus bij hangende verbindingen juist **te lang** voor een sync HTTP-endpoint. Bij snelle 5xx (TEI restart) is 7s daarentegen **te kort** — TEI container + BGE-M3 model reload duurt typisch 15–45s.
- **Cruciale vondst:** **geen jitter** in de backoff (`2**attempt`, deterministisch). Bij bulk-sync van 50 pagina's slaan alle calls tegelijk t=1s, t=3s, t=7s — thundering-herd op TEI tijdens recovery.
- **Caller-gedrag bij definitieve faal:**
  - Direct POST → HTTP 500 naar caller, geen catch
  - Gitea-webhook → `logger.warning`, pagina stil overgeslagen
  - Bulk sync → exception per pagina, stil overgeslagen
  - Crawl-task → `max_attempts=1` in Procrastinate (Procrastinate-retry compenseert NIET)
- **Aanbevolen fixes (ranked):**
  1. Voeg full jitter toe: `random.uniform(0, min(2**attempt, 30))`
  2. Verhoog van 3 naar 5 pogingen (dekt het 60s-window van TEI-restart)
  3. Voeg `stop_after_delay` (Tenacity-pattern) toe als wall-time vangnet
  4. Log embed-failures op ERROR-niveau (zodat ze door `obs-001-ingest-error-rate-elevated` opgepikt worden)
  5. Circuit breaker — nog niet nodig op huidige schaal
- **Risico-niveau:** MED. Combineert met Finding 5 (silent-degrade) — bulk-sync pagina's worden stil overgeslagen bij elke TEI-blip.

---

### Finding 7: Geen UNIQUE constraint op `(org_id, kb_slug, path) WHERE belief_time_end > now()` op `knowledge.artifacts`

**Severity:** MED (race-window onder concurrent-ingest)
**Status:** Bevestigd via alembic baseline lezen
**Code-locatie:**
- Schema: [alembic/versions/0001_baseline.py:104-140](../../klai-knowledge-ingest/alembic/versions/0001_baseline.py#L104-L140) (table create) en [:457-480](../../klai-knowledge-ingest/alembic/versions/0001_baseline.py#L457-L480) (UNIQUE-sectie)
- Indexes: [:572-588](../../klai-knowledge-ingest/alembic/versions/0001_baseline.py#L572-L588) (niet-unieke btree op `(org_id, kb_slug, path)` en `(..., belief_time_end)`)
- Race-window: [routes/ingest.py:283-440](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L283-L440) (content_hash check + soft_delete + create_artifact)

**Observatie:**
De UNIQUE-sectie van de baseline-migratie heeft alleen:
- `crawled_pages_uniq UNIQUE (org_id, kb_slug, url)`
- `page_links_uniq UNIQUE (org_id, kb_slug, from_url, to_url)`

`knowledge.artifacts` heeft alleen single-PK op `id` (uuid) en niet-unieke
btree-indexes op `(org_id, kb_slug, path)`. Twee concurrent
`ingest_document` calls met identieke `(org_id, kb_slug, path)`:

1. Beide `get_active_content_hash` → null
2. Beide `soft_delete_artifact` → idempotent
3. Beide `create_artifact` → twee actieve rijen voor zelfde pad

De content-hash short-circuit ([routes/ingest.py:283-287](../../klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L283-L287)) helpt alleen bij **niet-concurrent** re-ingests.

In de praktijk zal de soft-delete-pattern (`belief_time_end < now()`) niet
fataal falen — maar de "active artifact" voor `(org, kb, path)` is niet
meer uniek, en consumers die `ORDER BY created_at DESC LIMIT 1` doen
kunnen beide rijen op verschillende momenten lezen.

**Vraag voor onderzoek:**
1. Wat is de standaard-patroon voor "soft-delete + nieuwe versie" in
   Postgres met UNIQUE-garantie?
2. Partial unique index `WHERE belief_time_end >= ${MAX_BIGINT}` — is dat
   de juiste fix?
3. Hoe doen event-sourced ingest pipelines (Kafka-Connect, Debezium) dit?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-7.md](research/finding-7.md))*

- **Verificatie:** claim volledig bevestigd. UNIQUE-blok in `0001_baseline.py` (regels 461-481) dekt alleen `crawled_pages_uniq` en `page_links_uniq`. `knowledge.artifacts` heeft alleen single-PK op `id` + 4 niet-unieke btree-indexes.
- **Soft-delete mechanisme:** "actief" = strict `belief_time_end = 253402300800` (sentinel = jaar ~9999). `soft_delete_artifact` zet dit veld op `int(time.time())`. **Geen transactie omsluit de drie stappen** (`get_active_content_hash` → `soft_delete_artifact` → `create_artifact`); embeddings + LLM-calls zitten ertussen, wat het race-venster verbreedt naar **1-2 seconden**.
- **Concurrent tests ontbreken:** geen enkele test creëert twee gelijktijdige requests voor hetzelfde pad. De bestaande dedup-tests testen alleen Procrastinate `AlreadyEnqueued`, niet de DB-laag.
- **Meest waarschijnlijke trigger:** connector met overlappende sync-runs (bv. handmatige "sync nu" terwijl een geplande sync loopt). `connector_is_active`-guard blokkeert alleen bij `state=deleting`, niet bij parallelle actieve syncs.
- **Aanbevolen fix:** partial unique index via nieuwe Alembic-migratie:
  ```sql
  CREATE UNIQUE INDEX CONCURRENTLY uq_artifacts_active_path
      ON knowledge.artifacts (org_id, kb_slug, path)
      WHERE belief_time_end = 253402300800;
  ```
  Gecombineerd met `UniqueViolationError`-handling in `pg_store.create_artifact`. Pre-migratie: query op bestaande dubbele actieve rijen om te zien of er al schade is.
- **Risico-niveau:** MED. Geen data-loss, wel "active artifact for path" niet-meer-uniek + downstream readers (`ORDER BY created_at DESC LIMIT 1`) kunnen verschillende rijen op verschillende momenten lezen.

---

### Finding 8: Markdown-chunker is regex-naïef voor code-blocks

**Severity:** LOW (kwaliteit, niet correctheid)
**Status:** Direct geverifieerd in code
**Code-locatie:** [chunker.py:53-103](../../klai-knowledge-ingest/knowledge_ingest/chunker.py#L53-L103)

**Observatie:**
`_split_by_headings` gebruikt `re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)` op de bare body. Dit detecteert **elke** regel die met `#` begint, ongeacht context. Voor Python comments (`# this is a comment`), shell scripts (`#!/usr/bin/env bash`), of fenced code-blocks met markdown-headings binnenin (zelden) wordt dit ten onrechte als heading geparsed. Resultaat: `heading_path` op child chunks bevat code-fragments — embedder krijgt ruis.

`_split_by_size` valt terug op `\n\n` paragraph-break of `. ` sentence-break. Geen code-block awareness — een chunk kan halverwege een ` ``` ` block eindigen, met onevenwichtige fences in zowel parent- als child-tekst.

**Vraag voor onderzoek:**
1. Welke markdown-chunkers zijn industry-standard in 2026 (LangChain
   `MarkdownHeaderTextSplitter`, LlamaIndex `MarkdownNodeParser`,
   `markdown-it-py` AST-based)?
2. Hoe groot is de kwaliteitsimpact bij code-heavy KB content (github-
   adapter, technical docs)?
3. Wat is het kostenverschil tussen pure regex en AST-based parsing per
   document?

**Onderzoeksbevindingen:** *(volledige rapportage: [research/finding-8.md](research/finding-8.md))*

- **Verificatie:** claim volledig bevestigd. `_split_by_headings` heeft géén state-tracking voor fenced code blocks. Reproductie: een README met ` ```python\n# connect to the server\n``` ` wordt letterlijk gesplitst op die comment-regel — de comment-tekst belandt als `heading_path` in Qdrant. Tests in `test_chunker_parent_child.py` bevatten **geen enkele** test met fenced code blocks of code comments. Ragas eval-suites testen alleen klantgerichte proza — geen signaal voor github-connector content.
- **Industry standard 2026 (4 opties, oplopende complexiteit):**
  - **LangChain `ExperimentalMarkdownSyntaxTextSplitter`** — sequentieel state-tracking (geen AST; well-maintained, minimale deps)
  - **LlamaIndex `MarkdownNodeParser`** — mistune AST; correct by construction
  - **mistune v3 direct** — pure Python, ~50KB, CommonMark-compliant; meest lightweight correcte aanpak
  - **Unstructured.io** — typet elementen als `CodeSnippet` apart; meest robuust maar zwaarste deps (~350MB)
- **Aanbevolen fixes (ranked):**
  1. **Option A (HIGH priority, low effort):** voeg `in_code_block` boolean toe aan line-iterator in `_split_by_headings`. Eén lokale wijziging, **nul nieuwe deps**. Vangt 90% van de regex-naïveteit.
  2. **Option D (HIGH priority, onafhankelijk):** voeg pytest-fixtures met code blocks toe + `eval/suites/github.yaml`. Maakt de bug **meetbaar** in ragas-runs.
  3. **Option B (MED):** maak code blocks atomisch in `_split_by_size` — geen splits halverwege een functie.
  4. **Option C (LOW priority, structureel):** migreer naar mistune AST-walking. Correctheid by construction, maar grotere refactor.
- **Risico-niveau:** LOW-MED (kwaliteit, niet correctheid). Impact schaalt met github-adapter content-volume.

---

## Doc-coverage check (fase 3, niet nu)

In fase 3 vergelijken we deze findings met:

- `docs/architecture/knowledge-ingest-flow.md` (1359 regels)
- `docs/architecture/klai-knowledge-architecture.md` (1578 regels)
- `docs/research/knowledge-pipeline-architecture.md` (2592 regels)
- `docs/research/knowledge-system-fundamentals.md` (1270 regels)

Per finding: documenteert het bestaande doc dit gedrag? Klopt het met de
huidige code? Wat moet aangepast worden?

**Niet nu** — eerst code-grounded analyse afmaken.

---

## Volgende stappen

1. **Fase 2 — onderzoek (parallel agents):** per finding één agent met opdracht:
   - Verifieer mijn observatie tegen de code (independent check)
   - Onderzoek industry-standard / best-practice (WebSearch + Context7
     waar relevant — Procrastinate, Qdrant, RAG-pipeline pattern docs)
   - Rapporteer in vast format: `[verificatie / huidige gedrag / industry standard / fix-aanbevelingen / risico-niveau / referenties]`
2. **Fase 3 — integratie:** ik vul de "Onderzoeksbevindingen" sectie per
   finding in en doe doc-coverage check tegen de bestaande architecture-docs
3. **Fase 4 — besluit met Mark:** welke findings worden SPEC, welke worden
   pitfall-entry, welke laten we als-is met ratio gedocumenteerd?

---

## Verandering t.o.v. eerste review

Mijn initiële review (in chat, voor verificatie) bevatte twee
overgeneraliseringen die ik hier corrigeer:

| Origineel (chat) | Na code-verificatie |
|---|---|
| "AlreadyEnqueued laat user-edits verdampen" | Alleen voor direct-POST flow. Gitea-webhook is veilig door `schedule_in` + re-fetch. |
| "document_text 3× gedupliceerd, allemaal dood" | PG-kopie en task-arg zijn actief in gebruik (rebuild_kb resp. enrichment). Alleen Qdrant-payload-kopie is dead weight. |

Reden voor verschuiving: ik leunde aanvankelijk op afgeleide claims; pas
na het lezen van `gitea_webhook`, `ingest_from_gitea`, `rebuild_tasks`
en `_ALLOWED_METADATA_FIELDS` werd de scope helder. Goede les voor de
adversarial-at-high-confidence rule: claims pas hard maken na code-trace.
