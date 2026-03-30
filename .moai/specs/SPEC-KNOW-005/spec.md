# SPEC-KNOW-005: Human Feedback Loop & Zelflerende Kennis

**Status:** Planned
**Priority:** High
**Aangemaakt:** 2026-03-30

---

## Environment

- **Monorepo:** klai-mono
- **Knowledge stack:** knowledge-ingest (Procrastinate workers) + retrieval-api (FastAPI) + LiteLLM hook (`klai_knowledge.py`)
- **Database:** PostgreSQL met `knowledge` schema (artifacts, derivations, entities, embedding_queue)
- **Vector store:** Qdrant (migratie vanuit SPEC-KNOW-004)
- **LLM proxy:** LiteLLM met custom `KlaiKnowledgeHook`
- **Frontend:** LibreChat (chat UI)

## Assumptions

1. LibreChat biedt een mechanisme (plugin, custom endpoint, of post-message callback) om thumbs up/down signalen te versturen naar een extern endpoint. Indien niet: LiteLLM post-call hook vangt het af.
2. De `done`-event in de SSE stream van `/chat` bevat voldoende metadata (artifact_ids, chunk_ids, query) om feedback te koppelen aan specifieke chunks.
3. Procrastinate workers kunnen nieuwe queues registreren zonder schema-migratie van de Procrastinate-tabellen zelf.
4. Qdrant similarity search is beschikbaar voor contradictie-detectie (SPEC-KNOW-004 Qdrant-migratie is afgerond of parallel uit te voeren).

## Requirements

### Module F: Feedback Capture

- **[F-1]** WHEN een gebruiker thumbs-up of thumbs-down geeft op een chat-antwoord in LibreChat, THEN slaat het systeem een feedbackrecord op met: artifact_id, chunk_id, user_id, org_id, signal (helpful/unhelpful), query, ranking_position, en timestamp.
- **[F-2]** Het systeem biedt een `POST /feedback` endpoint in retrieval-api dat feedbackrecords accepteert en persisteert in `knowledge.feedback`.
- **[F-3]** WHEN het `/chat` endpoint een `done`-event streamt, THEN bevat dat event een `feedback_context` object met de artifact_ids, chunk_ids en ranking_positions van de gebruikte chunks, zodat de client feedback kan koppelen.
- **[F-4]** Het systeem valideert dat org_id in het feedbackverzoek overeenkomt met de org_id van de genoemde artifacts (geen cross-tenant feedback).

### Module C: Confidence Aggregatie

- **[C-1]** WHEN een artifact 5 of meer `helpful` feedback-signalen heeft ontvangen EN minder dan 20% `unhelpful`, THEN schrijft het systeem een suggestie naar de editorial inbox dat de confidence naar `high` kan worden gepromoveerd.
- **[C-2]** WHEN een artifact 3 of meer `unhelpful` feedback-signalen heeft ontvangen EN meer dan 60% `unhelpful`, THEN schrijft het systeem een suggestie naar de editorial inbox dat de confidence naar `low` moet worden verlaagd.
- **[C-3]** Het systeem wijzigt assertion_mode NOOIT automatisch. Alle statuswijzigingen vereisen menselijke goedkeuring via de editorial inbox.
- **[C-4]** De `reflect` Procrastinate queue verwerkt confidence-herberekeningen als fire-and-forget taak na elke N feedbacksignalen (configureerbaar, default: 5).

### Module D: Contradictie-detectie bij Ingest

- **[D-1]** WHEN een nieuw artifact wordt ge-ingest, THEN voert het systeem een fire-and-forget contradictie-check uit als Procrastinate taak op de `reflect` queue.
- **[D-2]** De contradictie-check zoekt via Qdrant similarity search naar bestaande artifacts met cosine similarity >= 0.85 binnen dezelfde org_id.
- **[D-3]** IF een gevonden artifact een conflicterende assertion_mode heeft (bijv. `factual` vs `hypothesis` over hetzelfde onderwerp), THEN schrijft het systeem `contradiction_candidates` naar het `extra` JSONB-veld van beide artifacts.
- **[D-4]** Contradictie-kandidaten zijn zichtbaar in de editorial inbox als review-item.
- **[D-5]** Het systeem logt contradictie-detectie resultaten (gevonden/niet-gevonden, similarity scores) voor monitoring.

### Module H: Source Diversity Health

- **[H-1]** Het systeem biedt een `GET /ingest/v1/kb/{kb_slug}/health` endpoint dat de bron-diversiteit per organisatie rapporteert.
- **[H-2]** Het health-endpoint retourneert: totaal aantal artifacts, verdeling per `provenance_type`, verdeling per `content_type`, en een `warnings` array.
- **[H-3]** IF een enkele `content_type` meer dan 80% van het totale corpus uitmaakt, THEN bevat de `warnings` array een `source_concentration` warning met het betreffende type en percentage.
- **[H-4]** IF meer dan 50% van de artifacts `confidence = null` heeft, THEN bevat de `warnings` array een `low_confidence_coverage` warning.

## Specifications

### Database: `knowledge.feedback` tabel

| Kolom | Type | Constraint |
|---|---|---|
| id | UUID | PRIMARY KEY |
| artifact_id | UUID | NOT NULL, FK -> knowledge.artifacts(id) |
| chunk_id | TEXT | NOT NULL (Qdrant point ID) |
| user_id | UUID | NOT NULL |
| org_id | UUID | NOT NULL |
| signal | TEXT | NOT NULL, CHECK IN ('helpful', 'unhelpful') |
| query | TEXT | NOT NULL |
| ranking_position | SMALLINT | NOT NULL |
| response_id | TEXT | Nullable, voor LibreChat message correlatie |
| created_at | BIGINT | NOT NULL |

Indexes: `idx_feedback_artifact` op (artifact_id), `idx_feedback_org` op (org_id, created_at).

### API: `POST /feedback` (retrieval-api)

Request body:
```
{
  "artifact_id": "uuid",
  "chunk_id": "string",
  "signal": "helpful" | "unhelpful",
  "query": "string",
  "ranking_position": int,
  "response_id": "string | null",
  "org_id": "uuid",
  "user_id": "uuid"
}
```
Response: `201 Created` met `{ "id": "uuid" }`

### API: `GET /ingest/v1/kb/{kb_slug}/health` (knowledge-ingest)

Response:
```
{
  "kb_slug": "string",
  "org_id": "uuid",
  "total_artifacts": int,
  "by_provenance_type": { "observed": int, ... },
  "by_content_type": { "pdf": int, ... },
  "confidence_coverage": { "high": int, "medium": int, "low": int, "null": int },
  "warnings": [
    { "type": "source_concentration", "content_type": "pdf", "percentage": 85.2 }
  ]
}
```

### Procrastinate: `enrich-reflect` queue

Nieuwe queue naast `enrich-interactive` en `enrich-bulk`. Twee taaktypes:
1. `reflect-confidence` -- herbereken confidence-suggesties op basis van feedback-tellingen
2. `reflect-contradiction` -- contradictie-detectie na ingest

---

## Traceability

| Requirement | Module | Files |
|---|---|---|
| F-1, F-2, F-4 | Feedback Capture | retrieval-api/api/feedback.py, pg_store_feedback.py |
| F-3 | Feedback Capture | retrieval-api/api/chat.py (done event uitbreiding) |
| C-1, C-2, C-3, C-4 | Confidence Aggregatie | knowledge-ingest/reflect_tasks.py, pg_store.py |
| D-1, D-2, D-3, D-4, D-5 | Contradictie-detectie | knowledge-ingest/reflect_tasks.py, qdrant_store.py, pg_store.py |
| H-1, H-2, H-3, H-4 | Source Diversity | knowledge-ingest/routes/health.py, pg_store.py |
