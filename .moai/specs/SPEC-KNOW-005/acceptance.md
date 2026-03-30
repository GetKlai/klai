# SPEC-KNOW-005: Acceptance Criteria

**SPEC ID:** SPEC-KNOW-005
**Titel:** Human Feedback Loop & Zelflerende Kennis

---

## Module F: Feedback Capture

### F-AC-1: Feedback opslaan via endpoint

**Given** een bestaand artifact met id `art-123` in org `org-456`
**And** een ingelogde gebruiker met user_id `usr-789` in dezelfde org
**When** de gebruiker een `POST /feedback` stuurt met:
```json
{
  "artifact_id": "art-123",
  "chunk_id": "chunk-abc",
  "signal": "helpful",
  "query": "Hoe werkt de onboarding?",
  "ranking_position": 1,
  "org_id": "org-456",
  "user_id": "usr-789"
}
```
**Then** retourneert het endpoint `201 Created` met een gegenereerd feedback-id
**And** het feedbackrecord is opgeslagen in `knowledge.feedback`

### F-AC-2: Cross-tenant feedback wordt geweigerd

**Given** een artifact met id `art-123` in org `org-456`
**When** een gebruiker uit org `org-999` een `POST /feedback` stuurt voor `art-123` met `org_id: "org-999"`
**Then** retourneert het endpoint `404 Not Found`
**And** er wordt geen feedbackrecord opgeslagen

### F-AC-3: Feedback context in done event

**Given** een chat-verzoek dat 3 chunks retourneert met artifact_ids `[a1, a2, a3]`
**When** het `/chat` endpoint het `done` SSE-event streamt
**Then** bevat het event een `feedback_context` array met 3 objecten
**And** elk object bevat `artifact_id`, `chunk_id`, en `ranking_position`

### F-AC-4: Ongeldige signal waarde wordt geweigerd

**Given** een geldig feedback-verzoek
**When** het `signal` veld de waarde `"neutral"` bevat (niet `helpful` of `unhelpful`)
**Then** retourneert het endpoint `422 Unprocessable Entity`

---

## Module C: Confidence Aggregatie

### C-AC-1: High confidence suggestie bij positieve feedback

**Given** een artifact `art-123` met `confidence = "medium"`
**And** 6 feedback-records met `signal = "helpful"` voor dit artifact
**And** 0 feedback-records met `signal = "unhelpful"`
**When** de `reflect-confidence` taak draait voor `art-123`
**Then** bevat `artifacts.extra` een `confidence_suggestion` met `proposed = "high"`
**And** het `confidence` veld op het artifact zelf is NIET gewijzigd

### C-AC-2: Low confidence suggestie bij negatieve feedback

**Given** een artifact `art-456` met `confidence = "medium"`
**And** 4 feedback-records met `signal = "unhelpful"` voor dit artifact
**And** 1 feedback-record met `signal = "helpful"`
**When** de `reflect-confidence` taak draait voor `art-456`
**Then** bevat `artifacts.extra` een `confidence_suggestion` met `proposed = "low"`
**And** het `confidence` veld op het artifact zelf is NIET gewijzigd
**And** het `assertion_mode` veld is NIET gewijzigd

### C-AC-3: Geen suggestie bij onvoldoende feedback

**Given** een artifact `art-789` met 2 feedback-records
**When** de `reflect-confidence` taak draait voor `art-789`
**Then** bevat `artifacts.extra` GEEN `confidence_suggestion`

### C-AC-4: Reflect taak wordt getriggerd na N feedbacks

**Given** de configuratie `reflect_feedback_threshold = 5`
**And** een artifact met 4 bestaande feedbackrecords
**When** een 5e feedback-record wordt opgeslagen via `POST /feedback`
**Then** wordt een `reflect-confidence` taak gescheduled op de `enrich-reflect` queue
**And** het response van het feedback-endpoint is niet vertraagd (fire-and-forget)

---

## Module D: Contradictie-detectie bij Ingest

### D-AC-1: Contradictie gedetecteerd bij conflicterende assertion_modes

**Given** een bestaand artifact `art-old` met `assertion_mode = "factual"` en embedding in Qdrant
**And** een nieuw artifact `art-new` met `assertion_mode = "hypothesis"`
**When** `art-new` wordt ge-ingest en enriched
**And** de Qdrant similarity search retourneert `art-old` met similarity 0.91
**Then** bevat `art-new.extra.contradiction_candidates` een entry met `art-old` id en similarity 0.91
**And** bevat `art-old.extra.contradiction_candidates` een entry met `art-new` id en similarity 0.91
**And** een logmelding wordt geschreven met artifact_ids en similarity score

### D-AC-2: Geen contradictie bij zelfde assertion_mode

**Given** een bestaand artifact `art-old` met `assertion_mode = "factual"`
**And** een nieuw artifact `art-new` met `assertion_mode = "factual"`
**When** de similarity search `art-old` retourneert met similarity 0.92
**Then** bevat `art-new.extra` GEEN `contradiction_candidates`

### D-AC-3: Geen contradictie onder similarity drempel

**Given** een bestaand artifact `art-old` met `assertion_mode = "factual"`
**And** een nieuw artifact `art-new` met `assertion_mode = "hypothesis"`
**When** de similarity search `art-old` retourneert met similarity 0.78
**Then** bevat `art-new.extra` GEEN `contradiction_candidates`

### D-AC-4: Graceful degradation bij Qdrant onbeschikbaarheid

**Given** Qdrant is tijdelijk onbereikbaar
**When** een `reflect-contradiction` taak draait
**Then** wordt een WARNING gelogd met de foutmelding
**And** de taak faalt NIET fataal (geen retry-storm)
**And** het oorspronkelijke artifact wordt niet beïnvloed

### D-AC-5: Contradictie-detectie respecteert org_id isolatie

**Given** artifact `art-A` in `org-1` en artifact `art-B` in `org-2`
**And** beide hebben hoge similarity maar conflicterende assertion_modes
**When** de contradictie-check draait voor `art-A`
**Then** wordt `art-B` NIET als kandidaat gevonden (Qdrant filter op org_id)

---

## Module H: Source Diversity Health

### H-AC-1: Health endpoint retourneert correcte statistieken

**Given** een kennisbank `main` in org `org-123` met:
  - 100 artifacts: 60 observed, 30 extracted, 10 synthesized
  - Content types: 80 pdf, 15 html, 5 txt
  - Confidence: 40 high, 30 medium, 10 low, 20 null
**When** `GET /ingest/v1/kb/main/health?org_id=org-123` wordt aangeroepen
**Then** retourneert het endpoint:
  - `total_artifacts: 100`
  - `by_provenance_type: { "observed": 60, "extracted": 30, "synthesized": 10 }`
  - `by_content_type: { "pdf": 80, "html": 15, "txt": 5 }`
  - `confidence_coverage: { "high": 40, "medium": 30, "low": 10, "null": 20 }`

### H-AC-2: Source concentration warning

**Given** een kennisbank met 100 artifacts waarvan 85 `content_type = "pdf"`
**When** het health endpoint wordt aangeroepen
**Then** bevat `warnings` een object met `type: "source_concentration"`, `content_type: "pdf"`, en `percentage: 85.0`

### H-AC-3: Low confidence coverage warning

**Given** een kennisbank met 100 artifacts waarvan 55 `confidence = null`
**When** het health endpoint wordt aangeroepen
**Then** bevat `warnings` een object met `type: "low_confidence_coverage"` en `percentage: 55.0`

### H-AC-4: Geen warnings bij gezonde kennisbank

**Given** een kennisbank met diverse bronnen (geen type > 80%) en goede confidence dekking (< 50% null)
**When** het health endpoint wordt aangeroepen
**Then** is `warnings` een lege array

### H-AC-5: Lege kennisbank

**Given** een kennisbank zonder artifacts
**When** het health endpoint wordt aangeroepen
**Then** retourneert het endpoint `total_artifacts: 0` en lege distributies
**And** `warnings` is een lege array

---

## Quality Gates

- Alle nieuwe endpoints hebben unit tests met pytest-asyncio
- Alle database-interacties gebruiken parameterized queries (geen string interpolatie)
- Feedback endpoint heeft rate limiting overwegingen gedocumenteerd
- Alle nieuwe Procrastinate taken hebben idempotente error handling (geen retry-storm bij failures)
- Structured logging (structlog) voor alle nieuwe componenten
- Org_id isolatie wordt gevalideerd in elke test die cross-tenant scenarios raakt
