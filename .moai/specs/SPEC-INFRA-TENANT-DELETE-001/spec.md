---
id: SPEC-INFRA-TENANT-DELETE-001
version: "0.1.0"
status: draft
created: "2026-05-03"
updated: "2026-05-03"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-03 | MoAI | Initiële draft. Happy-path tenant deprovisioning over 16 stappen, twee endpoints (owner + platform-admin) met gedeelde async orchestrator, hard-delete op `portal_orgs` met aparte `tenant_lifecycle_events` audit-tabel zonder FK, transitionele state-machine (`deprovisioning` → `deprovisioned` of `failed_deprovisioning`), Camp 1 light fail-loud retry-pattern. |

# SPEC-INFRA-TENANT-DELETE-001: Tenant deprovisioning — happy-path delete + endpoint

## Overview

Klai heeft tenant-provisioning (`klai-portal/backend/app/services/provisioning/orchestrator.py::provision_tenant`) maar GEEN happy-path delete-flow. De bestaande `_compensate_*` functies in dezelfde module zijn rollback-paden voor mislukte provisioning, niet voor "deze tenant is klaar en moet weg". Gevolg: zonder een complete delete kunnen we geen herhaalbare e2e-tests draaien (orphan tenants per run) en hebben we geen self-service offboarding.

Deze SPEC implementeert een complete deprovisioning-flow over 16 stappen die alle 11 resource-categorieën opruimt (Caddy, LibreChat container + filesystem, MongoDB database + user, Meilisearch, Redis, Qdrant points, FalkorDB graph, Scribe artifacts, LiteLLM team, Stripe customer, Zitadel OIDC app + users + org, docs-app personal KB, Postgres rijen). Een kleine state-machine markeert de tenant als `deprovisioning` zodat ingelogde teamleden direct een nette 403 krijgen, en een aparte `tenant_lifecycle_events` audit-tabel zonder FK overleeft de hard-delete van `portal_orgs`.

Twee endpoints delen één orchestrator: `DELETE /api/admin/org/me` voor owner-self-service en `DELETE /api/admin/orgs/{slug}/deprovision` voor platform-admins (support, fraude, e2e-cleanup). Het patroon is direct geleend van `provision_tenant` + `retry_provisioning.py` — zelfde state-machine semantiek, zelfde `BackgroundTasks`-aanroep, zelfde retry-via-admin-endpoint.

Failure-strategie: **fail loudly, no fake success.** Elke step is idempotent en heeft 3 interne retries met exponential backoff. Faalt een step definitief? Status wordt `failed_deprovisioning`, admin retry-endpoint kan opnieuw vanaf-begin draaien (steps overslaan al-gedane werk via idempotency).

## Environment

- **Affected services:** klai-portal/backend (orchestrator + endpoints + audit-tabel + auth-flow check), klai-portal/frontend (DeleteOrgModal + status-polling pagina).
- **External resources** (zelfde 11 systemen die `provision_tenant` aanmaakt): Docker (LibreChat container), `/opt/klai/librechat/{slug}/` (filesystem), MongoDB (db `librechat-{slug}` + user), Meilisearch (tenant-index), Redis (cache keys met `configs:*` patroon), Qdrant (`klai_knowledge` + `klai_focus` shared collections, points met `org_id` filter), FalkorDB via Graphiti (`group_ids=[org_id]`), Scribe Garage S3 (`s3://klai-scribe/{org_slug}/`), LiteLLM (team), Stripe (customer archive), Zitadel (OIDC app + users + org), docs-app (`personal` KB).
- **Affected klai-portal backend files:**
  - `app/services/provisioning/deprovisioning_orchestrator.py` (NIEUW)
  - `app/services/provisioning/deprovisioning_steps.py` (NIEUW — 16 step-functies)
  - `app/services/provisioning/state_machine.py` (UITGEBREID — 3 nieuwe states)
  - `app/services/zitadel.py` (UITGEBREID — `delete_org` + `list_org_users` + `delete_user`)
  - `app/services/moneybird_client.py` (NIEUW of UITGEBREID — `stop_subscription` + `archive_contact`)
  - `app/services/audit/tenant_lifecycle.py` (NIEUW — emit-helper)
  - `app/api/admin/deprovision_org.py` (NIEUW — beide endpoints + retry)
  - `app/api/admin/__init__.py` (UITGEBREID — router include)
  - `app/api/auth.py` (UITGEBREID — `_get_caller_org` returnt 403 op `provisioning_status='deprovisioning'`)
  - `alembic/versions/{rev}_add_tenant_lifecycle_events.py` (NIEUW)
  - `tests/test_deprovisioning_orchestrator.py` (NIEUW — 16 step-mocks + integration)
  - `tests/test_deprovision_endpoints.py` (NIEUW — auth + state-machine + retry)
  - `tests/test_deprovision_e2e.py` (NIEUW — happy-path tegen dev-stack)
- **Affected klai-portal frontend files:**
  - `frontend/src/components/ui/delete-org-modal.tsx` (NIEUW — gemodelleerd naar `delete-kb-modal.tsx`)
  - `frontend/src/routes/admin/danger-zone.tsx` (NIEUW — owner-only pagina met delete-knop)
  - `frontend/src/routes/admin/deprovisioning-status.tsx` (NIEUW — wachtscherm met polling)
  - `frontend/src/routes/__root.tsx` of equivalent (UITGEBREID — 403-handler met `tenant_deleting` code → redirect naar info-pagina)
  - `frontend/src/routes/tenant-deleted.tsx` (NIEUW — landing voor verwijderde tenant)
  - `frontend/messages/{nl,en}.json` (UITGEBREID — Paraglide strings)
- **Affected docs:**
  - `docs/runbooks/tenant-delete.md` (NIEUW — handmatige fallback + GDPR-context)
  - `klai-portal/CLAUDE.md` (UITGEBREID — verwijzing naar runbook)

## Assumptions

- A1: De bestaande state-machine in `app/services/provisioning/state_machine.py` (TRANSITIONS dict + `transition_state()` helper) accepteert nieuwe states zonder structurele aanpassing — alleen het toevoegen van `deprovisioning`, `deprovisioned`, en `failed_deprovisioning` aan de TRANSITIONS configuration en het `provisioning_status` Postgres CHECK constraint.
- A2: `BackgroundTasks` van FastAPI is voldoende voor de async-execution requirement (zelfde patroon als bestaande `retry_provisioning.py` regel 156). Een dedicated task queue (Celery, RQ, Temporal) is out-of-scope voor MVP.
- A3: Klai's facturatiesysteem is Moneybird (NIET Stripe). `portal_orgs.moneybird_subscription_id` (Text, nullable) bestaat. De Moneybird-stap zal het abonnement opzeggen / contact archiveren via Moneybird's REST API. Exacte API-call wordt tijdens RUN-fase bepaald (Moneybird heeft `subscriptions/{id}/stop` of equivalent — te verifiëren).
- A4: Zitadel's Management API `DELETE /management/v1/orgs` cascadeert alle users + grants in die org. Bevestigd door product-eigenaar. Geen aparte `_delete_zitadel_users` step nodig.
- A5: `klai-knowledge-ingest/knowledge_ingest/graph.py::sweep_orphan_episodes_org_wide` bestaat al en is de canonieke functie om Graphiti/FalkorDB-data org-wide op te ruimen. De `_delete_falkordb_graph` orchestrator-step roept deze functie aan via een interne HTTP-call naar knowledge-ingest (nieuwe endpoint `POST /internal/v1/orgs/{org_id}/wipe-graph`) of importeert direct als knowledge-ingest een library-import toelaat. Eerste keuze tijdens RUN: HTTP-call (loosely coupled, test-baar in isolation).
- A6: Het `delete-kb-modal.tsx` AlertDialog-pattern (type-slug-om-te-bevestigen) is direct herbruikbaar voor `DeleteOrgModal` — alleen labels en API-call wijken af.
- A7: Frontend's bestaande 403-handler in `apiFetch` of een wrapper kan een nieuwe response-code `{"error": "tenant_deleting"}` herkennen en redirecten naar `/tenant-deleted`. Indien geen centrale handler bestaat: één regel toevoegen in `lib/apiFetch.ts`.
- A8: PostgreSQL `DELETE FROM portal_orgs WHERE id=:id` na alle expliciete DELETEs op niet-cascading kindtabellen (zie REQ-9) zal succesvol cascaden naar de wel-cascading tabellen (`partner_api_keys`, `portal_knowledge_bases`, `portal_connectors`, `portal_widgets`, `portal_feedback_events`, `portal_retrieval_gaps`) en de SET NULL tabellen behoorlijk afhandelen (`vexa_meetings`, `product_events` blijven met `org_id=NULL`).
- A9: `tenant_slug_cache` invalidate-helper bestaat al (`from app.api.auth import invalidate_tenant_slug_cache`, gebruikt in [orchestrator.py:595](klai-portal/backend/app/services/provisioning/orchestrator.py#L595) en [retry_provisioning.py:144](klai-portal/backend/app/api/admin/retry_provisioning.py#L144)) en hoeft niet uitgebreid te worden.
- A10: VictoriaLogs 30-dagen retentie van tenant-logs is voor MVP een geaccepteerde GDPR-trade-off. Hard-purge van logs op verzoek is een aparte SPEC.

## Requirements

### R1 — Ubiquitous: twee endpoints delen één orchestrator

**WHEN** een geauthenticeerde caller een DELETE-verzoek doet op `/api/admin/org/me` (owner self-service) of `/api/admin/orgs/{slug}/deprovision` (platform-admin), **THEN** beide endpoints SHALL dezelfde `deprovision_tenant(org_id, deprovisioner_user_id, deprovisioner_type)` orchestrator-functie aanroepen via `BackgroundTasks`.

Auth-rules:
- `/api/admin/org/me`: caller MUST hebben `portal_role='admin'` (org-owner) binnen de eigen org. Slug komt uit `_get_caller_org(credentials, db).org.slug`. `deprovisioner_type='owner'`.
- `/api/admin/orgs/{slug}/deprovision`: caller MUST `_require_admin` slagen ÉN caller's eigen org MUST de platform-org zijn (`settings.platform_org_slug`, default `getklai`). `deprovisioner_type='platform_admin'`.

Beide endpoints SHALL returnen:
- `202 Accepted` + `{"status": "queued", "org_slug": <slug>}` op succes (orchestrator gaat de achtergrond in).
- `403 Forbidden` als auth-rule faalt.
- `404 Not Found` als slug niet bestaat (alleen platform-admin endpoint).
- `409 Conflict` + `{"error": "already_deprovisioning"}` als `provisioning_status` al een terminale of in-progress delete-state is.

Concurrency-guarantee: `SELECT ... FOR UPDATE` op de target row binnen één transactie vóór de status-transitie naar `deprovisioning`. Tweede gelijktijdige request leest de row na de eerste's commit en valt door naar de 409-branch.

### R2 — Event-driven: state-machine uitgebreid met drie nieuwe states

De `provisioning_status` kolom op `portal_orgs` SHALL drie nieuwe waarden ondersteunen:

- `deprovisioning` — orchestrator is bezig. Auth-flow returnt 403 met code `tenant_deleting`. Geen retries op deze state mogelijk.
- `deprovisioned` — terminale succes-state. Wordt eigenlijk nooit geobserveerd want de row is in dezelfde transactie hard-deleted (zie R7). Bestaat als waarde voor het CHECK constraint zodat de orchestrator een geldige tussenstap heeft.
- `failed_deprovisioning` — terminale failure-state. Een step is na 3 retries definitief gefaald. `last_failure` jsonb-kolom (NIEUW) bevat `{step: <name>, error: <truncated>, attempt: 3, failed_at: <iso>}`. Admin retry-endpoint mag opnieuw beginnen vanaf-begin (idempotency van steps zorgt dat al-gedane werk wordt overgeslagen).

De TRANSITIONS dict in `state_machine.py` SHALL toestaan:
- `ready` → `deprovisioning` (start van delete)
- `failed_rollback_complete` → `deprovisioning` (delete van een al-soft-deleted-failed-provisioning row, edge-case)
- `deprovisioning` → `deprovisioned` (alle steps geslaagd, vlak voor hard-delete)
- `deprovisioning` → `failed_deprovisioning` (een step na 3 retries gefaald)
- `failed_deprovisioning` → `deprovisioning` (admin retry)

Een nieuwe migratie SHALL `last_failure JSONB NULL` toevoegen aan `portal_orgs` en het CHECK constraint op `provisioning_status` uitbreiden.

### R3 — Ubiquitous: orchestrator is async via BackgroundTasks, alle steps idempotent met retry

`deprovision_tenant(org_id, deprovisioner_user_id, deprovisioner_type)` SHALL:

1. Een nieuwe DB-sessie openen (`AsyncSessionLocal`, want request-sessie is gesloten).
2. De org-rij lezen + state-transitie naar `deprovisioning` doen binnen één `SELECT FOR UPDATE` + `UPDATE` transactie.
3. `tenant_slug_cache` invalideren via `invalidate_tenant_slug_cache()`.
4. De step-list sequentieel afgaan. Elke step:
   - SHALL idempotent zijn (al-weg = OK, geen exception).
   - SHALL intern 3 attempts proberen met exponential backoff (1s, 2s, 4s) bij `httpx.HTTPError`, `docker.errors.APIError`, `pymongo.errors.OperationFailure` (die NIET de "user not found" code is), `redis.RedisError`, `asyncpg.PostgresError`, `qdrant_client.http.exceptions.UnexpectedResponse`. Andere exception-types: geen retry, direct fail.
   - SHALL bij definitieve failure een `DeprovisionStepError(step_name, original_exc)` raisen.
5. Bij `DeprovisionStepError`: orchestrator commit transitie naar `failed_deprovisioning` met `last_failure` populated, log `deprovisioning_failed` met `step` en `error`, return zonder verdere steps.
6. Bij volledige succes: emit `tenant_lifecycle_event` (R6) + `DELETE FROM portal_orgs WHERE id=:org_id` in dezelfde transactie + commit.

De step-functie signature SHALL zijn:

```python
async def step(state: _DeprovisionState) -> None: ...
```

Waarbij `_DeprovisionState` een dataclass is met alle resource-handles die een step nodig heeft (`slug`, `org_id`, `zitadel_org_id`, `zitadel_oidc_app_id`, `litellm_team_id`, `stripe_customer_id`, `mongo_db_name`, etc.) — geladen door een initiële `_load_state_for_deprovision(org_id, db)` helper.

### R4 — Event-driven: auth-flow blokkeert tijdens `deprovisioning`

`_get_caller_org` in `app/api/auth.py` SHALL aan het einde van de org-resolutie controleren of `org.provisioning_status == 'deprovisioning'`. Zo ja: raise `HTTPException(status_code=403, detail={"error": "tenant_deleting", "message": "This workspace is being deleted by the owner."})`.

Dit blokkeert ALLE ingelogde teamleden onmiddellijk vanaf het moment dat de orchestrator de eerste state-transitie commit. Alle in-flight requests die NA dit moment binnenkomen krijgen 403.

In-flight requests die VOOR de transitie binnen waren maar nog niet de auth-check gepasseerd zijn: idem 403. Requests die al door de auth-check zijn maar nog DB-writes doen: hun effecten worden door de cleanup-steps overschreven of weggegooid (acceptable transient orphans).

### R5 — Ubiquitous: orchestrator step-volgorde is deterministisch

De step-list SHALL in deze volgorde uitgevoerd worden:

| # | Step naam | Wat doet het | Resource |
|---|---|---|---|
| 0 | `_mark_deprovisioning` | UPDATE provisioning_status='deprovisioning' + invalidate slug cache | Postgres + cache |
| 1 | `_delete_caddy_upstream` | rm tenant.caddyfile + reload Caddy | Caddy |
| 2 | `_delete_librechat_container` | docker rm -f librechat-{slug} | Docker |
| 3 | `_delete_librechat_filesystem` | rm -rf /opt/klai/librechat/{slug}/ | Filesystem |
| 4 | `_drop_mongodb_database` | db.dropDatabase() voor librechat-{slug} | MongoDB |
| 5 | `_drop_mongodb_user` | db.command('dropUser', user) | MongoDB |
| 6 | `_delete_meilisearch_index` | DELETE /indexes/{tenant_index} | Meilisearch |
| 7 | `_flush_redis_tenant_keys` | SCAN MATCH configs:{slug}:* + UNLINK | Redis |
| 8 | `_delete_qdrant_points` | client.delete(filter org_id=N) op klai_knowledge + klai_focus | Qdrant |
| 9 | `_delete_falkordb_graph` | Graphiti delete on group_ids=[org_id] (of GRAPH.DELETE fallback) | FalkorDB |
| 10 | `_delete_scribe_artifacts` | S3 batch-delete onder s3://klai-scribe/{slug}/ | Garage S3 |
| 11 | `_delete_litellm_team` | POST /team/delete met team_ids=[id] | LiteLLM |
| 12 | `_archive_moneybird_subscription` | Moneybird API: subscription stop + contact archive | Moneybird |
| 13 | `_delete_personal_kb` | docs_api.deprovision_kb(org_slug, kb_slug='personal') | docs-app |
| 14 | `_delete_zitadel_oidc_app` | DELETE /management/v1/projects/{pid}/apps/{app_id} | Zitadel |
| 15 | `_delete_zitadel_org` | DELETE /management/v1/orgs (cascades users) | Zitadel |
| 16 | `_finalize_postgres_delete` | INSERT tenant_lifecycle_event + DELETE portal_orgs in single tx | Postgres |

**Volgorde-rationale:**
- Step 0 eerst: blokkeert nieuwe traffic op auth-niveau.
- Step 1 tweede: stopt http-routing zodat externe requests niet meer aankomen.
- Steps 2-3 derde: zet de chat-app stil + ruimt zijn config-dir op.
- Steps 4-13: data-stores in willekeurige onafhankelijke volgorde (gekozen op voorspelbaarheid).
- Step 14-15: Zitadel laatst zodat een mid-flight failure de tenant nog "bestaand" houdt voor diagnose. Org-delete cascadet users (per A4).
- Step 16: hard-delete Postgres + audit-emit als laatste atomaire commit.

### R6 — Ubiquitous: aparte audit-tabel `tenant_lifecycle_events` zonder FK

Een nieuwe Postgres-tabel SHALL aangemaakt worden via Alembic-migratie:

```sql
CREATE TABLE tenant_lifecycle_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('provisioned', 'deprovisioned', 'failed_deprovisioning')),
    org_id_snapshot INT NOT NULL,
    org_slug_snapshot TEXT NOT NULL,
    org_name_snapshot TEXT NOT NULL,
    actor_user_id TEXT,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('owner', 'platform_admin', 'system')),
    properties JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_tenant_lifecycle_events_org_slug ON tenant_lifecycle_events (org_slug_snapshot);
CREATE INDEX ix_tenant_lifecycle_events_created_at ON tenant_lifecycle_events (created_at DESC);
```

Geen FK naar `portal_orgs` — overleeft hard-delete by design. Snapshot-velden bewaren wat de tenant heette ten tijde van het event.

RLS-policy: SELECT alleen voor platform-admin (caller's `org.slug == settings.platform_org_slug`), INSERT alleen voor de orchestrator (via raw `text()` SQL bypass van ORM RLS-trigger, mirror van `events.py::emit_event` patroon).

Een helper `app/services/audit/tenant_lifecycle.py::emit_lifecycle_event(...)` SHALL het INSERT-werk doen. Deze helper SHALL synchroon binnen de orchestrator-transactie uitgevoerd worden (NIET fire-and-forget zoals `emit_event`) zodat een failure de hele finalize-step doet falen.

Voor MVP wordt alleen `deprovisioned` en `failed_deprovisioning` geëmit; `provisioned`-emit wordt later toegevoegd in de `provision_tenant` finalizer (out-of-scope deze SPEC, maar tabel-schema is voorbereid).

### R7 — Ubiquitous: hard-delete via `DELETE FROM portal_orgs` na expliciete child-deletes

Step 16 (`_finalize_postgres_delete`) SHALL binnen één transactie:

1. INSERT in `tenant_lifecycle_events` (event_type='deprovisioned').
2. Expliciete DELETEs op niet-cascading child-tabellen in deze volgorde:
   - `DELETE FROM portal_groups WHERE org_id = :id` (ook al cascaden zijn group_memberships)
   - `DELETE FROM portal_products WHERE org_id = :id`
   - `DELETE FROM portal_templates WHERE org_id = :id`
   - `DELETE FROM portal_users WHERE org_id = :id` (LAATST want andere tabellen kunnen FK naar users hebben)
   - Expliciet voor elke andere tabel zonder `ondelete='CASCADE'` (audit complete tijdens RUN-fase via Alembic introspection).
3. `DELETE FROM portal_orgs WHERE id = :id` — cascadet automatisch naar `partner_api_keys`, `portal_knowledge_bases` (en hun child-cascade-chain), `portal_connectors`, `portal_widgets`, `portal_feedback_events`, `portal_retrieval_gaps`. SET NULL: `vexa_meetings.org_id`, `product_events.org_id`.
4. COMMIT.

Bij FK-violation (een geheime niet-cascading kindtabel die we missen): exception → step faalt → `failed_deprovisioning` state, audit-emit wordt rollback'ed. Admin retry kan opnieuw nadat de DELETE-lijst is bijgewerkt.

### R8 — Ubiquitous: admin retry-endpoint voor `failed_deprovisioning`

Een nieuw endpoint `POST /api/admin/orgs/{slug}/retry-deprovisioning` SHALL bestaan, gemodelleerd op [retry_provisioning.py](klai-portal/backend/app/api/admin/retry_provisioning.py):

- Vereist platform-admin auth (zelfde rule als R1's platform-admin endpoint).
- Vereist `org.provisioning_status == 'failed_deprovisioning'`.
- `SELECT FOR UPDATE` op de row, transitie naar `deprovisioning` met reset van `last_failure = NULL`.
- Schedule `deprovision_tenant` als `BackgroundTask`.
- Returnt `202 Accepted` + `{"status": "queued"}`.

Idempotency van elke step zorgt dat al-gedane werk wordt overgeslagen (bv. `_delete_caddy_upstream` heeft `unlink(missing_ok=True)`, `_drop_mongodb_database` checkt `MONGO_DB_NOT_FOUND`, `_delete_qdrant_points` is idempotent op een leeg result).

### R9 — Ubiquitous: frontend DeleteOrgModal + status-polling

**Owner-knop** SHALL bestaan op een nieuwe pagina `frontend/src/routes/admin/danger-zone.tsx`, alleen bereikbaar voor users met `portal_role='admin'`. Layout volgens `portal-frontend.md` (max-w-lg, header met back-button rechts, gevaarzone-styling met `text-[var(--color-destructive)]`).

De knop opent `DeleteOrgModal` (NIEUW component, gemodelleerd op `delete-kb-modal.tsx`):
- AlertDialog (Tier 1 hierarchy) — irreversible operation.
- Lijst van wat verwijderd wordt (workspace naam, X teamleden, X knowledge bases, etc. — counts opgehaald via `GET /api/admin/org/me/deletion-preview` als die bestaat, anders statische tekst).
- Type-naam-om-te-bevestigen input met `kbSlug` vervangen door `org.slug`.
- "Permanent verwijderen" knop met `variant="destructive"`, disabled tot input matcht slug.
- Op submit: `DELETE /api/admin/org/me` → 202 → navigate naar `/admin/deprovisioning-status`.

**Status-pagina** `frontend/src/routes/admin/deprovisioning-status.tsx`:
- Volledig scherm met spinner + "Werkomgeving wordt verwijderd... (ongeveer 30 seconden)".
- `GET /api/admin/org/me/deprovision-status` polling elke 2s.
  - Returnt `{"status": "deprovisioning"}` → blijf pollen.
  - Returnt `404` (org-row weg) → status is "deprovisioned", redirect naar `/tenant-deleted`.
  - Returnt `{"status": "failed_deprovisioning", "last_failure": {...}}` → toon foutmelding + "Neem contact op met support" knop (mailto/intercom).
- Polling-timeout 5 minuten met exponentieel terugzakkend interval (2s → 5s → 10s na 30s).

**Tenant-deleted landing** `frontend/src/routes/tenant-deleted.tsx`:
- Statisch scherm "Deze werkomgeving is verwijderd." + link naar marketing-site.
- Volledig public, geen auth-check.

**403 handler in apiFetch** SHALL nieuwe response-code `tenant_deleting` herkennen:
- Als response is `{"error": "tenant_deleting"}` → redirect naar `/tenant-deleted` met query `?reason=deleting`.
- Toon korte melding "Deze werkomgeving wordt verwijderd door de eigenaar."

**i18n-strings** (Paraglide) voor alle nieuwe UI-tekst, NL + EN.

### R10 — Ubiquitous: status-endpoint voor owner-polling

`GET /api/admin/org/me/deprovision-status` SHALL:
- Vereist owner-auth (zelfde rule als R1's owner-endpoint, ook tijdens `deprovisioning` toegestaan want anders kan owner zijn eigen status niet zien).
- Returnt `200 OK` + `{"status": "deprovisioning"}` als org bestaat met die status.
- Returnt `404 Not Found` als org niet meer bestaat (succesvolle deprovisioning).
- Returnt `200 OK` + `{"status": "failed_deprovisioning", "last_failure": {...}}` op failure.
- Returnt `200 OK` + `{"status": "ready"}` als de owner deze pagina opent ZONDER een actieve deprovision (bv. door direct naar de URL te gaan) — frontend redirect dan terug naar admin home.

Auth-uitzondering: `_get_caller_org` SHALL voor dit specifieke endpoint NIET de standaard 403-bij-`deprovisioning` raisen. Een endpoint-decorator of `__init__.py` route-config-vlag (`allow_during_deprovisioning=True`) regelt dit.

### R11 — State-driven: tenant_slug_cache invalidatie + locking

De `invalidate_tenant_slug_cache()` helper SHALL aangeroepen worden:
- Aan het begin van orchestrator (na transitie naar `deprovisioning`) — zodat nieuwe callback-URL requests de slug niet meer accepteren.
- Aan het einde (na portal_orgs delete) — voor de zekerheid mocht de cache tussentijds gerefresht zijn.

Concurrency-locking volgt het bestaande pattern van `provision_tenant`:
- `_caddy_lock` (asyncio.Lock) wordt hergebruikt voor step 1 (Caddy delete + reload).
- Postgres `SELECT FOR UPDATE` op `portal_orgs WHERE id=:id` binnen elke endpoint-transactie en in `_mark_deprovisioning`.

### R12 — Periodic: out-of-scope verklaringen

Deze SPEC SHALL EXPLICIET NIET in scope nemen:

- **Soft-delete + restore.** Hard-delete is final. Restore-functionaliteit voor enterprise customers is een aparte SPEC.
- **Backup vóór delete.** Geen automatische snapshot of export van tenant-data vóór deprovisioning. Owner is verantwoordelijk voor eigen backup (export-endpoints bestaan al per KB).
- **Bulk-deprovisioning.** Eén tenant per call. Bulk-flow is een aparte SPEC.
- **GDPR hard-purge van logs.** VictoriaLogs 30d-retentie blijft staan. Een latere SPEC kan tenant-specific log-purge implementeren via VL's `/select/logsql/_delete` (indien ondersteund).
- **GDPR hard-purge van `tenant_lifecycle_events`.** Audit-tabel blijft ook na 30d staan. Een GDPR-recht-op-vergetelheid verzoek raakt deze tabel — runbook documenteert het handmatige purge-pad.
- **Tenant-rename of tenant-merge.** Andere lifecycle-operaties zijn aparte SPECs. Het `tenant_lifecycle_events` schema is wel voorbereid op `event_type IN ('provisioned', 'deprovisioned', 'failed_deprovisioning')` — uit te breiden later.
- **Tenant-suspend** (tijdelijk uit, bv. wegens fraude). Aparte SPEC; deze flow is one-way.
- **Webhook-notificatie naar third parties** dat een tenant verwijderd is. Geen externe systemen worden geïnformeerd buiten Stripe/Zitadel/etc. die direct betrokken zijn bij de cleanup.

## Specifications

### Orchestrator skeleton

```python
# app/services/provisioning/deprovisioning_orchestrator.py

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, pin_session
from app.services.provisioning.deprovisioning_steps import STEPS
from app.services.provisioning.state_machine import transition_state
from app.services.audit.tenant_lifecycle import emit_lifecycle_event

logger = structlog.get_logger()


@dataclass
class _DeprovisionState:
    org_id: int
    slug: str
    zitadel_org_id: str
    zitadel_oidc_app_id: str
    litellm_team_id: str
    stripe_customer_id: str | None
    deprovisioner_user_id: str
    deprovisioner_type: str  # 'owner' | 'platform_admin' | 'system'
    org_name: str
    failures: list[str] = field(default_factory=list)


class DeprovisionStepError(Exception):
    def __init__(self, step_name: str, original: Exception):
        self.step_name = step_name
        self.original = original
        super().__init__(f"step {step_name} failed: {original}")


async def deprovision_tenant(
    org_id: int,
    deprovisioner_user_id: str,
    deprovisioner_type: str,
) -> None:
    """FastAPI BackgroundTask entry point. Owns its own DB session."""
    async with AsyncSessionLocal() as db:
        await _run(org_id, deprovisioner_user_id, deprovisioner_type, db)


async def _run(org_id: int, actor_id: str, actor_type: str, db: AsyncSession) -> None:
    state = await _load_state(org_id, actor_id, actor_type, db)
    logger.info("deprovisioning_started", org_id=org_id, slug=state.slug, actor_type=actor_type)

    try:
        for step_fn in STEPS:
            await _run_step_with_retry(step_fn, state)
    except DeprovisionStepError as exc:
        logger.error(
            "deprovisioning_failed",
            org_id=org_id,
            slug=state.slug,
            step=exc.step_name,
            error=str(exc.original),
            exc_info=True,
        )
        await _mark_failed(db, org_id, exc.step_name, str(exc.original))
        return

    # All steps OK — finalize: emit audit + hard-delete in one tx.
    await _finalize_success(db, state)
    logger.info("deprovisioning_complete", org_id=org_id, slug=state.slug)


async def _run_step_with_retry(step: Callable[[_DeprovisionState], Awaitable[None]], state: _DeprovisionState) -> None:
    delays = [1, 2, 4]  # 3 attempts total
    last_exc: Exception | None = None
    for attempt, delay in enumerate(delays, start=1):
        try:
            await step(state)
            return
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            logger.warning(
                "deprovisioning_step_retry",
                step=step.__name__,
                slug=state.slug,
                attempt=attempt,
                error=str(exc),
            )
            if attempt < len(delays):
                await asyncio.sleep(delay)
        except Exception as exc:
            # Non-retryable — fail immediately.
            raise DeprovisionStepError(step.__name__, exc) from exc

    raise DeprovisionStepError(step.__name__, last_exc or RuntimeError("unknown"))
```

(`_RETRYABLE_EXCEPTIONS`, `_load_state`, `_mark_failed`, `_finalize_success` ge-implementeerd in dezelfde file met patterns gelijk aan `provision_tenant`.)

### Endpoint pattern

```python
# app/api/admin/deprovision_org.py

@router.delete("/org/me", status_code=status.HTTP_202_ACCEPTED)
async def deprovision_my_org(
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _, caller_org, caller_user = await _get_caller_org(credentials, db)
    if caller_user.role != "admin":
        raise HTTPException(403, "Only org owner can delete the workspace")

    org = await _lock_org_for_deprovision(db, caller_org.id)
    background_tasks.add_task(
        deprovision_tenant,
        org.id,
        caller_user.zitadel_user_id,
        "owner",
    )
    return {"status": "queued", "org_slug": org.slug}


@router.delete("/orgs/{slug}/deprovision", status_code=status.HTTP_202_ACCEPTED)
async def deprovision_org_admin(
    slug: str,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _, caller_org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    if caller_org.slug != settings.platform_org_slug:
        raise HTTPException(403, "Only platform admins can deprovision other tenants")

    target_org = await _find_org_by_slug(db, slug)
    if target_org is None:
        raise HTTPException(404, "Organisation not found")
    target_org = await _lock_org_for_deprovision(db, target_org.id)
    background_tasks.add_task(
        deprovision_tenant,
        target_org.id,
        caller_user.zitadel_user_id,
        "platform_admin",
    )
    return {"status": "queued", "org_slug": slug}
```

### State-machine extensions

```python
# app/services/provisioning/state_machine.py — extension fragment

DEPROVISION_TRANSITIONS = {
    "ready": {"deprovisioning"},
    "failed_rollback_complete": {"deprovisioning"},
    "deprovisioning": {"deprovisioned", "failed_deprovisioning"},
    "failed_deprovisioning": {"deprovisioning"},  # admin retry
}

# Merge into existing TRANSITIONS dict.
TRANSITIONS = {**TRANSITIONS, **DEPROVISION_TRANSITIONS}
```

CHECK constraint update via Alembic:

```sql
ALTER TABLE portal_orgs DROP CONSTRAINT portal_orgs_provisioning_status_check;
ALTER TABLE portal_orgs ADD CONSTRAINT portal_orgs_provisioning_status_check
  CHECK (provisioning_status IN (
    'pending', 'queued', 'creating_zitadel_app', 'creating_litellm_team',
    'creating_mongo_user', 'writing_env_file', 'creating_personal_kb',
    'creating_portal_kbs', 'starting_container', 'writing_caddyfile',
    'reloading_caddy', 'creating_system_groups', 'ready',
    'failed_rollback_pending', 'failed_rollback_complete',
    'deprovisioning', 'deprovisioned', 'failed_deprovisioning'
  ));
ALTER TABLE portal_orgs ADD COLUMN last_failure JSONB NULL;
```

### Audit-tabel insert pattern (mirror van events.py)

```python
# app/services/audit/tenant_lifecycle.py

import json
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def emit_lifecycle_event(
    db: AsyncSession,
    *,
    event_type: str,
    org_id_snapshot: int,
    org_slug_snapshot: str,
    org_name_snapshot: str,
    actor_user_id: str | None,
    actor_type: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """Synchronous insert within caller's transaction. NOT fire-and-forget."""
    await db.execute(
        text("""
            INSERT INTO tenant_lifecycle_events (
                event_type, org_id_snapshot, org_slug_snapshot, org_name_snapshot,
                actor_user_id, actor_type, properties
            )
            VALUES (
                :event_type, :org_id, :slug, :name, :actor, :actor_type, CAST(:props AS jsonb)
            )
        """),
        {
            "event_type": event_type,
            "org_id": org_id_snapshot,
            "slug": org_slug_snapshot,
            "name": org_name_snapshot,
            "actor": actor_user_id,
            "actor_type": actor_type,
            "props": json.dumps(properties or {}),
        },
    )
```

### Frontend DeleteOrgModal pattern

Direct gemodelleerd op `delete-kb-modal.tsx`. Verschillen:
- `kbSlug` → `orgSlug`, `kbName` → `orgName`
- API-call: `DELETE /api/admin/org/me` ipv `DELETE /api/app/knowledge-bases/{kbSlug}`
- onSuccess: `navigate({ to: '/admin/deprovisioning-status' })` ipv `/app/knowledge`
- Lijst van wat verwijderd wordt: members count, KB count, connector count, "alle conversaties en uploads", "factuurgeschiedenis blijft beschikbaar in Stripe" (omdat archive, niet delete)
- Title: "Werkomgeving permanent verwijderen"
- Confirm-text label: "Typ <strong>{orgSlug}</strong> om te bevestigen"

## Files Affected

### klai-portal/backend (NIEUW + UITGEBREID)

**Nieuw:**
- `app/services/provisioning/deprovisioning_orchestrator.py`
- `app/services/provisioning/deprovisioning_steps.py` (16 step-functies)
- `app/services/audit/__init__.py`
- `app/services/audit/tenant_lifecycle.py`
- `app/api/admin/deprovision_org.py`
- `app/services/moneybird_client.py` (alleen indien nog niet bestaand)
- `alembic/versions/{rev}_add_tenant_lifecycle_events.py`
- `alembic/versions/{rev}_add_deprovision_states.py` (CHECK constraint + last_failure column)
- `tests/test_deprovisioning_orchestrator.py`
- `tests/test_deprovisioning_steps.py`
- `tests/test_deprovision_endpoints.py`
- `tests/test_deprovision_status_endpoint.py`
- `tests/test_tenant_lifecycle_audit.py`
- `tests/integration/test_deprovision_e2e.py`

**Uitgebreid:**
- `app/services/provisioning/state_machine.py` — TRANSITIONS dict + ENTRY_STATES doorvoeren naar deprovision-flow
- `app/services/provisioning/infrastructure.py` — `_sync_drop_mongodb_tenant_database` toevoegen
- `app/services/zitadel.py` — `delete_org`, `list_org_users`, ev. `delete_user_in_org`
- `app/api/admin/__init__.py` — router includen voor nieuwe endpoints
- `app/api/auth.py` — `_get_caller_org` 403-branch op `deprovisioning` + `allow_during_deprovisioning` flag

### klai-portal/frontend (NIEUW + UITGEBREID)

**Nieuw:**
- `frontend/src/components/ui/delete-org-modal.tsx`
- `frontend/src/routes/admin/danger-zone.tsx`
- `frontend/src/routes/admin/deprovisioning-status.tsx`
- `frontend/src/routes/tenant-deleted.tsx`

**Uitgebreid:**
- `frontend/src/lib/apiFetch.ts` (of equivalent) — 403-handler voor `tenant_deleting` code
- `frontend/messages/nl.json` + `frontend/messages/en.json` — Paraglide strings
- `frontend/src/routes/admin/index.tsx` (of nav) — link naar Danger Zone (alleen voor admins)
- `frontend/src/routeTree.gen.ts` — auto-regenerated door TanStack CLI

### Docs (NIEUW + UITGEBREID)

- `docs/runbooks/tenant-delete.md` (NIEUW) — handmatige fallback-procedure als endpoint faalt + GDPR-context
- `docs/runbooks/tenant-delete-rollback.md` (NIEUW) — wat te doen als een step faalt en hoe handmatig schoon te maken
- `klai-portal/CLAUDE.md` (UITGEBREID) — verwijzing naar tenant-delete runbook
- `.claude/rules/klai/projects/portal-backend.md` (UITGEBREID) — sectie "Tenant deprovisioning" naast bestaande "Provisioning state machine"

### Hooks / mechanisch

- Geen wijzigingen aan `.claude/hooks/klai/container-hygiene-preflight.sh` — het bestaande hook (uit SPEC-INFRA-CONTAINER-HYGIENE-001) blokkeert al `docker rm librechat-*`. AC-10 uit deze SPEC is daarmee impliciet gedekt: enige correcte route is via `_delete_librechat_container` step, die intern docker.from_env() gebruikt en dus NIET door de Bash-hook geraakt wordt.

## MX Tag Plan

- `deprovisioning_orchestrator.py::deprovision_tenant`: `# @MX:ANCHOR fan_in=2 — twee endpoints + admin retry. SPEC-INFRA-TENANT-DELETE-001 R1.`
- `deprovisioning_orchestrator.py::_run_step_with_retry`: `# @MX:NOTE: idempotency-contract per step. Zie deprovisioning_steps.py per-step docstring.`
- Elke step in `deprovisioning_steps.py`: `# @MX:NOTE: idempotent — al-weg = geen exception. SPEC R3.`
- `auth.py::_get_caller_org` 403-branch: `# @MX:WARN: any new endpoint that should bypass this MUST set allow_during_deprovisioning=True. SPEC R4. @MX:REASON: blocks team members during owner-initiated delete.`
- `tenant_lifecycle.py::emit_lifecycle_event`: `# @MX:ANCHOR: synchronous insert, never fire-and-forget. SPEC R6.`
- `state_machine.py` DEPROVISION_TRANSITIONS: `# @MX:NOTE: keep in sync with portal_orgs CHECK constraint. SPEC R2.`
- Frontend `delete-org-modal.tsx`: `// @MX:NOTE: pattern shared with delete-kb-modal.tsx. Tier 1 confirmation per portal-frontend.md.`

## Exclusions

- **Soft-delete + restore.** Tenant-restore is een aparte SPEC.
- **Bulk-deprovisioning meerdere tenants.** Eén tenant per call.
- **Tenant-merge / tenant-rename.** Andere lifecycle-operaties.
- **Backup vóór delete.** Owner-verantwoordelijkheid via bestaande export-endpoints.
- **GDPR hard-purge VictoriaLogs.** Aparte SPEC. Runbook documenteert handmatige stap als interim.
- **GDPR hard-purge `tenant_lifecycle_events`.** Audit-tabel persisteert; runbook documenteert handmatige purge-procedure.
- **Tenant-suspend** (tijdelijk uit). Aparte SPEC.
- **Webhook-notificaties naar third parties.** Buiten directe cleanup-targets, geen externe systemen geïnformeerd.
- **Email-notificatie aan owner of teamleden** dat de delete is uitgevoerd. Out-of-scope MVP; runbook noemt het als toekomstige UX-verbetering.
- **Moneybird openstaande facturen afhandelen.** Het abonnement-stop laat eventueel openstaande facturen staan. Runbook noteert dit als operationeel risico — toekomstige SPEC kan een pre-delete-check toevoegen die owner waarschuwt over openstaand bedrag.
- **Per-tenant Caddy rate-limit zone cleanup buiten de tenant.caddyfile.** De Caddy-config heeft per-tenant zones; verwijderen van de file + reload haalt ook de zone weg (per Caddy semantiek). Geen extra cleanup nodig.
- **CI-rule of pre-merge guard tegen "iemand voegt nieuwe niet-cascading FK toe naar portal_orgs".** Risk: in toekomst groeit de DELETE-lijst stilletjes uit. Runbook + SPEC-test vermelden de volledige tabel-lijst met checksum, en `_finalize_postgres_delete` test asserteert dat een test-tenant met een rij in elke tabel succesvol delete. Het CI-test vangt regressie. Een aparte ast-grep/lint-rule kan later toegevoegd worden.

## Implementation Notes (voor `/moai run`)

- **Volgorde STRIKT:**
  1. Backend: state-machine extension + Alembic-migraties (R2 + R6) — fail-loud bij apply, want validation-fail betekent rollback.
  2. Backend: `tenant_lifecycle.py` audit-helper + tests.
  3. Backend: `deprovisioning_steps.py` per step met unit-tests (mock external clients).
  4. Backend: `deprovisioning_orchestrator.py` met integration-tests (gemockte steps + state-machine).
  5. Backend: `zitadel.py::delete_org` etc. + integration-test tegen dev-Zitadel.
  6. Backend: `_get_caller_org` 403-branch + auth-test fixtures.
  7. Backend: endpoints + endpoint-tests.
  8. Backend: e2e-test tegen dev-stack die volledige delete + verify-all-resources-gone uitvoert.
  9. Frontend: `DeleteOrgModal` + Danger Zone pagina.
  10. Frontend: status-pagina + polling.
  11. Frontend: 403-handler + tenant-deleted landing.
  12. Frontend: Paraglide strings + routeTree regen.
  13. Docs: runbook + portal-backend.md update.
- **Worktree:** verplichte git worktree voor deze SPEC (zie `spec-work-in-a-worktree` pitfall) — meer dan 15 files over backend + frontend + docs.
- **Zitadel cascade:** bevestigd door product-eigenaar dat `DELETE /management/v1/orgs` users + grants meeneemt. Geen aparte step.
- **Graphiti opruim-functie:** hergebruik `klai-knowledge-ingest::sweep_orphan_episodes_org_wide`. Als die per ongeluk niet ALLE episodes ruimt (alleen orphans), tijdens RUN een nieuwe `wipe_org_graph(org_id)` toevoegen aan knowledge-ingest die `MATCH (n) WHERE n.group_id = $org_id DETACH DELETE n` doet.
- **Moneybird API-call exact:** Moneybird REST API documentatie raadplegen tijdens RUN voor de juiste call (`PATCH /subscriptions/{id}` met status=stopped of een soft-delete contact-endpoint). Indien `moneybird_subscription_id` NULL is op de org: step is no-op met log-warn (niet failed_deprovisioning) — dat is een tenant zonder actief abonnement.
- **Test-setup voor e2e:** dedicated dev-tenant aanmaken via signup-flow → wacht op `provisioning_status='ready'` → run delete-flow → assert alle 11 resources weg via directe queries (Mongo, Qdrant, FalkorDB, Postgres, Docker, S3). Cleanup-fixture die dit doet ALS test faalt halverwege.
- **Stuck-detector uitbreiding:** de bestaande `stuck_detector.py` die `failed_rollback_pending` rows oppikt SHALL ook `deprovisioning` rows ouder dan 5 minuten flaggen (orchestrator crashte midden-flow). NIET in scope deze SPEC; documenteren als toekomstig follow-up.
- **CI-tijd:** integration-test tegen dev-stack zal ~60s per run kosten. Overwegen om alleen op nightly of pre-merge te draaien, niet per push.
- **Rollback-plan deze SPEC zelf:** als de migratie + endpoint live gaat en bug blijkt: revert van endpoint-route is voldoende (geen verkeer mogelijk). Migratie kan blijven staan (CHECK constraint accepteert nieuwe values, niemand schrijft ze nog) — geen rollback van Postgres nodig. `tenant_lifecycle_events` tabel blijft leeg als niemand emit, geen kwaad.
