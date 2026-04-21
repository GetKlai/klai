---
id: SPEC-GDPR-002
document: plan
version: 1.1.0
created: 2026-03-28
updated: 2026-04-21
---

# SPEC-GDPR-002: Implementatieplan -- Vexa Recording Cleanup

> **⚠️ UPDATE 2026-04-21 (SEC-021 follow-up):** het oorspronkelijke plan
> leunde op `container.exec_run(["rm", "-rf", ...])` via de docker socket.
> Dat pad is **niet meer bruikbaar** — de `docker-socket-proxy` in front
> van portal-api weigert `/exec/*/start` (403). De referentie-implementaties
> in `provisioning.py` die naar dit patroon verwezen zijn zelf herschreven
> naar native protocol-clients (pymongo, redis). Zie
> `.claude/rules/klai/platform/docker-socket-proxy.md`. De onderstaande
> aanpak moet worden vervangen vóór implementatie start — zie
> "Alternatief ontwerp" hieronder.

## Technische aanpak (VERVALLEN — niet implementeren)

~~De verwijdering maakt gebruik van het bewezen Docker SDK-patroon uit
`provisioning.py` (regel 150-178). Portal-api heeft al Docker socket
proxy-toegang met de juiste permissies (`POST=1, CONTAINERS=1`). De
`exec_run()` methode wordt gebruikt om `rm -rf` uit te voeren binnen de
vexa-bot-manager container.~~

Doorgestreept omdat `exec_run()` vanuit portal-api altijd 403 geeft onder
de huidige proxy-config. Zelfs als we `EXEC=1` zouden toevoegen krijgen we
daarmee feitelijk een shell op iedere container op de host — niet
aanvaardbaar voor een `rm -rf` in een GDPR-verwijderpad.

## Alternatief ontwerp (verplicht vóór implementatie)

Kies één van onderstaande routes; beide vermijden `docker exec`.

### Optie A: klein HTTP-endpoint op vexa-bot-manager

Voeg op de vexa-bot-manager een intern endpoint toe (bijv.
`DELETE /internal/recordings/{vexa_meeting_id}`) dat is beschermd door
`X-Internal-Secret`. Portal-api roept dit via httpx aan; de
vexa-bot-manager doet de lokale `rm -rf`. Voordelen: dezelfde
auth-boundary als andere internal-to-internal calls, geen Docker SDK
nodig, testbaar zonder Docker.

### Optie B: shared volume mount

Mount `/var/lib/vexa/recordings` ook read-write in portal-api (of in een
dedicated cleanup-worker). Portal-api doet de `shutil.rmtree()` direct.
Voordelen: geen extra endpoint te onderhouden. Nadelen: coupling tussen
portal-api filesystem en vexa-bot-manager volumes; extra container met
write-access op opnames vergroot blast radius bij compromittering.

**Aanbeveling:** Optie A. Matcht het patroon dat al in gebruik is voor
knowledge-ingest, retrieval-api en mailer (internal-secret HTTP).
Portal-api blijft stateless t.o.v. de opname-storage.

Zie `.claude/rules/klai/platform/docker-socket-proxy.md` voor de
protocol-first regel en het overzicht van toegestane docker API-verbs.

### Integratie in transcriptiepipeline

De verwijdering wordt aangeroepen NADAT `run_transcription()` succesvol afrondt en de status op `"done"` staat. De aanroep vindt plaats in `meetings.py` op twee plekken:
1. **Webhook handler** (regel 631-632): na `await run_transcription(meeting, db)` en `await db.commit()`
2. **bot_poller** (regel 96-97): na `await run_transcription(m, db)` en `await db.commit()`

De verwijdering is fire-and-forget met error handling -- een failure mag de pipeline niet blokkeren.

---

## Taakdecompositie

### Milestone 1: Modeluitbreiding en migratie (Prioriteit Hoog)

**Taak 1.1:** Voeg `recording_deleted` en `recording_deleted_at` velden toe aan `VexaMeeting`

- Bestand: `klai-portal/backend/app/models/meetings.py`
- Wijziging: Twee nieuwe velden toevoegen aan de `VexaMeeting` class
  - `recording_deleted: Mapped[bool]` met `default=False, server_default="false"`
  - `recording_deleted_at: Mapped[datetime | None]` met `nullable=True`
- Traceability: SPEC-GDPR-002-R3

**Taak 1.2:** Genereer en review Alembic-migratie

- Commando: `alembic revision --autogenerate -m "add recording_deleted fields to vexa_meetings"`
- Validatie: Controleer dat de migratie alleen de twee nieuwe kolommen toevoegt
- Traceability: SPEC-GDPR-002-R3

### Milestone 2: delete_recording() methode (Prioriteit Hoog)

**Taak 2.1:** Implementeer `delete_recording()` op `VexaClient`

- Bestand: `klai-portal/backend/app/services/vexa.py`
- Nieuwe methode: `async def delete_recording(self, vexa_meeting_id: int) -> bool`
- Logica:
  1. Verkrijg Docker client via `docker.from_env()`
  2. Verkrijg container via `client.containers.get()` (containernaam uit settings of hardcoded)
  3. Voer `exec_run(["rm", "-rf", f"/var/lib/vexa/recordings/{vexa_meeting_id}"])` uit via `asyncio.to_thread()`
  4. Controleer exit code -- return `True` bij succes, `False` bij fout
  5. Log resultaat via structlog met `vexa_meeting_id` en `result`
- Foutafhandeling: Catch `docker.errors.NotFound` (container niet gevonden), `docker.errors.APIError` (exec mislukt), en generieke `Exception`
- Traceability: SPEC-GDPR-002-R2, SPEC-GDPR-002-R4, SPEC-GDPR-002-R6

**Taak 2.2:** Voeg `docker` dependency toe (indien nodig)

- Controleer of `docker` package al in `requirements.txt` staat (verwacht: ja, want `provisioning.py` gebruikt het)
- Traceability: SPEC-GDPR-002-R2

### Milestone 3: Integratie in transcriptiepipeline (Prioriteit Hoog)

**Taak 3.1:** Helper-functie voor verwijdering na transcriptie

- Bestand: `klai-portal/backend/app/api/meetings.py` (of nieuw bestand `klai-portal/backend/app/services/recording_cleanup.py`)
- Nieuwe functie: `async def cleanup_recording(meeting: VexaMeeting, db: AsyncSession) -> None`
- Logica:
  1. Guard: return als `meeting.status != "done"` of `meeting.vexa_meeting_id is None` of `meeting.recording_deleted is True`
  2. Roep `vexa.delete_recording(meeting.vexa_meeting_id)` aan
  3. Bij succes: set `meeting.recording_deleted = True`, `meeting.recording_deleted_at = datetime.now(UTC)`
  4. Bij fout: log warning, ga door (meeting-status blijft `"done"`)
  5. `await db.commit()`
- Traceability: SPEC-GDPR-002-R1, SPEC-GDPR-002-R4

**Taak 3.2:** Integreer in webhook handler

- Bestand: `klai-portal/backend/app/api/meetings.py`
- Locatie: na regel 632 (`await db.commit()`) in de webhook handler
- Toevoegen: `await cleanup_recording(meeting, db)` (alleen als `meeting.status == "done"`)
- Traceability: SPEC-GDPR-002-R1

**Taak 3.3:** Integreer in bot_poller

- Bestand: `klai-portal/backend/app/services/bot_poller.py`
- Locatie: na regel 97 (`await db.commit()`) in de active-meeting loop
- Locatie: na regel 115 (`await db.commit()`) in de stuck-meeting loop
- Toevoegen: `await cleanup_recording(m, db)` (alleen als `m.status == "done"`)
- Traceability: SPEC-GDPR-002-R1

### Milestone 4: Achtergrondtaak voor oude opnames (Prioriteit Hoog)

**Taak 4.1:** Implementeer `recording_cleanup_loop()`

- Bestand: `klai-portal/backend/app/services/recording_cleanup.py`
- Nieuwe functie: `async def recording_cleanup_loop() -> None`
- Logica:
  1. `await asyncio.sleep(60)` -- wacht tot app volledig gestart is
  2. While True:
     - Query `VexaMeeting` met `status="done"`, `recording_deleted=False`, `created_at < now() - 30 min`
     - Voor elk record: roep `cleanup_recording(meeting, db)` aan
     - `await asyncio.sleep(300)` -- elke 5 minuten controleren
  3. Catch `asyncio.CancelledError` voor graceful shutdown
  4. Catch generieke `Exception` met logging (loop mag niet crashen)
- Patroon: identiek aan `bot_poller.poll_loop()`
- Traceability: SPEC-GDPR-002-R5

**Taak 4.2:** Registreer in FastAPI lifespan

- Bestand: `klai-portal/backend/app/main.py`
- Wijziging: voeg `asyncio.create_task(recording_cleanup_loop())` toe in de `lifespan()` context manager, vergelijkbaar met de registratie van `poll_loop()`
- Traceability: SPEC-GDPR-002-R5

### Milestone 5: Audit logging (Prioriteit Hoog)

**Taak 5.1:** Structlog logging in `delete_recording()` en `cleanup_recording()`

- Alle verwijderpogingen loggen met:
  - `vexa_meeting_id` (int)
  - `meeting_id` (UUID)
  - `result` (`"deleted"` of `"failed"`)
  - `error` (bij mislukking)
- Logniveau: `info` voor succes, `warning` voor mislukking
- Traceability: SPEC-GDPR-002-R6

### Milestone 6: Validatie docker-compose.yml (Prioriteit Laag)

**Taak 6.1:** Verifieer dat `vexa-recordings-data` volume NIET gemount is

- Bestand: `deploy/docker-compose.yml`
- Controleer regels 795-801 (vexa-bot-manager volumes)
- Verwachting: volume is NIET aanwezig -- geen wijziging nodig
- Documenteer deze verificatie in de PR-beschrijving
- Traceability: SPEC-GDPR-002-R7

---

## Risicoanalyse

### Risico 1: Container niet bereikbaar

- **Oorzaak:** vexa-bot-manager container is gestopt, herstart, of hernoemd
- **Impact:** Verwijdering mislukt, opname blijft staan tot container herstart (dan verdwijnt ephemeral storage sowieso)
- **Mitigatie:** Graceful error handling (R4), achtergrondtaak herprobeert (R5), ephemeral storage verdwijnt bij herstart
- **Kans:** Laag (container draait continu)

### Risico 2: Race condition tussen webhook en poller

- **Oorzaak:** Zowel webhook als poller proberen dezelfde opname te verwijderen
- **Impact:** Tweede verwijderpoging faalt (bestand bestaat niet meer) -- wordt gelogd als "failed"
- **Mitigatie:** Guard check op `recording_deleted=True` voorkomt dubbele pogingen. `rm -rf` op een niet-bestaand pad retourneert exit code 0 (geen fout).
- **Kans:** Laag (guard check + idempotent rm)

### Risico 3: exec_run() blokt de event loop

- **Oorzaak:** Docker SDK is synchrone Python; `exec_run()` blokkeert
- **Impact:** Event loop vastgelopen tijdens verwijdering
- **Mitigatie:** Gebruik `asyncio.to_thread()` om de synchrone Docker SDK-calls in een threadpool uit te voeren
- **Kans:** N.v.t. (opgelost door ontwerp)

### Risico 4: Verkeerd pad voor opnamebestanden

- **Oorzaak:** Opnames staan niet in `/var/lib/vexa/recordings/{vexa_meeting_id}`
- **Impact:** Bestanden worden niet verwijderd, maar `rm -rf` retourneert exit code 0
- **Mitigatie:** Verifieer het exacte pad door `exec_run(["ls", "/var/lib/vexa/recordings/"])` op een actieve container uit te voeren tijdens ontwikkeling. Log het pad bij elke verwijderpoging.
- **Kans:** Middel (pad moet geverifieerd worden)

### Risico 5: Containernaam wijzigt

- **Oorzaak:** Docker Compose-hernoemen of multi-instance setup
- **Impact:** `client.containers.get("vexa-bot-manager")` faalt
- **Mitigatie:** Containernaam configureerbaar maken via `settings` (vergelijkbaar met `mongodb_container_name` in provisioning)
- **Kans:** Laag

---

## Architectuurdiagram

```
                        Webhook/Poller
                             |
                    run_transcription()
                             |
                     status = "done"
                             |
                    db.commit() slaagt
                             |
                  cleanup_recording()
                     /              \
               [succes]           [fout]
                  |                  |
        recording_deleted=True    log warning
        recording_deleted_at=now  meeting blijft "done"
                  |
             db.commit()


    recording_cleanup_loop() (elke 5 min)
                  |
         Query: done + not deleted + >30 min
                  |
         cleanup_recording() per record
```

---

## Expert consultatie-aanbeveling

**Backend expert (expert-backend):** Aanbevolen voor review van:
- Docker SDK-integratie en `asyncio.to_thread()` patroon
- Race condition-analyse tussen webhook, poller en cleanup-taak
- Alembic-migratie review

**DevOps expert (expert-devops):** Aanbevolen voor verificatie van:
- Docker socket proxy-permissies voor `exec_run()` op vexa-bot-manager
- Containernaam-resolutie in productie
- Verificatie van het opname-pad binnen de container
