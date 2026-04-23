# Acceptance Criteria — SPEC-CONNECTOR-CLEANUP-001

All scenarios in Gherkin Given/When/Then format, grouped per requirement module.

---

## REQ-CONNECTOR-CLEANUP-001-01 — Legacy tabel + model weg

### AC-01.1: `connector.connectors` tabel bestaat niet meer

```gherkin
Given Alembic is op head na SPEC-CONNECTOR-CLEANUP-001
When psql \dt connector.*
Then is er alleen de tabel connector.sync_runs
  And connector.connectors bestaat niet meer
```

### AC-01.2: Downgrade werkt

```gherkin
Given de SPEC-cleanup migration is toegepast
When alembic downgrade -1 wordt uitgevoerd
Then connector.connectors bestaat weer met de originele schema
  And connector.sync_runs is onveranderd
  And de FK naar portal_connectors is weggehaald
  And een upgrade erna landt zonder errors
```

### AC-01.3: Python import error als code `Connector` probeert te importeren

```gherkin
Given de cleanup is compleet
When `from app.models.connector import Connector` wordt uitgevoerd
Then ImportError wordt geraised
  And `from app.models.connector import Base` werkt nog steeds
```

---

## REQ-CONNECTOR-CLEANUP-001-02 — Scheduler weg

### AC-02.1: klai-connector start zonder scheduler-regel in logs

```gherkin
Given klai-connector container start op
When docker logs klai-core-klai-connector-1 --since 30s
Then bevat het "klai-connector started successfully"
  And bevat het GEEN "Scheduler started with"
  And GEEN "ConnectorScheduler"
  And GEEN "apscheduler"
```

### AC-02.2: scheduler.py bestaat niet meer

```gherkin
Given SPEC is geïmplementeerd
When ls klai-connector/app/services/
Then scheduler.py is NIET in de lijst
```

### AC-02.3: apscheduler dep is weg

```gherkin
Given pyproject.toml van klai-connector
When grep -i apscheduler pyproject.toml
Then geen matches
  And uv.lock bevat geen apscheduler + transitive deps
```

---

## REQ-CONNECTOR-CLEANUP-001-03 — Adapter type-hints

### AC-03.1: Alle adapters type-hinten ConnectorConfig

```gherkin
Given de adapter-files in klai-connector/app/adapters/
When grep "from app.models.connector import Connector" klai-connector/app/
Then geen matches
  And grep "from app.services.portal_client import ConnectorConfig" vindt matches
     in minimaal base.py, github.py, notion.py, google_drive.py, oauth_base.py
```

### AC-03.2: pyright strict is clean

```gherkin
Given de adapter refactor is compleet
When uv run pyright klai-connector/app/
Then 0 errors, 0 warnings
  And 0 van de ontstane errors hebben te maken met Connector/ConnectorConfig drift
```

### AC-03.3: Adapter tests blijven groen

```gherkin
Given tests/adapters/ met bestaande mocks
When uv run pytest tests/adapters/
Then all pass
  And geen test gebruikt Connector() als fixture meer
  And tests gebruiken ConnectorConfig(...) als constructor
```

---

## REQ-CONNECTOR-CLEANUP-001-04 — FK sync_runs → portal_connectors

### AC-04.1: FK bestaat en heeft ON DELETE CASCADE

```gherkin
Given de nieuwe migration is toegepast
When psql query "\d connector.sync_runs"
Then output bevat:
  "Foreign-key constraints:
     fk_sync_runs_connector_id_portal_connectors
     FOREIGN KEY (connector_id) REFERENCES public.portal_connectors(id) ON DELETE CASCADE"
```

### AC-04.2: CASCADE werkt bij portal connector delete

```gherkin
Given een portal_connectors rij pc1
  And een connector.sync_runs rij sr1 met connector_id = pc1.id
When DELETE FROM public.portal_connectors WHERE id = pc1.id
Then connector.sync_runs WHERE id = sr1.id retourneert 0 rijen
  And geen error over orphaned rows
```

### AC-04.3: Orphan pre-check in migration voorkomt half-mislukte upgrade

```gherkin
Given er bestaan orphan sync_runs (connector_id zonder portal_connectors parent)
When alembic upgrade head wordt uitgevoerd
Then de migration detecteert dit
  And de upgrade stopt met een duidelijke error die vertelt welke sync_runs eerst verwijderd moeten worden
  And de FK wordt NIET half-gezet
```

### AC-04.4: klai-connector UI delete flow cleant nu ook sync_runs automatisch

```gherkin
Given een Voys Help NL connector met 1+ sync_runs
When gebruiker klikt Delete in portal UI en bevestigt
Then portal_connectors rij is weg
  And connector.sync_runs rijen voor die connector_id zijn weg via CASCADE (OR via app-laag, beide zijn OK)
  And knowledge.artifacts met die source_connector_id zijn weg via knowledge-ingest delete route
  And Qdrant chunks met die source_connector_id zijn weg via qdrant_store.delete_connector
```

---

## REQ-CONNECTOR-CLEANUP-001-05 — Cron-scheduling beslissing

### AC-05a.1: Als 6a (permanent drop)

```gherkin
Given de gebruiker heeft 6a gekozen
When de migrations zijn toegepast
Then portal_connectors.schedule kolom bestaat niet meer
  And de add-connector + edit-connector UI flows hebben geen schedule-veld meer
  And ConnectorCreate / ConnectorUpdate Pydantic schemas bevatten geen schedule-field
  And .claude/rules/klai/projects/knowledge.md bevat entry dat schedule-feature afgedankt is
```

### AC-05b.1: Als 6b (reimplement via nieuwe SPEC)

```gherkin
Given de gebruiker heeft 6b gekozen
When de SPEC wordt afgesloten
Then er bestaat een nieuwe SPEC-CONNECTOR-SCHEDULING-001 stub
  And .claude/rules/klai/projects/knowledge.md bevat entry met forward-reference
  And portal_connectors.schedule kolom blijft staan (maar gedocumenteerd als "not honored")
```

---

## REQ-CONNECTOR-CLEANUP-001-06 — Testing & verificatie

### AC-06.1: Bestaande test suite passeert

```gherkin
Given alle fases zijn geïmplementeerd
When cd klai-connector && uv run pytest
Then exit code 0
  And coverage op gewijzigde files >= 85%
```

### AC-06.2: Nieuwe regression test dekt FK-CASCADE

```gherkin
Given tests/test_sync_run_fk_cascade.py bestaat
When uv run pytest tests/test_sync_run_fk_cascade.py
Then alle tests pass
  And tests verifiëren: insert pc + sr, delete pc, assert sr weg
```

### AC-06.3: CI blijft groen na elke commit

```gherkin
Given elke fase een aparte commit op main is
When gh run watch <run-id> per commit
Then elke run is success
  And Trivy scan is success
  And klai-connector deploy op core-01 lukt
```

### AC-06.4: klai-connector runtime werkt na deploy

```gherkin
Given de laatste SPEC-commit is gedeployed op core-01
When docker exec klai-core-klai-connector-1 curl -s localhost:8200/health
Then response is {"status":"ok"}
  And docker logs toont geen startup error
  And een handmatige sync via portal UI werkt end-to-end (github/notion/web_crawler)
```

---

## REQ-CONNECTOR-CLEANUP-001-07 — Docs & knowledge

### AC-07.1: Pitfall entry in knowledge.md

```gherkin
Given de implementatie is compleet
When grep "SPEC-CONNECTOR-CLEANUP-001" .claude/rules/klai/projects/knowledge.md
Then er is minimaal één entry
  And de entry legt uit: portal_connectors = bron van waarheid, sync_runs FK CASCADE, connector.connectors weg
```

### AC-07.2: Architectuurdiagram bijgewerkt

```gherkin
Given docs/architecture/klai-knowledge-architecture.md (of vergelijkbaar)
When human reader opent de connector-flow sectie
Then de sectie beschrijft alleen portal_connectors + sync_runs
  And GEEN verwijzing meer naar connector.connectors
```

---

## Edge Cases

### EC-1: Lege sync_runs bij FK-add

```gherkin
Given connector.sync_runs is leeg
When de FK-migration wordt toegepast
Then upgrade lukt zonder issue
  And FK is aanwezig
```

### EC-2: portal_connectors wordt geleegd tijdens migration (race)

```gherkin
Given een admin dropt portal_connectors parallel aan de migration
When de FK-migration draait
Then Postgres weigert de FK (want sync_runs kan refereren naar niet-bestaande parent)
  And migration faalt netjes met actionable error
  And geen half-state (upgrade = atomic)
```

### EC-3: klai_connector_role heeft geen REFERENCES op portal_connectors

```gherkin
Given de DB-role mist het REFERENCES-recht
When de FK-migration draait
Then Postgres retourneert "permission denied for table portal_connectors"
  And de migration stopt met duidelijke error
  And gebruiker krijgt advies: "GRANT REFERENCES ON public.portal_connectors TO klai_connector_role"
```

### EC-4: Oude cron-schedule rijen bestaan in portal_connectors.schedule

```gherkin
Given portal_connectors.schedule heeft non-null waarden (bijv. "0 3 * * *")
When 6a wordt uitgevoerd (DROP COLUMN)
Then de data gaat verloren
  And een pre-flight check in de migration logt hoeveel rijen een niet-null schedule hadden
  And de audit log toont wie welke schedule had (voor possible reimpl later)
```

### EC-5: Rollback in productie na deploy

```gherkin
Given fase 5 is gedeployed (FK-add)
  And er is een onverwachte incident ontstaan (bijv. portal-api doet iets raars)
When operator besluit te revertten: alembic downgrade -1
Then FK is weggehaald
  And sync_runs werkt zoals vóór fase 5 (zonder integriteit, zoals de productie-status van vandaag)
  And geen data verloren
```

---

## Quality Gate Criteria

| Gate | Threshold | Evidence |
|------|-----------|----------|
| Unit test coverage nieuwe modules | >= 85% | `pytest --cov` op tests/test_sync_run_fk_cascade.py |
| Regression test suite | 100% pass | `uv run pytest` op klai-connector |
| Ruff + pyright strict | 0 errors | `uv run ruff check .` + `uv run pyright` |
| CI pipeline elke commit | success | `gh run list --branch main` per SPEC-commit |
| Post-deploy container health | `{"status":"ok"}` | `ssh core-01 docker exec klai-connector curl localhost:8200/health` |
| Startup log grep | geen "Scheduler started" / "apscheduler" | `docker logs --since 60s klai-core-klai-connector-1` |
| Postgres schema check | `connector.connectors` is weg | `\d connector.*` |
| FK geverifieerd | `pg_catalog` bevat fk_sync_runs_connector_id_portal_connectors | `SELECT conname FROM pg_constraint WHERE conname = ...` |
