# SPEC-KNOW-005: Implementatieplan

**SPEC ID:** SPEC-KNOW-005
**Titel:** Human Feedback Loop & Zelflerende Kennis

---

## 1. Probleemstelling

Het Klai knowledge platform leert momenteel niet van gebruikersinteracties. Antwoorden worden gesynthetiseerd uit opgeslagen kennis, maar er is geen feedbackmechanisme waarmee het systeem kan leren welke kennis waardevol is en welke niet. Daarnaast ontbreekt proactieve kwaliteitsbewaking: tegenstrijdige informatie wordt niet gedetecteerd bij ingest, en er is geen zicht op de diversiteit van bronnen in een kennisbank.

Dit resulteert in drie concrete problemen:
1. **Geen leersignaal:** het systeem weet niet of antwoorden nuttig waren
2. **Stille tegenstrijdigheden:** conflicterende kennis kan naast elkaar bestaan zonder waarschuwing
3. **Blinde vlekken:** als 90% van een kennisbank uit een type bron komt, is er geen signalering

## 2. Module-overzicht met dependencies

```
Module F: Feedback Capture
   |
   v
Module C: Confidence Aggregatie  (afhankelijk van F)
   |
Module D: Contradictie-detectie  (onafhankelijk, deelt reflect queue met C)
   |
Module H: Source Diversity Health (onafhankelijk)
```

**Dependencies:**
- Module C hangt af van Module F (feedback-data nodig voor confidence-berekening)
- Module D deelt de `enrich-reflect` Procrastinate queue met Module C, maar is functioneel onafhankelijk
- Module H is volledig onafhankelijk en kan parallel worden gebouwd
- Alle modules hangen af van de bestaande `knowledge.artifacts` tabel (aanwezig)

## 3. Module F: Feedback Capture

### Wat moet gebouwd worden

1. **Database-migratie:** nieuwe `knowledge.feedback` tabel
2. **Feedback endpoint:** `POST /feedback` in retrieval-api
3. **Chat event uitbreiding:** `feedback_context` toevoegen aan het `done` SSE-event
4. **LiteLLM hook uitbreiding:** feedback doorsturen vanuit LibreChat naar retrieval-api (optioneel, afhankelijk van LibreChat integratiemogelijkheden)

### Welke files worden aangeraakt

| File | Actie |
|---|---|
| `deploy/postgres/migrations/002_feedback.sql` | **Nieuw** -- DDL voor knowledge.feedback tabel |
| `retrieval-api/retrieval_api/api/feedback.py` | **Nieuw** -- POST /feedback endpoint |
| `retrieval-api/retrieval_api/api/__init__.py` | **Wijzig** -- router registratie |
| `retrieval-api/retrieval_api/models.py` | **Wijzig** -- FeedbackRequest model toevoegen |
| `retrieval-api/retrieval_api/api/chat.py` | **Wijzig** -- feedback_context in done event |
| `retrieval-api/retrieval_api/services/synthesis.py` | **Wijzig** -- artifact_id + chunk_id + ranking_position meegeven aan done event |

### Technische aanpak

- De feedback-tabel wordt aangemaakt via een idempotente SQL-migratie (zelfde patroon als `001_knowledge_schema.sql`).
- Het `POST /feedback` endpoint valideert org_id-eigendom door een lookup op `knowledge.artifacts` te doen voordat het feedbackrecord wordt opgeslagen.
- Het `done` event in de SSE-stream krijgt een extra `feedback_context` veld met een array van `{ artifact_id, chunk_id, ranking_position }` objecten. Dit vereist dat `synthesis.py` de metadata doorgeeft aan het finale event.
- De database-interactie gebruikt asyncpg direct (zelfde patroon als `pg_store.py`), geen ORM.

### LibreChat integratie-onderzoek

Voordat Module F gebouwd wordt, moet onderzocht worden hoe LibreChat feedback-signalen kan versturen. Twee opties:

**Optie A (voorkeur):** LibreChat heeft een rating callback/webhook die we configureren naar `POST /feedback` via de LiteLLM hook.

**Optie B (fallback):** Als LibreChat geen native feedback-mechanisme biedt, bouwen we een klein proxy-endpoint in de LiteLLM post-call hook dat feedback opvangt via de LibreChat API/events.

Dit onderzoek is een prerequisite voor de exacte implementatie maar blokkeert niet het bouwen van het backend endpoint en de database.

## 4. Module C: Confidence Aggregatie

### Wat moet gebouwd worden

1. **Reflect queue registratie:** `enrich-reflect` queue in Procrastinate
2. **Reflect taak:** `reflect-confidence` taak die feedback-tellingen aggregeert
3. **Confidence update functie:** `update_artifact_confidence()` in pg_store
4. **Editorial inbox integratie:** suggesties schrijven naar `extra` JSONB als `confidence_suggestion`

### Welke files worden aangeraakt

| File | Actie |
|---|---|
| `deploy/knowledge-ingest/knowledge_ingest/reflect_tasks.py` | **Nieuw** -- reflect queue + taken |
| `deploy/knowledge-ingest/knowledge_ingest/enrichment_tasks.py` | **Wijzig** -- reflect queue registratie in init_app() |
| `deploy/knowledge-ingest/knowledge_ingest/pg_store.py` | **Wijzig** -- update_artifact_confidence(), get_feedback_stats() |
| `retrieval-api/retrieval_api/api/feedback.py` | **Wijzig** -- na elke N feedbacks een reflect-confidence taak schedulen |

### Technische aanpak

- De `enrich-reflect` queue wordt geregistreerd in `init_app()` naast de bestaande `enrich-interactive` en `enrich-bulk` queues.
- `reflect-confidence` is een Procrastinate taak die:
  1. Feedback-tellingen ophaalt voor een artifact (COUNT helpful, COUNT unhelpful)
  2. Drempelwaarden toepast (configureerbaar via `kb_config`)
  3. Bij drempeloverschrijding een `confidence_suggestion` schrijft naar `artifacts.extra` via `update_artifact_extra()`
- De suggestie bevat: `{ "confidence_suggestion": { "proposed": "high", "reason": "12 helpful, 1 unhelpful", "created_at": timestamp } }`
- Het systeem wijzigt `confidence` of `assertion_mode` NIET direct. De editorial inbox (toekomstige UI) leest deze suggesties uit `extra`.
- Na elke 5e feedback voor hetzelfde artifact wordt een `reflect-confidence` taak op de queue gezet. De teller wordt bijgehouden via een simpele `SELECT COUNT(*)` op `knowledge.feedback` (geen aparte counter nodig bij dit volume).

## 5. Module D: Contradictie-detectie bij Ingest

### Wat moet gebouwd worden

1. **Reflect taak:** `reflect-contradiction` taak in de reflect queue
2. **Qdrant similarity search:** hergebruik van bestaande `qdrant_store` voor nearest-neighbor lookup
3. **Contradictie-schrijflogica:** contradictie-kandidaten naar `extra` JSONB

### Welke files worden aangeraakt

| File | Actie |
|---|---|
| `deploy/knowledge-ingest/knowledge_ingest/reflect_tasks.py` | **Wijzig** -- reflect-contradiction taak toevoegen |
| `deploy/knowledge-ingest/knowledge_ingest/qdrant_store.py` | **Wijzig** -- search_similar_artifacts() functie toevoegen |
| `deploy/knowledge-ingest/knowledge_ingest/pg_store.py` | **Wijzig** -- get_artifact_assertion_mode(), update_artifact_extra() (al aanwezig) |
| `deploy/knowledge-ingest/knowledge_ingest/enrichment_tasks.py` | **Wijzig** -- na succesvolle enrichment een reflect-contradiction taak schedulen |

### Technische aanpak

- Na succesvolle enrichment (wanneer chunks ge-embed en in Qdrant staan) wordt een `reflect-contradiction` taak gescheduled op de `enrich-reflect` queue.
- De taak:
  1. Haalt de embedding van het nieuwe artifact op uit Qdrant
  2. Zoekt nearest neighbors met cosine similarity >= 0.85 binnen dezelfde org_id (filter op Qdrant payload)
  3. Voor elke match met hoge similarity: vergelijkt `assertion_mode` van het nieuwe artifact met dat van de match
  4. Bij conflict (bijv. `factual` vs `hypothesis`): schrijft `contradiction_candidates` naar `extra` van beide artifacts
- `contradiction_candidates` formaat: `[{ "artifact_id": "uuid", "similarity": 0.92, "assertion_mode": "hypothesis", "detected_at": timestamp }]`
- De detectie is bewust conservatief: alleen assertion_mode conflict bij hoge similarity triggert een kandidaat. Geen automatische actie.
- Logging: elk resultaat (0 kandidaten of N kandidaten) wordt gelogd met artifact_id, org_id, en similarity scores.

### Qdrant dependency

Deze module vereist dat artifacts als vectors in Qdrant staan. Dit is al het geval na enrichment (de `enrich-interactive`/`enrich-bulk` taken doen de Qdrant upsert). De `reflect-contradiction` taak runt na enrichment, dus de vectors zijn beschikbaar.

## 6. Module H: Source Diversity Health

### Wat moet gebouwd worden

1. **Health endpoint:** `GET /ingest/v1/kb/{kb_slug}/health`
2. **PostgreSQL aggregatie queries**

### Welke files worden aangeraakt

| File | Actie |
|---|---|
| `deploy/knowledge-ingest/knowledge_ingest/routes/health.py` | **Nieuw** -- health endpoint |
| `deploy/knowledge-ingest/knowledge_ingest/routes/__init__.py` | **Wijzig** -- router registratie |
| `deploy/knowledge-ingest/knowledge_ingest/pg_store.py` | **Wijzig** -- get_kb_health_stats() functie |

### Technische aanpak

- Puur PostgreSQL aggregatie, geen externe dependencies:
  ```sql
  SELECT provenance_type, content_type, confidence, COUNT(*)
  FROM knowledge.artifacts
  WHERE org_id = $1 AND kb_slug = $2 AND belief_time_end = 253402300800
  GROUP BY provenance_type, content_type, confidence
  ```
- De Python-code transformeert de resultaten naar het response-formaat en past de warning-drempels toe (80% concentratie, 50% null confidence).
- Geen caching nodig: de query is licht genoeg voor on-demand uitvoering.

## 7. Risicoanalyse

| Risico | Impact | Kans | Mitigatie |
|---|---|---|---|
| LibreChat biedt geen native feedback-mechanisme | Module F frontend-integratie vertraagd | Medium | Backend endpoint + done-event metadata worden gebouwd ongeacht. Frontend-koppeling via LiteLLM post-call hook als fallback. |
| Qdrant niet beschikbaar voor contradictie-detectie (SPEC-KNOW-004 niet afgerond) | Module D kan niet draaien | Laag | Module D is fire-and-forget; bij Qdrant-fout logt het een warning en skipt. Kan later alsnog draaien. |
| Feedback-volume te laag voor zinvolle confidence-signalen | Module C produceert geen suggesties | Medium | Drempelwaarden configureerbaar maken. Eventueel verlagen bij laag volume. |
| Contradictie-detectie genereert te veel false positives | Editorial inbox wordt onbruikbaar | Medium | Hoge similarity-drempel (0.85) en alleen assertion_mode conflict als trigger. Drempel configureerbaar. |
| Performance-impact van reflect-taken op Procrastinate workers | Enrichment vertraagd | Laag | Reflect queue is een aparte queue met lagere prioriteit dan enrich-interactive. Workers configureerbaar per queue. |

## 8. Implementatievolgorde

### Primair doel: Module F (Feedback Capture) + Module H (Source Diversity)

**Waarom eerst:**
- Module F is de fundering voor het leersignaal (Module C hangt ervan af)
- Module H is klein, onafhankelijk, en levert direct waarde op
- Beide kunnen parallel gebouwd worden

**Module F stappen:**
1. Database-migratie (`002_feedback.sql`)
2. `POST /feedback` endpoint in retrieval-api
3. `done` event uitbreiden met `feedback_context`
4. LibreChat integratie-onderzoek (parallel)

**Module H stappen:**
1. `get_kb_health_stats()` in pg_store
2. `GET /ingest/v1/kb/{kb_slug}/health` endpoint

### Secundair doel: Module C (Confidence Aggregatie)

**Waarom tweede:**
- Vereist dat feedback-data instroomt (Module F moet live zijn)
- De `enrich-reflect` queue die hier opgezet wordt, wordt hergebruikt door Module D

**Stappen:**
1. `enrich-reflect` queue registratie in Procrastinate
2. `reflect-confidence` taak implementatie
3. Confidence-suggestie schrijflogica
4. Trigger vanuit feedback endpoint (elke N-de feedback)

### Tertiair doel: Module D (Contradictie-detectie)

**Waarom laatst:**
- Functioneel het meest complex
- Hergebruikt de reflect queue uit Module C
- Vereist dat Qdrant similarity search betrouwbaar werkt (SPEC-KNOW-004)

**Stappen:**
1. `search_similar_artifacts()` in qdrant_store
2. `reflect-contradiction` taak implementatie
3. Trigger vanuit enrichment pipeline (na succesvolle enrichment)
4. Contradictie-kandidaten schrijven naar `extra` JSONB

---

## Architectuurbeslissingen

### Waarom `extra` JSONB in plaats van nieuwe tabellen voor suggesties?

De `extra` JSONB kolom bestaat al op `knowledge.artifacts` en wordt al gebruikt door `update_artifact_extra()`. Door suggesties (confidence, contradictie) in `extra` te schrijven:
- Geen nieuwe tabellen of migraties nodig voor de suggestie-opslag
- De editorial inbox kan alle suggesties ophalen met een enkele query op `extra`
- Het patroon is consistent met de bestaande codebase

### Waarom een aparte reflect queue?

Enrichment-taken (vectorisatie, LLM-verrijking) zijn zwaar en tijdgevoelig. Reflect-taken (aggregatie, similarity search) zijn lichter maar minder urgent. Door een aparte queue te gebruiken:
- Reflect-taken blokkeren nooit de enrichment-pipeline
- Workers kunnen per queue geconfigureerd worden (meer workers op enrich-interactive, minder op reflect)
- Bij overbelasting kan de reflect queue tijdelijk gepauzeerd worden zonder impact op ingest

### Waarom geen automatische assertion_mode wijziging?

Core principle van Klai: AI suggereert, mensen beslissen. Automatische wijziging van epistemische status zou:
- Het vertrouwen in het systeem ondermijnen
- Moeilijk te debuggen fouten introduceren
- In strijd zijn met de product-filosofie

Alle wijzigingen gaan via de editorial inbox als suggestie.
