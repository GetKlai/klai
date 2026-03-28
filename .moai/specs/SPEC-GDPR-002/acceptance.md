---
id: SPEC-GDPR-002
document: acceptance
version: 1.0.0
created: 2026-03-28
updated: 2026-03-28
---

# SPEC-GDPR-002: Acceptatiecriteria -- Vexa Recording Cleanup

## Scenario's

### S1: Opname wordt verwijderd na succesvolle transcriptie via webhook

**Traceability:** SPEC-GDPR-002-R1, SPEC-GDPR-002-R3

**Given** een VexaMeeting met `status="processing"` en een geldig `vexa_meeting_id`
**And** de vexa-bot-manager container draait en is bereikbaar via Docker socket proxy
**And** er bestaan opnamebestanden in `/var/lib/vexa/recordings/{vexa_meeting_id}`
**When** de Vexa webhook `run_transcription()` triggert en de transcriptie slaagt
**And** `db.commit()` slaagt met `status="done"`
**Then** het systeem voert `delete_recording(vexa_meeting_id)` uit
**And** de opnamebestanden zijn verwijderd uit de container
**And** `meeting.recording_deleted` is `True`
**And** `meeting.recording_deleted_at` bevat het huidige tijdstip
**And** een info-logregel is geschreven met `result="deleted"`

### S2: Opname wordt verwijderd na transcriptie via bot_poller

**Traceability:** SPEC-GDPR-002-R1, SPEC-GDPR-002-R3

**Given** een VexaMeeting met een actieve bot-status in Vexa
**And** de bot heeft de meeting verlaten (gedetecteerd door bot_poller)
**When** bot_poller `run_transcription()` triggert en de transcriptie slaagt
**And** `db.commit()` slaagt met `status="done"`
**Then** het systeem voert `cleanup_recording()` uit
**And** de opnamebestanden zijn verwijderd
**And** `meeting.recording_deleted` is `True`

### S3: Transcriptiepipeline faalt niet bij mislukte verwijdering

**Traceability:** SPEC-GDPR-002-R4

**Given** een VexaMeeting met `status="processing"` en een geldig `vexa_meeting_id`
**And** de vexa-bot-manager container is NIET bereikbaar (gestopt of netwerk-probleem)
**When** `run_transcription()` slaagt en `status="done"` wordt gezet
**And** `cleanup_recording()` wordt aangeroepen
**Then** de verwijderpoging faalt met een Docker-fout
**And** de meeting-status blijft `"done"` (NIET gewijzigd naar "failed")
**And** `meeting.recording_deleted` blijft `False`
**And** `meeting.recording_deleted_at` blijft `None`
**And** een warning-logregel is geschreven met `result="failed"` en het foutbericht
**And** de transcript_text en transcript_segments zijn correct opgeslagen

### S4: delete_recording() voert exec_run uit in vexa-bot-manager

**Traceability:** SPEC-GDPR-002-R2

**Given** de vexa-bot-manager container draait
**And** Docker socket proxy is geconfigureerd met `POST=1, CONTAINERS=1`
**When** `delete_recording(vexa_meeting_id=42)` wordt aangeroepen
**Then** het systeem verkrijgt de vexa-bot-manager container via Docker SDK
**And** voert `exec_run(["rm", "-rf", "/var/lib/vexa/recordings/42"])` uit
**And** de aanroep vindt plaats via `asyncio.to_thread()` (niet-blokkerend)
**And** retourneert `True` als exit code 0

### S5: delete_recording() handelt ontbrekend pad graceful af

**Traceability:** SPEC-GDPR-002-R2, SPEC-GDPR-002-R4

**Given** de vexa-bot-manager container draait
**And** het pad `/var/lib/vexa/recordings/99` bestaat NIET (opname al verwijderd of nooit aangemaakt)
**When** `delete_recording(vexa_meeting_id=99)` wordt aangeroepen
**Then** `rm -rf` retourneert exit code 0 (idempotent)
**And** de methode retourneert `True`
**And** `meeting.recording_deleted` wordt op `True` gezet

### S6: Achtergrondtaak vindt en verwijdert oude opnames

**Traceability:** SPEC-GDPR-002-R5

**Given** drie VexaMeeting records:
  - Meeting A: `status="done"`, `recording_deleted=False`, `created_at` = 2 uur geleden
  - Meeting B: `status="done"`, `recording_deleted=False`, `created_at` = 5 minuten geleden
  - Meeting C: `status="done"`, `recording_deleted=True`, `created_at` = 3 uur geleden
**When** `recording_cleanup_loop()` draait
**Then** het systeem probeert Meeting A te verwijderen (ouder dan 30 min, niet verwijderd)
**And** het systeem slaat Meeting B over (jonger dan 30 min)
**And** het systeem slaat Meeting C over (al verwijderd)

### S7: Achtergrondtaak blijft draaien na individuele fout

**Traceability:** SPEC-GDPR-002-R5, SPEC-GDPR-002-R4

**Given** twee VexaMeeting records die in aanmerking komen voor cleanup
**And** de verwijdering van het eerste record faalt (container tijdelijk onbereikbaar)
**When** `recording_cleanup_loop()` beide records verwerkt
**Then** de fout voor het eerste record wordt gelogd
**And** het tweede record wordt nog steeds verwerkt
**And** de loop crashed NIET en draait door na de geconfigureerde interval

### S8: Audit logging bij succes en falen

**Traceability:** SPEC-GDPR-002-R6

**Given** een verwijderpoging voor een meeting
**When** de verwijdering slaagt
**Then** een structlog info-bericht wordt geschreven met:
  - `vexa_meeting_id` (int)
  - `meeting_id` (UUID als string)
  - `result="deleted"`

**Given** een verwijderpoging voor een meeting
**When** de verwijdering faalt
**Then** een structlog warning-bericht wordt geschreven met:
  - `vexa_meeting_id` (int)
  - `meeting_id` (UUID als string)
  - `result="failed"`
  - `error` (foutmelding als string)

### S9: docker-compose.yml bevat geen recording volume mount

**Traceability:** SPEC-GDPR-002-R7

**Given** het bestand `deploy/docker-compose.yml`
**When** de vexa-bot-manager service volumes worden gecontroleerd (regels 795-801)
**Then** het `vexa-recordings-data` volume is NIET aanwezig als mount
**And** er wordt GEEN wijziging aangebracht aan docker-compose.yml

### S10: Guard voorkomt dubbele verwijdering

**Traceability:** SPEC-GDPR-002-R1, SPEC-GDPR-002-R3

**Given** een VexaMeeting met `status="done"` en `recording_deleted=True`
**When** `cleanup_recording()` wordt aangeroepen (door webhook, poller of achtergrondtaak)
**Then** de functie retourneert onmiddellijk zonder `delete_recording()` aan te roepen
**And** er wordt GEEN Docker exec uitgevoerd
**And** er wordt GEEN logregel geschreven

### S11: Guard bij ontbrekend vexa_meeting_id

**Traceability:** SPEC-GDPR-002-R1

**Given** een VexaMeeting met `status="done"` en `vexa_meeting_id=None`
**When** `cleanup_recording()` wordt aangeroepen
**Then** de functie retourneert onmiddellijk zonder verwijderpoging
**And** er wordt GEEN foutmelding gegenereerd

### S12: Meeting nog in processing wordt overgeslagen

**Traceability:** SPEC-GDPR-002-R1

**Given** een VexaMeeting met `status="processing"` (transcriptie nog bezig)
**When** `cleanup_recording()` wordt aangeroepen
**Then** de functie retourneert onmiddellijk
**And** de opnamebestanden blijven intact voor eventuele Whisper-fallback

---

## Performance-criteria

| Criterium                                      | Drempel                        |
|------------------------------------------------|--------------------------------|
| `delete_recording()` uitvoertijd               | < 5 seconden per aanroep       |
| Impact van cleanup_loop op normale operaties   | Geen merkbare latency-toename  |
| Cleanup loop interval                          | Elke 5 minuten                 |
| Maximale leeftijd onverwijderde opname         | 30 minuten (bij werkende cleanup) |
| Event loop blocking door Docker SDK            | 0 ms (via asyncio.to_thread)   |

---

## Quality gate criteria

- [ ] Alle 12 scenario's zijn geimplementeerd en getest
- [ ] `recording_deleted` en `recording_deleted_at` velden bestaan in het model
- [ ] Alembic-migratie is gegenereerd en gereviewed
- [ ] `delete_recording()` methode bestaat op VexaClient
- [ ] `cleanup_recording()` helper-functie geintegreerd in webhook en poller
- [ ] `recording_cleanup_loop()` geregistreerd in FastAPI lifespan
- [ ] Structlog audit logging aanwezig voor succes en falen
- [ ] Geen gebruik van `asyncio.to_thread()` overgeslagen (geen blocking Docker calls op event loop)
- [ ] docker-compose.yml ongewijzigd (geen volume mount toegevoegd)
- [ ] Unit tests voor `delete_recording()`, `cleanup_recording()` en `recording_cleanup_loop()`
- [ ] Transcriptiepipeline test: verwijderfout veroorzaakt GEEN pipeline-failure

---

## Definition of Done

1. Alle requirements (R1-R7) zijn geimplementeerd
2. Alle acceptatiescenario's (S1-S12) zijn geverifieerd
3. Alembic-migratie is gegenereerd en succesvol uitgevoerd op dev-database
4. Unit tests dekken: succesvolle verwijdering, gefaalde verwijdering, guard checks, achtergrondtaak
5. Structlog audit trail is zichtbaar in container logs
6. Code review door expert-backend is afgerond
7. CI pipeline is groen na push
