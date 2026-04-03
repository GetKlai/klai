---
id: SPEC-GDPR-002
version: 1.0.0
status: completed
created: 2026-03-28
updated: 2026-03-28
author: klai-team
priority: high
---

## HISTORY

| Datum      | Versie | Wijziging                                        |
|------------|--------|--------------------------------------------------|
| 2026-03-28 | 1.0.0  | Initieel SPEC-document voor recording cleanup    |

---

# SPEC-GDPR-002: Vexa Recording Cleanup na Transcriptie

## Overzicht

Dit SPEC-document beschrijft het automatisch verwijderen van Vexa meeting-opnames na succesvolle transcriptie, als onderdeel van DPIA-maatregel M1 (dataminimalisatie).

Klai is een privacy-first, EU-only AI-platform. Meeting-opnames worden door de vexa-bot-manager container opgeslagen in `/var/lib/vexa/recordings/` op ephemeral container storage. Na transcriptie hebben deze opnames geen doel meer en moeten ze verwijderd worden voor AVG-compliance.

**Wettelijke grondslag:** AVG Artikel 5(1)(c) -- Dataminimalisatie
**DPIA-maatregel:** M1 -- Opnames zo snel mogelijk verwijderen na verwerking
**Gerelateerd:** SPEC-GDPR-001 (Subject Access Request)

## Aannames

- De vexa-bot-manager container is bereikbaar via de Docker socket proxy (reeds geconfigureerd met `POST=1, CONTAINERS=1, DELETE=1`).
- Portal-api heeft Docker SDK-toegang via `docker.from_env()` -- bewezen patroon in `provisioning.py` voor `container.exec_run()`.
- Opnames staan in `/var/lib/vexa/recordings/` binnen de vexa-bot-manager container, georganiseerd per `vexa_meeting_id`.
- Het `vexa-recordings-data` volume is bewust NIET gemount -- opnames zijn ephemeral en verdwijnen bij container herstart.
- De vexa-bot-manager container heet `vexa-bot-manager` of is vindbaar via Docker SDK op core-01.
- Er is geen Vexa DELETE-API beschikbaar; verwijdering moet via `exec_run()` met `rm -rf`.

## Randvoorwaarden

- De transcriptiepipeline (`run_transcription()`) mag NOOIT falen door een mislukte opname-verwijdering.
- De `vexa-recordings-data` volume mag NIET persistent gemount worden in `docker-compose.yml`.
- Alle verwijderpogingen (geslaagd en mislukt) moeten gelogd worden via structlog.

---

## Requirements

### R1: Opname verwijderen na succesvolle transcriptie

**WHEN** `run_transcription()` succesvol afrondt EN `db.commit()` slaagt met `status="done"`,
**THEN** het systeem **SHALL** een verwijderpoging uitvoeren voor de opnamebestanden van deze meeting in de vexa-bot-manager container.

**Traceability:** SPEC-GDPR-002-R1

### R2: `delete_recording()` methode op VexaClient

Het systeem **SHALL** een `delete_recording(vexa_meeting_id: int)` methode aanbieden op `VexaClient` die via Docker SDK `container.exec_run()` de opnamebestanden verwijdert uit `/var/lib/vexa/recordings/` binnen de vexa-bot-manager container.

**Traceability:** SPEC-GDPR-002-R2

### R3: Verwijderstatus bijhouden in model

Het `VexaMeeting` model **SHALL** de volgende velden bevatten:
- `recording_deleted: Mapped[bool]` (default `False`) -- of de opname succesvol verwijderd is
- `recording_deleted_at: Mapped[datetime | None]` (nullable) -- tijdstip van succesvolle verwijdering

**WHEN** een opname succesvol verwijderd is,
**THEN** het systeem **SHALL** beide velden bijwerken en committen.

**Traceability:** SPEC-GDPR-002-R3

### R4: Graceful failure handling

**IF** het verwijderen van een opname faalt (container niet bereikbaar, exec mislukt, bestand niet gevonden),
**THEN** het systeem **SHALL** de fout loggen maar de transcriptiepipeline **NIET** laten falen. De meeting-status blijft `"done"`.

**Traceability:** SPEC-GDPR-002-R4

### R5: Achtergrondtaak voor oude opnames

Het systeem **SHALL** een periodieke achtergrondtaak bevatten die:
- `VexaMeeting` records opvraagt met `status="done"` EN `recording_deleted=False` EN `created_at` ouder dan 30 minuten
- Voor elk gevonden record een verwijderpoging uitvoert via `delete_recording()`
- Draait als asyncio-taak in de FastAPI lifespan, vergelijkbaar met `bot_poller.poll_loop()`

**Traceability:** SPEC-GDPR-002-R5

### R6: Audit logging

Alle verwijderpogingen (succesvol en mislukt) **SHALL** gelogd worden via de bestaande structlog logger met de volgende velden:
- `vexa_meeting_id`
- `meeting_id` (portal UUID)
- `result` (`"deleted"` of `"failed"`)
- `error` (bij mislukking: foutmelding)

**Traceability:** SPEC-GDPR-002-R6

### R7: Geen persistent volume voor opnames

De `docker-compose.yml` **SHALL NOT** een persistent volume mount toevoegen voor vexa-opnames. Het `vexa-recordings-data` volume (gedefinieerd op regel 58) mag NIET gekoppeld worden aan de vexa-bot-manager service (regels 795-801). Ephemeral storage + actieve cleanup is de AVG-voorkeursbenadering.

**Traceability:** SPEC-GDPR-002-R7

---

## Buiten scope

- **Scribe audio-opnames:** Scribe verwerkt audio volledig in-memory; er zijn geen bestanden om te verwijderen.
- **Real-time vertaling:** Bestaat niet in het huidige systeem.
- **Vexa API-wijzigingen:** Er wordt geen upstream DELETE-API verwacht of aangevraagd.
- **UI-indicatie:** Geen frontend-wijzigingen nodig; verwijdering is volledig backend.
- **Opname-retentiebeleid configureerbaar maken:** De 30-minutendrempel is hardcoded; configuratie is een toekomstige uitbreiding.

---

## Afhankelijkheden

| Component                  | Locatie                                              | Relatie         |
|----------------------------|------------------------------------------------------|-----------------|
| VexaClient                 | `klai-portal/backend/app/services/vexa.py`                | Uitbreiden      |
| VexaMeeting model          | `klai-portal/backend/app/models/meetings.py`              | Uitbreiden      |
| run_transcription()        | `klai-portal/backend/app/api/meetings.py`                 | Integratiepunt  |
| bot_poller.poll_loop()     | `klai-portal/backend/app/services/bot_poller.py`          | Referentiepatroon |
| provisioning.py            | `klai-portal/backend/app/services/provisioning.py`        | Docker exec patroon |
| FastAPI lifespan           | `klai-portal/backend/app/main.py`                         | Registratiepunt |
| docker-compose.yml         | `deploy/docker-compose.yml`                          | Niet wijzigen   |
| Alembic migrations         | `klai-portal/backend/alembic/`                            | Nieuwe migratie |
