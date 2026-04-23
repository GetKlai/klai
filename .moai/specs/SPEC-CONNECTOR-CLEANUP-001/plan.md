# Implementation Plan — SPEC-CONNECTOR-CLEANUP-001

## Overview

Maak de halfverbouwde migratie van `connector.connectors` naar `portal_connectors` af. Vijf kleine, los-revertable fases. Elke fase is een commit, elke commit is standalone groen in CI.

Belangrijkste keuze vooraf: **REQ-05 — hou cron-scheduling wel of niet?** Beantwoord die voor fase 5 start.

---

## Reference Implementation Anchors

| Concept | Reference |
|---------|-----------|
| Lege tabel | `psql klai -c "SELECT COUNT(*) FROM connector.connectors;"` → 0 bij alle tenants |
| Dode code start | `klai-connector/app/main.py` lifespan, regel waar `ConnectorScheduler()` wordt geïnstantieerd |
| Type-hint drift | `klai-connector/app/adapters/github.py::_fetch_files(connector: Connector)` — runtime krijgt `ConnectorConfig` |
| FK-gedropt | `klai-connector/alembic/versions/004_remove_sync_run_fk.py` |
| ConnectorConfig definitie | `klai-connector/app/services/portal_client.py::ConnectorConfig` — het echte type |

---

## Technology Stack

Python 3.12, SQLAlchemy 2.x async, Alembic, asyncpg, pyright strict. Geen nieuwe runtime deps; één dep (`apscheduler`) wordt verwijderd.

---

## Phase Breakdown

### Fase 1 — Scheduler weg (dead-code removal)

**Goal:** klai-connector start op zonder `ConnectorScheduler`. Geen gedragswijziging in prod want scheduler deed al niks.

**Tasks:**

1. Verwijder `scheduler = ConnectorScheduler()` en `await scheduler.start(...)` uit `app/main.py` lifespan.
2. Verwijder `await scheduler.shutdown()` uit de cleanup.
3. Delete `app/services/scheduler.py`.
4. Verwijder `apscheduler` uit `pyproject.toml` `[project].dependencies`.
5. `uv lock` → commit `uv.lock`.
6. Unit test: `test_main_startup.py` — import + lifespan smoke.

**Estimated size:** ~40 LOC removed + lockfile churn.

**Risks:**

- Lock file diff is groot — review apart. Check dat enkel `apscheduler` + transitive deps eruit zijn.
- `apscheduler` wordt ergens anders gebruikt? Grep to confirm: alleen in `scheduler.py`. Veilig.

**Verify:** container herstart, `curl /health` → 200, logs tonen "klai-connector started successfully" zonder scheduler-regel.

### Fase 2 — `connector.connectors` tabel droppen

**Goal:** lege legacy tabel weg uit Postgres. Schema `connector` bevat daarna alleen nog `sync_runs`.

**Tasks:**

1. `alembic revision -m "drop legacy connectors table"` → genereert UUID-safe revision id.
2. Body:
   ```python
   def upgrade():
       op.drop_index("idx_connectors_org_id", "connectors", schema="connector")
       op.drop_table("connectors", schema="connector")

   def downgrade():
       # Restore empty table (no data, schema only)
       op.create_table(
           "connectors", ...  # same DDL as 001_initial
           schema="connector",
       )
       op.create_index("idx_connectors_org_id", "connectors", ["org_id"], schema="connector")
   ```
3. Run `alembic upgrade head` lokaal tegen klai-connector DB — confirm clean.
4. Run `alembic downgrade -1 && alembic upgrade head` — confirm round-trip.

**Estimated size:** ~30 LOC migration.

**Risks:**

- De `Connector` model is nog steeds geïmporteerd in `scheduler.py` (verwijderd in fase 1) en adapters (fase 3). Pas na fase 1 veilig.
- pyright klaagt mogelijk als `Connector` uit `__init__.py` wordt verwijderd vóór fase 3. Volgorde: fase 2 dropt tabel + model, fase 3 vervangt type-hints. Dus òf combineer 2+3 òf doe eerst fase 3.
- **Beter: swap fase 2 en 3.** Eerst type-hints refactoren zodat `Connector` class nergens meer wordt gebruikt, dan pas class + tabel weg.

### Fase 3 — Adapter type-hints naar `ConnectorConfig`

**Goal:** adapters type-hinten het echte runtime-type, niet meer een ORM-model dat ze nooit krijgen.

**Tasks:**

1. Grep: `grep -rn "from app.models.connector import Connector" klai-connector/app/` — 5-6 files.
2. Per adapter: vervang de import door `from app.services.portal_client import ConnectorConfig` en type-hint `Connector` door `ConnectorConfig`. Docstrings aanpassen.
3. `app/services/sync_engine.py` — zelfde oefening.
4. Run `uv run pyright app/adapters/ app/services/` — must be clean.
5. Run bestaande tests (`tests/adapters/`) — moeten groen blijven. Mocks gebruiken mogelijk `Connector(...)` — vervang door `ConnectorConfig(...)`.

**Estimated size:** ~80 LOC changed, geen nieuwe.

**Risks:**

- `ConnectorConfig` heeft mogelijk niet exact dezelfde attributen als `Connector` ORM model. Verify: `ConnectorConfig.id`, `.org_id`, `.config`, `.credentials_enc` moeten bestaan.
- Tests kunnen ORM-specifieke dingen doen (zoals `connector.flush()`). Converteer naar Pydantic-equivalent.

### Fase 4 — Tabel + model droppen

**Goal:** Nu `Connector` ORM nergens meer wordt gebruikt, droppen.

**Tasks:**

1. Verwijder `class Connector` uit `app/models/connector.py`. Laat `class Base(DeclarativeBase)` staan (gebruikt door `SyncRun`).
2. Update docstring: `"""SQLAlchemy Base for klai-connector models."""`.
3. Update `app/models/__init__.py`: remove `Connector` uit imports + `__all__`.
4. Alembic migration zoals beschreven in Fase 2 (`drop_table + drop_index`).
5. Combineer met CI run — check dat imports, migration, adapters allemaal werken.

**Estimated size:** ~50 LOC migration + 20 LOC model cleanup.

**Risks:**

- Als `Base` alleen nog `SyncRun` nodig heeft, verplaats `class Base` eventueel naar `sync_run.py` en verwijder `connector.py` helemaal. Of hou bestandnaam voor minimale diff. Keuze.

### Fase 5 — Nieuwe FK `sync_runs.connector_id` → `portal_connectors.id`

**Goal:** Referentiële integriteit terug. Portal-delete cascadet sync_runs automatisch.

**Tasks:**

1. **Pre-check script:** vind orphan sync_runs vóór de FK kan landen:
   ```sql
   SELECT sr.id, sr.connector_id, sr.status, sr.started_at
   FROM connector.sync_runs sr
   LEFT JOIN public.portal_connectors pc ON pc.id = sr.connector_id
   WHERE pc.id IS NULL;
   ```
   Als er 0 zijn: door. Als er wel: DELETE ze eerst (log naar audit) of escaleer naar user.
2. `alembic revision -m "add fk sync_runs connector_id to portal_connectors"`
3. Body:
   ```python
   def upgrade():
       op.create_foreign_key(
           "fk_sync_runs_connector_id_portal_connectors",
           source_table="sync_runs",
           referent_table="portal_connectors",
           local_cols=["connector_id"],
           remote_cols=["id"],
           source_schema="connector",
           referent_schema="public",
           ondelete="CASCADE",
       )

   def downgrade():
       op.drop_constraint(
           "fk_sync_runs_connector_id_portal_connectors",
           "sync_runs",
           schema="connector",
           type_="foreignkey",
       )
   ```
4. DB-role check: klai-connector-role heeft REFERENCES-recht op `public.portal_connectors`? Verifieer met:
   ```sql
   SELECT has_table_privilege('klai_connector_role', 'public.portal_connectors', 'REFERENCES');
   ```
   Zo niet: grant in een one-time admin step.
5. Regressietest `tests/test_sync_run_fk_cascade.py`:
   - Setup: insert portal_connectors row + sync_runs row met die connector_id.
   - Action: DELETE portal_connectors row.
   - Assert: sync_runs row is weg (CASCADE).

**Estimated size:** ~60 LOC migration + ~80 LOC test.

**Risks:**

- Cross-schema FK: sommige tooling (psql dump, pg_migrate) kan hiccups hebben. Test in dev vóór prod.
- `portal_connectors` is eigendom van portal-api service, niet klai-connector. De FK creëert een logische koppeling tussen twee services' DB-schema's. Dat is architectural debatable — maar praktisch klopt het omdat het 1 Postgres database is.
- Alternatief als cross-schema FK te eng: laat applicatie-laag de cascade doen (portal-api roept knowledge-ingest's `/ingest/v1/connector` aan, knowledge-ingest cleant sync_runs). Meer werk, minder DB-integriteit. Default: cross-schema FK.

### Fase 6 — Cron-scheduling beslissing (REQ-05)

**Goal:** documenteren en, als (a), de UI-resten opruimen.

**Sub-variant 6a — drop permanently:**

1. Alembic migration (portal-api): `ALTER TABLE public.portal_connectors DROP COLUMN schedule`.
2. Portal UI: verwijder schedule-veld uit `/app/knowledge/<kb>/add-connector` wizard + edit-flow.
3. API: verwijder `schedule` uit `ConnectorCreate` / `ConnectorUpdate` Pydantic schemas.
4. Pitfall entry in `.claude/rules/klai/projects/knowledge.md`: "cron schedules zijn afgedankt na SPEC-CONNECTOR-CLEANUP-001 — tegenwoordig syncs altijd portal- of user-getriggerd".

**Sub-variant 6b — reimplement:**

1. Skip deze fase binnen dit SPEC.
2. Open nieuwe SPEC `SPEC-CONNECTOR-SCHEDULING-001`.
3. Pitfall entry: "cron schedules staan in `portal_connectors.schedule` maar worden nog niet gehonoreerd — zie SPEC-CONNECTOR-SCHEDULING-001".

**Estimated size (6a):** ~100 LOC migration + frontend + schema.

**Decision owner:** Mark Vletter.

### Fase 7 — Docs + pitfall

**Goal:** Voorkomen dat volgende persoon over exact dezelfde legacy struikelt.

**Tasks:**

1. Update `docs/architecture/klai-knowledge-architecture.md` — connector-flow diagram bijwerken.
2. Entry in `.claude/rules/klai/projects/knowledge.md` onder nieuwe sectie "Connector lifecycle":
   - `portal_connectors` is de bron van waarheid.
   - `sync_runs.connector_id` → `portal_connectors.id` ON DELETE CASCADE.
   - `connector.connectors` is weg sinds SPEC-CONNECTOR-CLEANUP-001.
3. Update SPEC frontmatter → `status: implemented`.

**Estimated size:** ~100 LOC docs.

---

## MX Tag Plan

Fan_in-hoge targets voor `@MX:ANCHOR`:
- `app/services/portal_client.py::ConnectorConfig` — het nieuwe type-anker voor alle adapters.
- `app/services/sync_engine.py::run_sync` — de nieuwe entry die direct met ConnectorConfig werkt.

Geen `@MX:WARN` te bedenken — alles wat we doen is opruimen, niet riskante toevoegingen.

---

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `ConnectorConfig` mist attrs die adapters gebruiken | M | M | Grep + typer eerste, dan adapter-by-adapter fix |
| Cross-schema FK (`connector.sync_runs` → `public.portal_connectors`) problemen | L | M | Test in dev Postgres; fallback naar applicatie-laag cascade |
| `apscheduler` transitive deps breken iets anders | L | L | uv.lock diff review; grep op apscheduler elders |
| `klai_connector_role` heeft geen REFERENCES-recht op portal_connectors | M | L | GRANT statement in migration (als superuser kan) of one-time admin |
| Portal UI breaks na `schedule` kolom drop (6a) | M | M | Voor (6a) eerst frontend deployen dat 'm niet meer toont, dan kolom droppen — klassieke expand-contract |
| Tests gebruiken `Connector(...)` als fixture | H | L | Grep + vervang; makkelijk |

---

## Estimated Effort

- Fase 1 (scheduler weg): 1 commit
- Fase 2 was scheduled als "tabel drop" maar wordt **fase 4** na swap met type-hints
- Fase 3 (type-hints refactor): 1 commit, ~80 LOC
- Fase 4 (tabel + model weg): 1 commit
- Fase 5 (FK): 1 commit + regression test
- Fase 6a OF 6b: 1 commit (6a) of 0 commits + nieuwe SPEC (6b)
- Fase 7 (docs): 1 commit

**Totaal: 5-6 commits.** Elke commit revertable. Geen multi-commit dependencies binnen klai-connector behalve de expliciete volgorde scheduler → type-hints → tabel → FK.

---

## Open Questions

1. **6a of 6b?** Drop cron scheduling permanent, of reimplement in nieuwe SPEC? → beslissing van gebruiker vóór fase 6.
2. **`Base` verhuizen?** Na class-drop in fase 4, blijft `app/models/connector.py` leeg op `Base` na. Verplaatsen naar `app/models/__init__.py` of `app/models/base.py`, of laten staan? → cosmetisch, laat ik aan de implementerende agent.
3. **Cross-schema FK toegestaan?** Als Postgres-roles die REFERENCES niet toestaan, fallback naar applicatie-cascade. → dev-env test beslist.

---

## Ordering Contract [HARD]

De fases hebben een expliciete volgorde vanwege pyright strict:

```
Fase 1  (scheduler dode code weg) — onafhankelijk
   ↓
Fase 3  (type-hints naar ConnectorConfig) — moet vóór fase 4
   ↓
Fase 4  (Connector class + tabel drop) — kan pas als niks meer hint naar Connector
   ↓
Fase 5  (nieuwe FK) — onafhankelijk van 4, kan parallel aan 3
   ↓
Fase 6  (scheduling decision) — na fase 5 omdat die de sync_runs-integriteit fixt die 6 al klaar-ligt
   ↓
Fase 7  (docs)
```

Nooit in andere volgorde. Pyright zal bijten als fase 4 vóór fase 3 komt.
