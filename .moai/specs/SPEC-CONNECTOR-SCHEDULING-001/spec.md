---
id: SPEC-CONNECTOR-SCHEDULING-001
version: "0.1"
status: draft
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: low
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1 | 2026-04-23 | Mark Vletter | Initial stub. Created as follow-up to SPEC-CONNECTOR-CLEANUP-001 REQ-05b (reimplement decision). |

---

# SPEC-CONNECTOR-SCHEDULING-001: Herinvoering van cron-scheduling voor connectors

## Context

Klai had ooit cron-based scheduling voor connector-syncs via `connector.connectors.schedule` + `ConnectorScheduler` (apscheduler). Tijdens de migratie naar `portal_connectors` is die feature stilletjes weggevallen:
- `portal_connectors.schedule` kolom bestaat, accepteert cron-expressies via de UI
- Niets leest die waarde
- Enige werkende sync-triggers: "Sync now" knop (manual) en `POST /ingest/v1/crawl/sync` (API)

SPEC-CONNECTOR-CLEANUP-001 ruimt de legacy op maar laat `schedule` kolom + UI-veld staan om de reimpl hier niet te hinderen.

---

## Prerequisite

**SPEC-CONNECTOR-CLEANUP-001 moet zijn afgerond** voordat deze SPEC in run-fase kan gaan. Redenen:
- Nieuwe FK `sync_runs → portal_connectors` is nodig voor referentiële integriteit tijdens scheduled runs
- Adapter type-hints + dode scheduler moeten eerst weg zodat we niet in oude valkuilen stappen

---

## Problem Statement

Een gebruiker die in de Klai portal een connector aanmaakt en een schedule invult ("0 3 * * *" — elke nacht om 3:00) verwacht dat de sync dan automatisch draait. Vandaag gebeurt er niks. De UI geeft geen feedback dat dit niet werkt.

---

## Open Scope — te beslissen tijdens `plan`

### Waar draait de scheduler?

Drie opties:

1. **Portal-api doet het zelf** — extra background task binnen portal-api, minder services, maar portal-api is geen event-loop-zware service.
2. **klai-connector pulled** — klai-connector vraagt periodiek bij portal-api welke connectors een schedule hebben en runt ze. Herhaalt de legacy architecture — waarom hebben we die dan weggehaald?
3. **Nieuwe dedicated scheduler-service** — doet alleen cron → portal-api call (om sync te starten). Apart deployment, clean separation of concerns.

Aanbeveling: optie 1 (portal-api + apscheduler of APScheduler-achtige lib), tenzij rate-limits of worker-isolatie-argumenten optie 3 rechtvaardigen.

### Welke cron-library?

- `apscheduler` — was er voorheen; well-known, actief onderhouden
- `croniter` + eigen loop — simpeler
- `Procrastinate periodic tasks` — zit al in de stack

Aanbeveling: **Procrastinate periodic tasks**. Zit al in de stack, geen extra dependency, draait al in een worker-context (retry + monitoring al in place).

### Timezones

- Connectors kunnen in verschillende tijdzones willen draaien. Klai is NL-centrisch vandaag maar niet voor altijd.
- `portal_connectors.schedule` moet óf impliciet UTC zijn, óf een aparte `timezone` kolom krijgen.

Aanbeveling: UTC-only in v1. Tijdzone-awareness is een v2 toevoegsel.

### User-facing feedback

- "Next sync at 3:00 UTC tomorrow" badge onder de schedule-input
- Last run status + next run timestamp in de connector-tabel
- Notificatie bij failed scheduled run (email/slack/in-app?)

---

## Requirements (draft)

### REQ-01 — Scheduler component

- Procrastinate periodic task reads `portal_connectors` every minute, triggers sync for each connector whose next-fire-time falls in the window.
- Skips connectors that are already mid-sync (no concurrent runs per connector).

### REQ-02 — Cron-expression validatie

- Portal API valideert cron-expressies bij create/update — rejecteer invalid met 422.
- Frontend toont live "next fire time" preview.

### REQ-03 — Observability

- Elke scheduled trigger logt met `reason=schedule` (vs `reason=manual` voor "Sync now").
- Grafana dashboard / VictoriaLogs query: welke connectors draaien scheduled, success-rate, drift.

### REQ-04 — Tests

- Unit tests voor de periodic task (met frozen time)
- Integratie test: maak connector met schedule, wacht 2 minuten, verify sync is getriggerd

---

## Exclusions

- **GEEN** second-level precision — minute-accurate is genoeg
- **GEEN** timezone-awareness in v1
- **GEEN** dependencies tussen schedules (bijv. "run B na A is klaar")
- **GEEN** one-shot future runs ("sync this connector once at 15:00 tomorrow") — dat is een aparte feature

---

## References

- SPEC-CONNECTOR-CLEANUP-001 (prerequisite)
- Historical: `klai-connector/app/services/scheduler.py` (deleted in cleanup) — referenceer als implementation hint
- Procrastinate periodic tasks docs: https://procrastinate.readthedocs.io/en/stable/howto/periodic_tasks.html (verify bij `plan`)

---

**Status:** stub. Vul `plan.md` + `acceptance.md` bij `/moai:plan` invocation.
