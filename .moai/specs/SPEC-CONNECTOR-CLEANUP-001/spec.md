---
id: SPEC-CONNECTOR-CLEANUP-001
version: "1.0"
status: draft
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: medium
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-04-23 | Mark Vletter | Initial draft. Found during SPEC-CRAWLER-005 Fase 6 E2E: legacy `connector.connectors` tabel, dead `ConnectorScheduler`, missing FK `sync_runs → portal_connectors`. |
| 1.1 | 2026-04-23 | Mark Vletter | REQ-05 resolved: option B — reimplement cron scheduling in a separate SPEC (`SPEC-CONNECTOR-SCHEDULING-001`). `portal_connectors.schedule` kolom blijft staan (als zombie) tot die SPEC landt. Fase 6 van dit SPEC wordt een one-liner: forward-reference schrijven, niets droppen. |

---

# SPEC-CONNECTOR-CLEANUP-001: Afmaken van de connector-architectuur migratie

## Context

Klai heeft ooit twee plekken gehad waar connector-config kon leven:

1. **De oude plek** — `connector.connectors` in de klai-connector service. Bedoeld voor cron-based schedules.
2. **De nieuwe plek** — `public.portal_connectors` in de portal DB. De huidige bron van waarheid, via de portal UI.

Op een gegeven moment is besloten: de portal is voortaan de baas. De migratie is **half uitgevoerd**. Fase 6 van SPEC-CRAWLER-005 (live Voys E2E) haalde drie gevolgen daarvan naar boven:

1. **Connector-delete cleanup gap.** Pagina's bleven achter in `knowledge.crawled_pages` / `knowledge.page_links` na connector-delete — daardoor was re-sync "unchanged"-dedup-geskipt. Gefikst in commit `04dc434c`.
2. **`source_connector_id` werd nooit in crawl-chunks gezet.** Gevolg: `qdrant_store.delete_connector` en `pg_store.delete_connector_artifacts` hadden hun filter op een veld dat niet bestond — elke delete was een stille no-op op artifacts + chunks. Gefikst in commit `66ea2d0c`.
3. **Phantom "orphan sync_runs" detectie.** Een diagnose-fout van de orchestrator, veroorzaakt door de verwarring: `sync_runs` heeft geen FK meer (weg in migration 004), en niemand weet meer of hij nog naar `connector.connectors` of naar `portal_connectors` wijst.

De drie bevindingen wijzen allemaal naar hetzelfde probleem: **de legacy connector-infrastructuur staat er nog, gedeeltelijk uit-gedraad, en maakt zowel code als mensen in de war.**

---

## Problem Statement

### Halfverbouwd

- `connector.connectors` tabel bestaat nog, is **leeg bij alle tenants**, en wordt door niks gevuld.
- `app/models/connector.py::Connector` Python-model leeft nog, wordt als type-hint gebruikt in 6+ adapter-files, terwijl runtime de adapters een `portal_client.ConnectorConfig` Pydantic-object krijgen. Type-hint en realiteit divergeren.
- `ConnectorScheduler` wordt elke service-boot gestart, selecteert 0 rijen, en logt braaf `"Scheduler started with 0 scheduled connectors"`. Dode code die bij elk opstarten draait.

### Verloren integriteit

- `sync_runs.connector_id` had een FK naar `connector.connectors.id` (migration 001). Die is in migration 004 **gedropt**, niet herbedraad naar `portal_connectors.id`.
- Gevolg: er is geen enkele DB-constraint die orphan sync_runs voorkomt. Portal-api moet die integriteit handmatig bewaken bij elke delete. Als iemand ooit sync_runs direct manipuleert (hersteld DB, admin script, test setup) is de state instantly corrupt zonder dat Postgres klaagt.

### Feature-regressie

- `Connector.schedule` kolom (cron expression) was de enige plek waar cron-scheduling configureerbaar was.
- `portal_connectors.schedule` kolom bestaat wel — maar niets leest 'm. De `ConnectorScheduler` kijkt naar de verkeerde (legacy) tabel en vindt dus 0 rijen ook als een gebruiker in de portal een schedule zou invullen.
- Netto-resultaat: cron-scheduling is een gedocumenteerde-maar-niet-werkende feature.

---

## Requirements (EARS format)

### REQ-01 — Legacy tabel + model weg

- **01.1** `connector.connectors` tabel wordt gedropt via een nieuwe Alembic migration in klai-connector.
- **01.2** De migration is `--autogenerate`-vrij: expliciete `op.drop_table("connectors", schema="connector")`.
- **01.3** De migration heeft een werkende `downgrade()` die de tabel herrestaureert (behalve data).
- **01.4** `app/models/connector.py` blijft als bestand bestaan maar bevat alleen nog `Base` (gebruikt door `SyncRun`). De `Connector` class wordt verwijderd.
- **01.5** `app/models/__init__.py` exporteert `Connector` niet meer.

### REQ-02 — Scheduler weg

- **02.1** `app/services/scheduler.py` wordt volledig verwijderd.
- **02.2** `app/main.py` start en stopt geen `ConnectorScheduler` meer.
- **02.3** `apscheduler` dependency wordt uit `pyproject.toml` verwijderd.
- **02.4** `uv.lock` wordt geregenereerd.

### REQ-03 — Adapter type-hints naar ConnectorConfig

- **03.1** Alle adapter-files (`app/adapters/base.py`, `github.py`, `notion.py`, `google_drive.py`, `oauth_base.py`) krijgen type-hints naar `app.services.portal_client.ConnectorConfig` in plaats van `app.models.connector.Connector`.
- **03.2** Docstrings die verwijzen naar "Connector model" worden bijgewerkt naar "ConnectorConfig".
- **03.3** Geen gedragswijziging — alleen type-hints en docs.
- **03.4** `pyright` (of `mypy`) strict draait schoon op alle gewijzigde files.

### REQ-04 — Nieuwe FK `sync_runs.connector_id` → `portal_connectors.id`

- **04.1** Nieuwe Alembic migration in klai-connector voegt FK toe met `ON DELETE CASCADE`.
- **04.2** Data-validation stap vóór FK-add: script die orphan sync_runs detecteert + optie biedt om weg te halen.
- **04.3** `portal_connectors.id` staat in de `klai` database `public` schema — de FK kruist schema's, wat Postgres ondersteunt maar vereist dat de klai-connector role rechten heeft op `public.portal_connectors`. Verifieer dit in de migration.
- **04.4** Na FK: `qdrant_store.delete_connector` + `pg_store.delete_connector_artifacts` krijgen een automatisch-cascadende sync_runs-cleanup als bijeffect van de portal connector delete. Backup voor de applicatielaag.

### REQ-05 — Cron-scheduling: reimplement via aparte SPEC (beslissing B)

- **05.1** `portal_connectors.schedule` kolom blijft staan — geen column drop in deze SPEC.
- **05.2** Er wordt een stub SPEC aangemaakt: `.moai/specs/SPEC-CONNECTOR-SCHEDULING-001/spec.md` met de intentie + scope voor de reimplementatie.
- **05.3** Pitfall entry in `.claude/rules/klai/projects/knowledge.md` documenteert: *"`portal_connectors.schedule` bestaat maar wordt niet gehonoreerd. Zie SPEC-CONNECTOR-SCHEDULING-001 voor de reimpl."* — voorkomt dat een volgende ontwikkelaar denkt dat de feature werkt.
- **05.4** Geen UI-verandering — het veld in de connector wizard / edit blijft zichtbaar maar doet gewoon niks. (Alternatief: frontend toont "Not yet supported" badge; scope-call voor SPEC-SCHEDULING-001.)

### REQ-06 — Tests + verificatie

- **06.1** Bestaande `klai-connector/tests/` suite draait schoon na changes.
- **06.2** Nieuwe regression test: na `portal delete connector` zijn er 0 sync_runs met die connector_id (FK-CASCADE verified).
- **06.3** Integratie-check: klai-connector container herstart en accepteert `/health` zonder errors uit.
- **06.4** `ruff check` + `pyright` strict clean op alle gewijzigde files.

---

## Files

### klai-connector — verwijderd

- `app/services/scheduler.py` (complete file)

### klai-connector — gewijzigd

- `app/main.py` — scheduler start/shutdown uit lifespan
- `app/models/connector.py` — `Connector` class weg, `Base` blijft
- `app/models/__init__.py` — `Connector` uit `__all__`
- `app/adapters/base.py` — type hint refactor
- `app/adapters/github.py` — type hint refactor
- `app/adapters/notion.py` — type hint refactor
- `app/adapters/google_drive.py` — type hint refactor
- `app/adapters/oauth_base.py` — type hint refactor
- `app/services/sync_engine.py` — verwijder eventuele `Connector`-imports
- `pyproject.toml` — `apscheduler` eruit
- `uv.lock` — regen

### klai-connector — nieuw

- `alembic/versions/005_drop_connectors_table.py`
- `alembic/versions/006_sync_runs_fk_portal_connectors.py`
- `tests/test_sync_run_fk_cascade.py` — regression

### portal-api — voorwaardelijk (REQ-05a)

- `klai-portal/backend/app/models/...` — verwijder `schedule` kolom als optie (a)
- `klai-portal/backend/alembic/versions/...` — migration
- `klai-portal/frontend/...` — UI-veld weg

### Documentatie

- `.claude/rules/klai/projects/knowledge.md` — pitfall entry: "connector.connectors is weg, alleen portal_connectors"
- `docs/architecture/klai-knowledge-architecture.md` of equivalent — architectuurdiagram

---

## Exclusions (What NOT to Build)

- **GEEN** nieuwe scheduler implementatie in deze SPEC (dat is REQ-05b, aparte SPEC).
- **GEEN** changes aan `knowledge-ingest`, `retrieval-api`, `portal-frontend` anders dan wat onder REQ-05 staat.
- **GEEN** refactor van de adapter-hiërarchie zelf — alleen type-hints.
- **GEEN** data-migratie: de tabel is leeg, er is niks te migreren.
- **GEEN** changes aan `portal_connectors.encrypted_credentials` of auth-flow.

---

## Constraints

- Elke fase is een **afzonderlijke commit** die los kan worden gerevert als productie raar doet.
- Zero-downtime deploy: de FK-add migration moet op een live running systeem kunnen draaien. Voor deze tabel (1-5 rows in prod) is dat geen zorg, maar de migration moet het wel netjes doen.
- Geen breaking changes voor portal-api of knowledge-ingest — die zien alleen hetzelfde `/sync`-endpoint.
- ruff + pyright strict blijven clean.
- CI blijft groen na elke commit.

---

## References

- SPEC-CRAWLER-005 Fase 6 closure waar de bugs naar boven kwamen
- `klai-connector/alembic/versions/004_remove_sync_run_fk.py` — de migratie die dit probleem achterliet
- Docstring in `klai-connector/app/models/connector.py` regel 1-5 die de legacy status expliciet maakt
- Commits `04dc434c` (cleanup gap fix) en `66ea2d0c` (source_connector_id threading) — reparaties die nu mooi worden bekroond met FK-cascade
