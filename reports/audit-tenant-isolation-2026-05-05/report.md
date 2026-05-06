# Tenant-isolation audit — 2026-05-05

**Scope:** alle services in klai monorepo (klai-portal, klai-connector, klai-knowledge-ingest, klai-knowledge-mcp, klai-focus/research-api, klai-mailer, klai-retrieval-api, klai-scribe) + externe stores (Postgres, Qdrant, FalkorDB, Garage, Redis, MongoDB).
**Methode:** statische code-audit (read-only). Drie parallelle `klai-security-audit` agents — sectie A (Postgres RLS + app-level filters), B (externe stores), C (cross-org-by-design + webhook/OAuth callbacks).
**Skepsis-bias:** "geen bewijs van isolatie" = finding. Silent app-filter = geen dichte deur.
**Datum:** 2026-05-05.

---

## Executive summary

**1 CRIT, 12 HIGH, 14 MED, 7 LOW.** De grootste structurele klasse: drie hele Postgres-schema's (`connector.*`, `knowledge.*`, `research.*`) hebben **geen RLS**. Het portal_* RLS-werk uit SPEC-AUTH-006/007 dekt portal-api, maar de andere zes diensten leunen volledig op application-level filters. Dat is precies de "convention die de volgende refactor breekt" die de audit-prompt expliciet als finding klasseert.

### CRIT — moet vóór elke livegang gefixt zijn

| ID | Finding | Service | Impact |
|---|---|---|---|
| **C-2** | `/api/admin/orgs/{slug}/retry-provisioning` mist `_require_platform_admin` | klai-portal | **Elke tenant-admin** (niet alleen platform-admin) kan elke andere tenant's failed-rollback org reviven. Live exploiteerbaar. |

### HIGH — near-term refactor breekt het of structurele single-layer defence

| ID | Finding | Service / Store |
|---|---|---|
| A-2 | `portal_group_memberships` heeft `ENABLE RLS` maar geen policy = default-deny voor non-owner roles | portal-api |
| A-3 | `partner_api_keys` ENABLE/FORCE alleen in docstring "operator note" — geen mechanische garantie | portal-api |
| A-7 | `connector.connectors` + `connector.sync_runs` hebben ZERO RLS (in flight: SPEC-SEC-CONNECTOR-RLS-001) | klai-connector |
| A-8 | `knowledge.*` schema (9 tabellen) heeft ZERO RLS — grootste data-bearing surface | knowledge-ingest |
| A-10 | `research.*` schema (4 tabellen) heeft ZERO RLS | research-api |
| A-12 | research-api `_get_user_org` kiest **willekeurige** tenant uit multi-org `portal_users` rows (geen LIMIT, geen ORDER BY) | research-api |
| A-13 | `/ingest/v1/start` (en sister-endpoints) trust `org_id` uit request body; alleen `INTERNAL_SECRET` als auth | knowledge-ingest |
| B-1 | retrieval-api router berekent per-org KB-centroids door Qdrant te scrollen **zonder org_id filter** — cross-tenant routing-signal-leak op elke chat-turn | retrieval-api / Qdrant |
| C-1 | Gitea webhook: HMAC fail-open op leeg secret + tenant-spoof via Gitea-org `description` veld | knowledge-ingest |
| C-9 | Moneybird + Vexa webhooks: geen replay-protection (TP-W1 re-affirmation) | klai-portal |

### MED — defense-in-depth gaten of latent risico

A-1 (Cat-A USING reused als WITH CHECK), A-4 (3 RLS-tabellen missen FORCE), A-5 (audit-log INSERT `WITH CHECK (true)`), A-6 (`tenant_lifecycle_events` GUC-reliance), A-9 (scribe.transcriptions heeft geen org_id), A-11 (research.chat_messages tenant_id type mismatch met sister-tables), B-2 (preview_crawl int vs Zitadel string namespace fragmentatie), B-3 (research → retrieval ambiguous `org_id` field), B-4 (Garage anonymous public read voor KB-images), B-5 (LiteLLM cache invalidation key mismatch), B-6 (`/internal/.../feature/knowledge` query-param org_id zonder caller binding), C-3 (invite_scheduler INSERT in cross_org_session), C-4 (lifespan stuck-detector ongetagged), C-5 (`/api/internal/connectors/{id}/finalize-delete` ongetagged), C-6 (connector lifespan UPDATE ongetagged), C-7 (`SyncRunReaper.tick()` ongetagged).

### LOW — hygiëne

B-7, B-8, B-9, B-10, C-8, C-10, C-11. Zie sectie-detail.

### Externe-store coverage in één regel

| Store | Tenant-key | Layer | Sterkte |
|---|---|---|---|
| Postgres | `org_id`/`tenant_id` per tabel | RLS (DB) + app-filters | **Goed in portal_*; zero-RLS in connector/knowledge/research** |
| Qdrant `klai_knowledge` | `org_id` (Zitadel string) | App-only filter | Routing-bug B-1; deprovisioning correct |
| Qdrant `klai_focus` | `tenant_id` (Zitadel string) | App-only filter | Verschillende key dan klai_knowledge — bron van #343 (gefixt) en B-3 (latent) |
| FalkorDB / Graphiti | per-org graph via `select_graph(org_id)` + `group_id` op nodes | App-only via per-graph fysieke isolatie | Sterk |
| Garage S3 (KB-images) | object-key prefix `{org_id}/...` | **Anoniem publiek leesbaar via Caddy** | **Zwak — B-4** |
| Garage S3 (scribe) | object-key prefix `{slug}/...` | App-only, gedeelde access-key | Matig |
| Redis | per-key prefix met tenant-component | App-only, gedeelde password | Mostly OK; B-5/B-9/B-10 hygiëne |
| MongoDB (LibreChat) | per-tenant DB + per-tenant user met `readWrite`-only RBAC + per-tenant container | **DB-level RBAC + container-isolation** | **Sterkste in de audit** |

### Globale aanbevelingen (gedetailleerd in `next-steps.md`)

1. **Direct fix C-2** (1-line PR + audit-log)
2. **SPEC voor RLS op connector + knowledge + research schemas** — drie afzonderlijke SPECs of één omnibus
3. **SPEC voor identity-assertion op alle internal endpoints die tenant-id uit body/query lezen** (A-13, B-2, B-6, B-8) — uitbreiding van SPEC-SEC-IDENTITY-ASSERT-001
4. **Webhook replay-store extractie** afronden (klai-libs/webhook-replay) en wire Moneybird + Vexa + Gitea
5. **Garage KB-image read-path achter portal-api** of presigned-met-expiry (B-4)
6. **research-api auth-resolver fix** — JWT resourceowner als bron-van-waarheid voor multi-org users (A-12)

---

# SECTION A — Postgres RLS + Application-Level Filters

## A.0 Drift validatie (prior audit `reports/audit-2026-05-04/tenant-scoping.md`)

| Prior finding | Status 2026-05-05 |
|---|---|
| **TP-1** `portal_join_requests` mist RLS | **FIXED** — migration `2f7d1eae1198` (PR #364, 2026-05-05). `tenant_isolation` policy `USING (org_id = T OR T IS NULL) WITH CHECK (org_id = T)` + `ENABLE/FORCE`. Cat-A justified (auth-seed). |
| **TP-2** `portal_org_allowed_domains` mist RLS | **MOOT** — SPEC-AUTH-009 migration `ed5b78b296f5` dropt de tabel; `post_deploy_ed5b78b296f5.sql` runt `DROP TABLE IF EXISTS ... CASCADE`. |
| **TP-3** Junction-tabellen Cat-D parent dependency | **OPEN, document-only** |
| **TP-4** `vexa_meetings` Cat-A audit-noise | **DESIGN-INTENT** (background task pattern). |
| **TP-5** `connector.sync_runs` org-scoping | **PARTIAL** — `org_id` kolom bestaat (migration 006), app-filter aanwezig in `routes/sync.py`+`internal.py`. **Geen RLS policy** — bevestigd door `Grep CREATE POLICY klai-connector → 0 hits`. SPEC-SEC-CONNECTOR-RLS-001 in flight. |

## A.1 Tenant-column inventarisatie

Zie `coverage-matrix.md` voor de volledige tabel. Samenvatting:

- **portal-api** (public schema): 24 tabellen met tenant-relatie; merendeel via `org_id` (int, FK `portal_orgs.id`), enkele via parent-FK (junctions).
- **klai-connector** (connector schema): 2 tabellen — `connectors`, `sync_runs` — beide `org_id VARCHAR(255)` (Zitadel resourceowner string).
- **klai-knowledge-ingest** (knowledge schema): 9 tabellen met `org_id text` + 4 junction-tabellen zonder eigen tenant-kolom.
- **klai-scribe** (scribe schema): 1 tabel `transcriptions` met **alleen `user_id` (Zitadel sub)** — geen `org_id`.
- **klai-focus/research-api** (research schema): 4 tabellen met `tenant_id` — drie als UUID, één (chat_messages) als `VARCHAR(64)` ⇒ A-11.
- **klai-mailer**: stateless, geen DB.
- **klai-retrieval-api**: geen eigen tabellen; INSERTs op `product_events`.

## A.2 RLS-coverage (samenvatting)

Volledig in `coverage-matrix.md`. Hoofdpunten:

- **portal_*** — 14+ tabellen met RLS, meestal Category-D (strict `_rls_current_org_id()`). 3 tabellen met FORCE-gat (A-4). 3 tabellen met permissive INSERT (A-5).
- **connector.*** — geen RLS (A-7).
- **knowledge.*** — geen RLS (A-8).
- **scribe.*** — geen RLS, geen org_id-kolom (A-9).
- **research.*** — geen RLS (A-10).
- **partner_api_keys + partner_api_key_kb_access** — RLS niet mechanisch verifieerbaar uit migrations (A-3).

## A.3 Findings

### Finding A-2: `portal_group_memberships` heeft `ENABLE RLS` maar geen policy
- **Priority:** HIGH
- **Location:** [klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py:48-52](klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py) — policy gecreëerd; [post_deploy_rls_raise_on_missing_context.sql:135-138](klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql) — comment "verified 2026-04-21 in pg_policies. Skipped."
- **Current situation:** Originele migratie creëert subquery-policy. Post-deploy SQL stelt dat de policy niet op prod bestaat. Met `ENABLE RLS` + geen policy = default-deny voor non-owner roles. Eigenaar `portal_api` bypass alleen als FORCE off — niet verifieerbaar uit code.
- **Attack scenario:** Toekomstig endpoint dat "members of group X" toont zonder JOIN via `PortalGroup` retourneert cross-tenant rows — geen DB-laag net. Hedendaagse callers gaan via `is_member_of_group(...)` helpers die parent-check; volledig conventie-afhankelijk.
- **Recommendation:** `CREATE POLICY tenant_isolation ON portal_group_memberships USING (group_id IN (SELECT id FROM portal_groups WHERE org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL))`. Toevoegen aan `RLS_DML_TABLES`.
- **Confidence:** 85

### Finding A-3: `partner_api_keys` ENABLE/FORCE RLS alleen in docstring
- **Priority:** HIGH
- **Location:** [klai-portal/backend/alembic/versions/b1f2a3c4d5e6_add_partner_api_keys.py:12-22](klai-portal/backend/alembic/versions/b1f2a3c4d5e6_add_partner_api_keys.py)
- **Current situation:** `ENABLE/FORCE ROW LEVEL SECURITY` op `partner_api_keys` + `partner_api_key_kb_access` staan in de docstring, niet in migratie-code. `CREATE POLICY` op tabel met `ENABLE RLS=false` is harmless — policy bestaat in `pg_policies` maar wordt nooit geëvalueerd. Operator was vereist om handmatig `ALTER TABLE ... ENABLE/FORCE` te runnen als `klai` superuser. Geen mechanische garantie dat dit op prod gebeurd is.
- **Attack scenario:** Als operator de manuele step heeft gemist, zijn alle partner API keys van alle tenants zichtbaar voor elke portal_api session. Partner API keys zijn bearer-tokens voor alle customer-API access — disclosure cross-tenant = totale compromise.
- **Recommendation:** Voeg startup-assertion toe analoog aan `assert_portal_users_rls_ready()` in `app/core/database.py:173`: lees `pg_class.relrowsecurity` + `relforcerowsecurity`; raise on startup als false. Verplaats `ENABLE/FORCE` naar een nieuwe alembic-migratie of post_deploy SQL.
- **Confidence:** 85

### Finding A-7: `connector.connectors` + `connector.sync_runs` hebben ZERO RLS
- **Priority:** HIGH
- **Location:** Hele `klai-connector/alembic/` tree — zero `CREATE POLICY` references. App-filter in [klai-connector/app/routes/sync.py:94-99](klai-connector/app/routes/sync.py#L94).
- **Current situation:** Beide tabellen hebben `org_id VARCHAR(255)`. Volledig app-laag-filter. Trigger-sync handler doet `if org_id is not None: query = query.where(SyncRun.org_id == org_id)` — als env-flag `sync_require_org_id=False` flipt (transition fallback), valt WHERE clause weg. Pre-migration-006 historische rijen hebben `org_id IS NULL` en zijn onzichtbaar voor elke per-org filter.
- **Attack scenario:** Elk codepad dat `select(SyncRun)` doet zonder filter retourneert all-tenants rows. Future cleanup-script dat filter laat vallen onder refactor-druk = silent cross-tenant leak.
- **Recommendation:** SPEC-SEC-CONNECTOR-RLS-001 landen. `ALTER TABLE connector.sync_runs ENABLE/FORCE ROW LEVEL SECURITY; CREATE POLICY tenant_isolation ON connector.sync_runs USING (org_id = current_setting('app.current_org_id', true)) WITH CHECK (org_id = current_setting('app.current_org_id', true));` — via post_deploy SQL. Zelfde voor `connectors`. Pin `sync_require_org_id=True` (huidige prod default — behouden).
- **Confidence:** 95

### Finding A-8: knowledge schema heeft ZERO RLS op 9 tenant-tagged tabellen
- **Priority:** HIGH
- **Location:** [klai-knowledge-ingest/alembic/versions/0001_baseline.py:101-315](klai-knowledge-ingest/alembic/versions/0001_baseline.py) — geen `artifacts`, `entities`, `crawl_domains`, `crawl_jobs`, `crawled_pages`, `kb_config`, `org_config`, `page_links`, `parent_chunks` carry RLS.
- **Current situation:** Knowledge service is grootste data-bearing surface (elk geïngest document, elke embedded chunk, elke crawl-job). Elke query 100% leunt op app-laag `WHERE org_id = $1`. Auth = `InternalSecretMiddleware` (één gedeelde `INTERNAL_SECRET` over portal-api / connector / mailer / scribe / research-api / knowledge-mcp / retrieval-api ≥ 7 services).
- **Attack scenario:** (1) Hallucinated query in `pg_store.py` die `org_id = $1` weglaat retourneert cross-tenant rows. (2) `start_crawl` neemt `req.org_id` uit body — elke service met `INTERNAL_SECRET` kan crawl voor arbitrary org_id submitten en target's KB pollueren (zie A-13). (3) `backfill.py:45` doet `SELECT DISTINCT org_id FROM knowledge.artifacts LIMIT 1` — operationele footgun.
- **Recommendation:** Dedicated alembic-migratie + `post_deploy_*.sql` enabling RLS op alle 9 tabellen, Cat-D pattern met strict `_rls_current_org_id()`. Bind ingest-endpoints via cryptografische identity-assertion ipv body-trust.
- **Confidence:** 95

### Finding A-10: research-api schema heeft ZERO RLS op 4 tenant-tagged tabellen
- **Priority:** HIGH
- **Location:** `klai-focus/research-api/alembic/versions/0001_create_research.py`, `0002_chat_history.py`, `0003_drop_embedding_column.py` — geen `CREATE POLICY` op `research.notebooks`, `research.sources`, `research.chunks`, `research.chat_messages`.
- **Current situation:** `_get_notebook_or_404` op [klai-focus/research-api/app/api/notebooks.py:71-87](klai-focus/research-api/app/api/notebooks.py#L71) checkt scope-by-scope. Voor `personal` branch: alleen `owner_user_id != user.user_id` — checkt NIET dat `tenant_id` matcht. Een user die tussen orgs wordt verplaatst behoudt access tot personal notebooks van vorige org. Combineer met A-12 (auth-resolver pakt willekeurige tenant): stille cross-tenant chunk-reads één refactor verwijderd.
- **Recommendation:** Cat-D RLS op vier research-tabellen; bind `app.current_tenant_id` vanuit JWT-resolved tenant; voeg expliciete `Notebook.tenant_id == user.tenant_id` toe aan beide scope-branches.
- **Confidence:** 90

### Finding A-12: research-api `_get_user_org` resolves arbitrary tenant voor multi-org users
- **Priority:** HIGH
- **Location:** [klai-focus/research-api/app/core/auth.py:104-113](klai-focus/research-api/app/core/auth.py#L104)
- **Current situation:** `SELECT pu.org_id, po.zitadel_org_id FROM portal_users pu JOIN portal_orgs po ON po.id = pu.org_id WHERE pu.zitadel_user_id = :uid` — **geen LIMIT, geen ORDER BY**, dan `org = row.fetchone()` pakt eerste rij. `portal_users` heeft `UniqueConstraint("zitadel_user_id", "org_id")` (NIET unique op alleen `zitadel_user_id` — zie [klai-portal/backend/app/models/portal.py:98](klai-portal/backend/app/models/portal.py)). Multi-org users (SPEC-AUTH-006) hebben meerdere rijen.
- **Attack scenario:** User in org-A én org-B. Opent research-api verwacht org-A notebooks; query retourneert org-B's row eerst; elke request opereert nu als org-B. User ziet org-B's `org`-scope notebooks. Met A-10 (geen RLS) compoundeert: ook sources/chunks van org-B met scope=`org` zichtbaar. Een org-B admin deelt een org-scope notebook verwacht alleen-binnen-B — user ziet het via org-A portal-login.
- **Recommendation:** (1) JWT `urn:zitadel:iam:org:project:resourceowner` claim als bron-van-waarheid voor tenant. (2) Als JWT resourceowner geen `portal_users` row matcht → 403. (3) Voeg ontbrekende `tenant_id` check toe aan `_get_notebook_or_404` personal-scope.
- **Confidence:** 95

### Finding A-13: knowledge-ingest `/ingest/v1/start` trust body-supplied org_id
- **Priority:** HIGH
- **Location:** [klai-knowledge-ingest/knowledge_ingest/routes/knowledge.py:20-53](klai-knowledge-ingest/knowledge_ingest/routes/knowledge.py#L20)
- **Current situation:** `INSERT INTO knowledge.crawl_jobs (id, org_id, kb_slug, ...) VALUES ($1, $2, ...)` met `req.org_id` uit body. Auth = shared `INTERNAL_SECRET` only. Geen identity-assertion check op de org_id body field. Pattern recurs in `proc_app.run_crawl.defer_async(... org_id=req.org_id, ...)`.
- **Attack scenario:** Service met `INTERNAL_SECRET` (≥ 7 services per `.claude/rules/klai/projects/portal-security.md`) kan crawl voor arbitrary org_id submitten. Met A-8 (geen RLS): resulting Postgres-rijen + Qdrant-chunks zichtbaar in target tenant's UI. INSERT forge een andere tenant's KB content. Matcht "fail-open-auth" pitfall — trust pattern is te coarse-grained.
- **Recommendation:** Vereis `X-Caller-Service` + `X-Org-ID` headers op elk internal endpoint dat `org_id` neemt. Valideer body-`org_id` matcht header. Bind header-chain cryptografisch (uitbreiding SPEC-SEC-IDENTITY-ASSERT-001). Sterkste binding: derive `org_id` uit JWT door portal-api ondertekend.
- **Confidence:** 90

### Finding A-1: Cat-A policies op `portal_users` + `portal_connectors` missen expliciete WITH CHECK
- **Priority:** MED
- **Location:** [klai-portal/backend/alembic/versions/1b8736eb6455_add_rls_phase2_user_tables.py:42-54](klai-portal/backend/alembic/versions/1b8736eb6455_add_rls_phase2_user_tables.py#L42)
- **Current situation:** Postgres: bij `FOR ALL` policy zonder expliciete `WITH CHECK`, hergebruikt USING als WITH CHECK. Cat-A (`org_id = T OR T IS NULL`) → INSERT met empty GUC slaagt voor ANY org_id (`T IS NULL` → check passeert unconditioneel). Peer migration `2f7d1eae1198` (voor `portal_join_requests`) voegt expliciete `WITH CHECK (org_id = T)` toe — drift; oude migration niet bijgewerkt.
- **Attack scenario:** Codepad dat `AsyncSessionLocal()` opent zonder `set_tenant` en INSERT in `portal_users` runt (signup, join-request approval) plaatst rij met attacker-supplied `org_id`. Vandaag writers tightly-controlled, geen DB-net voor toekomstige regressie.
- **Recommendation:** Post-deploy SQL die de twee Cat-A policies upgrade naar expliciete `WITH CHECK (org_id = T)` (NIET `OR T IS NULL` — IS-NULL bypass alleen op read).
- **Confidence:** 90

### Finding A-4: Drie RLS-enabled tabellen missen FORCE ROW LEVEL SECURITY
- **Priority:** MED
- **Location:** `portal_feedback_events` ([b6c7d8e9f0a1:56](klai-portal/backend/alembic/versions/b6c7d8e9f0a1_add_portal_feedback_events.py#L56)), `widgets`+`widget_kb_access` ([post_deploy_f0a1b2c3d4e5.sql:18-20](klai-portal/backend/alembic/versions/post_deploy_f0a1b2c3d4e5.sql#L18)), `tenant_lifecycle_events` ([post_deploy_7e2d3c1a9b8f.sql:28](klai-portal/backend/alembic/versions/post_deploy_7e2d3c1a9b8f.sql#L28)).
- **Current situation:** Zonder `FORCE`, owner-role (`klai` superuser per `OWNER TO klai`) bypass RLS volledig. Code dat als `klai` connect (operator-scripts, alembic-migrations zelf, ad-hoc psql) ziet alle tenants. `portal_api` runtime role respecteert RLS — dagelijkse requests scoped — maar defense-in-depth één missed config verwijderd.
- **Recommendation:** Post-deploy SQL `ALTER TABLE <name> FORCE ROW LEVEL SECURITY` voor alle drie. Mirror pattern in `1b8736eb6455` (`_enable_rls(table)` runs both ENABLE+FORCE).
- **Confidence:** 85

### Finding A-5: Audit-log INSERT policies = `WITH CHECK (true)`
- **Priority:** MED (audit-integrity, geen data-leak)
- **Location:** [83a82cc61aee:45](klai-portal/backend/alembic/versions/83a82cc61aee_fix_audit_log_rls_allow_inserts_without_.py#L45) (`portal_audit_log`), [6dd868123a4e:38](klai-portal/backend/alembic/versions/6dd868123a4e_add_rls_phase2_background_tables.py#L38) (`product_events`), [b6c7d8e9f0a1:74](klai-portal/backend/alembic/versions/b6c7d8e9f0a1_add_portal_feedback_events.py#L74) (`portal_feedback_events`), [post_deploy_7e2d3c1a9b8f.sql:39-41](klai-portal/backend/alembic/versions/post_deploy_7e2d3c1a9b8f.sql#L39) (`tenant_lifecycle_events`).
- **Current situation:** Cat-C "fire-and-forget" — write-path runt zonder tenant-context dus RLS kan binding niet enforceren. Maar WITH CHECK kan: `org_id = current_setting(...)::int OR current_setting(...) = ''` — dat blokkeert writes met attacker-supplied `org_id` met behoud van no-context use-case. Vandaag: policy permits anything. Session met `app.current_org_id=A` doet `emit_event(org_id=B, ...)` → schrijft B-tagged rij. Audit-integriteit gebroken.
- **Recommendation:** Vervang `WITH CHECK (true)` door `WITH CHECK (current_setting('app.current_org_id', true) = '' OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::int)`. Voor `tenant_lifecycle_events`: write gebeurt binnen `set_tenant(state.org_id)` per `deprovisioning_steps.py:750`, dus strikter `WITH CHECK (org_id_snapshot = NULLIF(current_setting('app.current_org_id', true), '')::int)`.
- **Confidence:** 80

### Finding A-6: `tenant_lifecycle_events` read-policy depends on un-set GUC
- **Priority:** MED
- **Location:** [post_deploy_7e2d3c1a9b8f.sql:54-57](klai-portal/backend/alembic/versions/post_deploy_7e2d3c1a9b8f.sql#L54)
- **Current situation:** `USING (current_setting('app.is_platform_admin', true) = '1')`. Comment: "Until that wiring lands, SELECT returns empty for ALL tenants — which is the safe default". Wiring is partial: platform-admin router doet `await db.execute(text("SELECT set_config('app.is_platform_admin', '1', true)"))`. Maar `is_local=true` → reset at end-of-transaction. Multiple consecutive admin-queries op aparte transactions op zelfde connection vereisen herhaalde set_config.
- **Attack scenario:** Geen leak (missing-GUC = zero rows = safe-fail). Observability defect: audit-tabel lijkt leeg ook al staan rows er.
- **Recommendation:** Doc requirement op elk endpoint dat `tenant_lifecycle_events` leest. Of vervang GUC-pattern door SECURITY DEFINER function die platform-admin-role uit `_get_caller_org` neemt.
- **Confidence:** 70

### Finding A-9: scribe.transcriptions heeft geen org_id, alleen user_id
- **Priority:** MED
- **Location:** [klai-scribe/scribe-api/alembic/versions/0001_create_scribe_schema.py:21-44](klai-scribe/scribe-api/alembic/versions/0001_create_scribe_schema.py#L21)
- **Current situation:** Tabel scoped op `user_id` (Zitadel sub). Endpoints in `app/api/transcribe.py` filteren consistent (lines 222, 287, 322, 345, 369, 402, 477). MAAR: een user-account verplaatst tussen orgs behoudt access tot transcripts uit vorige org. Geen `org_id` om tenant-grens af te dwingen.
- **Attack scenario:** User verlaat company A en joint company B met zelfde Zitadel sub. Opent scribe-api in company-B portal en ziet company-A meeting-transcripts.
- **Recommendation:** Voeg `org_id` toe (Zitadel resourceowner). Filter elke query op `(user_id, org_id)`. Cat-D RLS policy. SPEC-SEC-IDENTITY-ASSERT-001 vereist al JWT `resourceowner` claim — re-use.
- **Confidence:** 90

### Finding A-11: research.chat_messages.tenant_id type mismatch met sister-tables
- **Priority:** MED
- **Location:** [0002_chat_history.py:31](klai-focus/research-api/alembic/versions/0002_chat_history.py#L31) `VARCHAR(64)` versus [0001_create_research.py:26](klai-focus/research-api/alembic/versions/0001_create_research.py#L26) `UUID(as_uuid=True)` voor `notebooks/sources/chunks`.
- **Attack scenario:** Refactor die RLS toevoegt met `current_setting('app.current_tenant_id')::uuid` faalt silent op chat_messages (UUID-vs-VARCHAR comparison is implicit-cast, maar typo of null-handling = "all visible" of "none visible").
- **Recommendation:** ALTER `chat_messages.tenant_id` naar UUID-type via dedicated migration vóór RLS toevoegen.
- **Confidence:** 80

## A.4 Cross-org-by-design sites

Compliant (helper of comment aanwezig): `bot_poller.py:154-164`, `invite_scheduler.py:64-67/100-104/130-133`, `recording_cleanup.py:130-138`, `meetings.py:704-708`, `deprovisioning_orchestrator.py:132 + steps.py:750`.

**Implicit / ongetagged** (geen helper, geen comment): `tenant_host.py:116`, `events.py:43-56` (`emit_event`), `audit/__init__.py:56-68` (`log_event`), `api/internal.py:195-207` (`_log_internal_call`), `provisioning/orchestrator.py:215`, `tenant_matcher.py:73`, `auth.py:355` (OIDC pre-callback), `main.py:64` (lifespan), `klai-knowledge-ingest/backfill.py:45` (random-tenant pick).

Per audit-prompt skepsis-standaard: elk implicit cross-org site moet (a) `cross_org_session()` wrapper of (b) `tenant_scoped_session(org_id)` wrapper of (c) `# cross-org-by-design: <reason>` comment met expliciete intentie. Zie ook C-3..C-8 voor cross-org sites in andere services.

## A.5 Confidence

Overall: **82**.
- Wel: alle alembic-migraties + post_deploy SQL files gelezen; tenant-kolom inventarisatie compleet; app-laag queries voor portal-api gesampled op high-risk surfaces.
- Niet: live `pg_policies` / `pg_class.relrowsecurity` query — A-3 en A-4 vereisen DB-introspectie om prod-state te bevestigen.

---

# SECTION B — External Data Stores

## B.0 Drift validatie (`reports/audit-2026-05-04/multi-tenancy-gdpr.md`)

| Prior finding | Status |
|---|---|
| Qdrant `klai_focus` purge filter-key bug (G4 / #343) | **FIXED** in [deprovisioning_steps.py:266-269](klai-portal/backend/app/services/provisioning/deprovisioning_steps.py#L266) — tuples `klai_knowledge → org_id`, `klai_focus → tenant_id` per `state.zitadel_org_id` |
| Inconsistente vector-keys class | **PARTIAL** — deprovisioning gefixt; writer-side fragmentation latent (zie B-2, B-3) |
| Garage per-tenant prefix op gedeelde bucket, gedeelde access-key | **PARTIAL** — image bucket gebruikt `{org_id}/images/{kb_slug}/...`; scribe `{slug}/`. App-only barriers; KB-images publiek anoniem leesbaar = B-4 |
| Redis key-prefix per tenant, gedeelde password | **PARTIAL** — keys carry tenant component; B-5/B-9/B-10 hygiëne |
| Per-tenant Mongo container + DB + readWrite-only user | **CONFIRMED** — strongste isolatie in audit |

## B.1 Coverage by store

Zie `coverage-matrix.md` voor de volledige tabel.

## B.2 Findings

### Finding B-1: retrieval-api router berekent KB-centroids ZONDER org_id-filter — cross-tenant routing-signal-leak
- **Priority:** HIGH
- **Store:** Qdrant `klai_knowledge`
- **Location:** [klai-retrieval-api/retrieval_api/services/router.py:190-205](klai-retrieval-api/retrieval_api/services/router.py#L190); cache write [router.py:267](klai-retrieval-api/retrieval_api/services/router.py#L267)
- **Current situation:** `_default_compute_centroids(catalog)` itereert over per-org catalog (correct met `org_id` filter op [router.py:36-40](klai-retrieval-api/retrieval_api/services/router.py#L36)) en doet voor elke `KBEntry.source_label` een `client.scroll(... scroll_filter=Filter(must=[FieldCondition(key="source_label", match=MatchValue(value=entry.source_label))]))`. Filter bevat ALLEEN `source_label` — geen `org_id` of `tenant_id`. Voor common labels (`Notion`, `Confluence`, `GitHub`, `Slack`, `Web`) gebruikt door elke tenant retourneert scroll tot 10 random chunks **across all orgs** met label. Hun `vector_chunk` worden gemiddeld in `_centroid_cache[org_id]` en gebruikt door layer-2 semantic router om sources te kiezen voor deze tenant.
- **Attack scenario:** Tenant A heeft labels `["Notion", "Confluence"]`. Tenant B uploadt zwaar in `Notion` met semantisch dichte content "salaries". Wanneer tenant A's user vraagt "salary policy", is de cached `Notion` centroid voor tenant A gecontamineerd met B's vectors → router kiest mogelijk Notion waar het anders iets anders zou kiezen. Chunk-selectie BINNEN gekozen source IS scoped (`_search_knowledge` filter doet `org_id`) — dus géén directe chunk-text exposure. **Maar leakt routing-signal**: welke sources B veel gebruikt + waar hun semantisch zwaartepunt ligt. Structurele cross-tenant info-leak op elke chat-turn.
- **Recommendation:** Voeg `FieldCondition(key="org_id", match=MatchValue(value=org_id))` toe aan scroll-filter op [router.py:195-201](klai-retrieval-api/retrieval_api/services/router.py#L195). Functie-signatuur moet `org_id` accepteren (vandaag niet — `_default_compute_centroids(catalog)` ziet alleen catalog), propageer vanaf caller op [router.py:265-267](klai-retrieval-api/retrieval_api/services/router.py#L265).
- **Confidence:** 85

### Finding B-2: portal-api preview_crawl stuurt int Postgres org.id; sister-paths sturen Zitadel resourceowner string
- **Priority:** MED
- **Store:** Indirectly Qdrant + `knowledge.domain_selectors`
- **Location:** Caller [klai-portal/backend/app/api/app_knowledge_bases.py:1228](klai-portal/backend/app/api/app_knowledge_bases.py#L1228) `org_id=str(org.id)`. Receiver [klai-knowledge-ingest/knowledge_ingest/routes/crawl.py:79](klai-knowledge-ingest/knowledge_ingest/routes/crawl.py#L79). Sister-paths op [app_knowledge_sources.py:134](klai-portal/backend/app/api/app_knowledge_sources.py#L134), [knowledge.py:178,203](klai-portal/backend/app/api/knowledge.py#L178), [app_knowledge_bases.py:551,582,678,743,746,1226](klai-portal/backend/app/api/app_knowledge_bases.py#L551) sturen `org.zitadel_org_id`.
- **Current situation:** Twee distincte namespaces in `knowledge.domain_selectors`: int-as-string (e.g. "42") versus Zitadel string (e.g. "362757920133283846"). Overlappen nooit — geen leak vandaag, maar footgun.
- **Attack scenario:** Future refactor die AI-detected selectors in live crawl-pad wired = cross-pollination of fragmentatie. Geen huidige attacker-movement.
- **Recommendation:** Wijzig [app_knowledge_bases.py:1228](klai-portal/backend/app/api/app_knowledge_bases.py#L1228) van `str(org.id)` naar `org.zitadel_org_id`. Regression test die elke knowledge_ingest_client functie aanroept en assert dat alleen Zitadel form op de wire gaat.
- **Confidence:** 90

### Finding B-3: research-api forwards Zitadel tenant_id als `org_id` naar retrieval-api broad scope
- **Priority:** MED
- **Store:** Qdrant `klai_knowledge` (via retrieval-api broad scope)
- **Location:** Sender [klai-focus/research-api/app/services/retrieval_client.py:69, 110](klai-focus/research-api/app/services/retrieval_client.py#L69) mapt `"org_id": tenant_id`. Receiver [klai-retrieval-api/retrieval_api/services/search.py:140](klai-retrieval-api/retrieval_api/services/search.py#L140) (notebook scope) → `FieldCondition(key="tenant_id", ...)`. Receiver [search.py:77](klai-retrieval-api/retrieval_api/services/search.py#L77) (knowledge scope) → `FieldCondition(key="org_id", ...)`.
- **Current situation:** `RetrieveRequest.org_id` is structureel "het tenant-identifier voor deze collection" — ZELFDE veld mapt naar TWEE verschillende Qdrant payload keys afhankelijk van scope. Vandaag: writer-side reality is BOTH klai_knowledge.org_id en klai_focus.tenant_id slaan dezelfde Zitadel resourceowner string op. **De audit-prompt's claim "klai_knowledge.org_id is int" is dus onjuist; beide zijn strings en identiek.**
- **Attack scenario:** Geen actieve leak. Contract is fragiel: als toekomstige ingest-call numerieke `org_id` schrijft naar klai_knowledge terwijl research-api Zitadel-strings stuurt → broad scope retourneert zero KB results (false negative). Inverse: als knowledge-ingest writers naar andere identifier gaan dan research-api's tenant_id → broad scope kon andere tenant's chunks retourneren.
- **Recommendation:** Split `RetrieveRequest` in expliciete `knowledge_org_id: str` en `focus_tenant_id: str`. Reject ambiguous overloads via `@field_validator`. Document canonical writer-side identifier per collection in module-docstrings van beide `qdrant_store`. Integration-test die elke collection met DIFFERENT tenant identifiers ingest en broad-scope retrieval verifieert zonder crossover.
- **Confidence:** 75

### Finding B-4: Garage KB-image bucket served via Caddy anonymous read — gelekte URL = permanente cross-tenant access
- **Priority:** MED
- **Store:** Garage S3 (`klai-images` bucket)
- **Location:** Object-key [klai-libs/image-storage/klai_image_storage/storage.py:88-96](klai-libs/image-storage/klai_image_storage/storage.py#L88) `{org_id}/images/{kb_slug}/{sha256}.{ext}`. Public-URL [storage.py:98-101](klai-libs/image-storage/klai_image_storage/storage.py#L98) → `/kb-images/{object_key}`. Caddy proxy `/kb-images/*` → `garage:3902` (website-mode, anoniem) op [deploy/caddy/Caddyfile:234-237](deploy/caddy/Caddyfile#L234).
- **Current situation:** Module-docstring [storage.py:9-11](klai-libs/image-storage/klai_image_storage/storage.py#L9) erkent: "served anonymously via Garage website mode through a Caddy reverse proxy at /kb-images/{object_key}". GEEN auth op public read. SHA256 in object-key voorkomt brute-force, maar **elke path-leak == permanente cross-tenant read**. Lekvectoren: (a) chat-citatie `image_urls` in `RetrieveResponse.chunks[].image_urls` op [klai-retrieval-api/retrieval_api/services/search.py:328](klai-retrieval-api/retrieval_api/services/search.py#L328) — als enig pad chunks across orgs retourneert (B-1 routing contamination), wordt URL geshipt naar foreign tenant; (b) productie-logs/screenshots/ticket-attachments lekken URL forever.
- **Attack scenario:** Tenant A's user die ooit citatie zag naar `https://my.getklai.com/kb-images/{ZITADEL_OF_B}/images/org/{SHA}.png` behoudt read-access na verlies van org-membership in B. Elke gelogde URL in VictoriaLogs (Caddy access-logs tonen paths default) is permanent retrievable.
- **Recommendation:** Of (a) move read-path achter portal-api met org-membership check (Caddy `handle_path /kb-images/*` → portal-api die `org.id == path[0]` valideert), of (b) Garage-presigned URLs met short expiry + per-request signing (kost dedup-win). Minimum: verwijder `image_urls` uit cross-org broad-scope merge results tot (a) of (b) gelandt is.
- **Confidence:** 70

### Finding B-5: LiteLLM hook writes Zitadel-string keys; portal-api invalidator deletes int-keys
- **Priority:** MED
- **Store:** Redis (LiteLLM hook cache)
- **Location:** Writer [deploy/litellm/klai_knowledge.py:791,795,1019](deploy/litellm/klai_knowledge.py#L791) — `templates:{zitadel_str}:{user_id}`, `kb_ver:{zitadel_str}:{user_id}`. Invalidator [klai-portal/backend/app/services/litellm_cache.py:31-36](klai-portal/backend/app/services/litellm_cache.py#L31) — `templates:{int}:{user_id}`. Idem [app_account.py:33,47,203](klai-portal/backend/app/api/app_account.py#L33).
- **Current situation:** Invalidator's SCAN-pattern `templates:{int}:*` matcht writer-keys `templates:{zitadel_str}:*` niet (Zitadel-strings zijn 18-cijferige numerics zoals "362757920133283846", duidelijk distinct van portal int IDs). Cache-miss-pad enige update-route; expliciete invalidate na portal-write = no-op.
- **Attack scenario:** Geen tenant-isolation leak. Stale-state bug: user toggle van `kb_retrieval_enabled` of nieuwe template-assignment kost 30s om in chat-UI door te komen. Defense-in-depth: future refactor die deze caches combineert/verplaatst zou tenant-scoping op fragiel type-mismatch leunen.
- **Recommendation:** Wijzig [litellm_cache.py:31-36](klai-portal/backend/app/services/litellm_cache.py#L31) naar `zitadel_org_id: str`; update 4 call-sites. Idem [app_account.py:33-53,203](klai-portal/backend/app/api/app_account.py#L33). Integration-test cache-invalidation.
- **Confidence:** 95

### Finding B-6: Internal feature_knowledge endpoint trust caller-provided zitadel_org_id query-param voor MongoDB DB-pick
- **Priority:** MED
- **Store:** MongoDB (LibreChat per-tenant DBs)
- **Location:** [klai-portal/backend/app/api/internal.py:626-687](klai-portal/backend/app/api/internal.py#L626)
- **Current situation:** Endpoint leest `org_id` query-param, doet `org = portal_orgs WHERE zitadel_org_id = $org_id`, dan `mongo_client[org.librechat_container]["users"].find_one({"_id": oid})`. Auth = `_require_internal_token(request)` (shared `INTERNAL_SECRET`). Geen binding tussen caller-identity en requested-org. Mongo-connection gebruikt `LIBRECHAT_MONGO_ROOT_URI` (admin) → per-tenant Mongo-user RBAC bypassed op dit entry-point.
- **Attack scenario:** Attacker met `INTERNAL_SECRET` (covered in broader Lens-5 secret-recovery) kan elke tenant's MongoDB users-collection enumereren — ObjectIds, openidIds, cross-tenant user-mappings.
- **Recommendation:** Vervang query-param `org_id` door Mongo-driven lookup keyed alleen op ObjectId: walk elke `librechat-*` DB tot ObjectId resolveert, OF (cheaper) cross-tenant lookup-tabel `portal_users(librechat_object_id PK, org_id, zitadel_user_id)`.
- **Confidence:** 60

### Finding B-7: focus research-api `delete_by_source/notebook` filtert op UUID-only zonder tenant_id
- **Priority:** LOW
- **Store:** Qdrant `klai_focus`
- **Location:** [qdrant_store.py:126-141](klai-focus/research-api/app/services/qdrant_store.py#L126); [scripts/backfill_notebook_visibility.py:81-90,114-128](klai-focus/research-api/scripts/backfill_notebook_visibility.py#L81)
- **Current situation:** `source_id`/`notebook_id` zijn v4 UUIDs — collisie ~0. Maar contract "deze rij behoort tenant X" niet enforced at delete-time.
- **Recommendation:** Altijd `FieldCondition(key="tenant_id", ...)` includen. Voeg `tenant_id: str` parameter toe.
- **Confidence:** 80

### Finding B-8: knowledge-ingest stats endpoints nemen org_id uit query-param zonder caller-binding
- **Priority:** LOW
- **Store:** FalkorDB / Postgres knowledge schema
- **Location:** [klai-knowledge-ingest/knowledge_ingest/routes/stats.py:42-46, 61-69](klai-knowledge-ingest/knowledge_ingest/routes/stats.py#L42)
- **Current situation:** Elke caller met shared internal secret kan stats voor ANY org opvragen. Counts only — geen chunk-text leaks.
- **Recommendation:** Caller-service identity-assertion (SPEC-SEC-IDENTITY-ASSERT-001 pattern uitbreiden).
- **Confidence:** 70

### Finding B-9: Redis feedback idempotency-key `fb:{message_id}:{conversation_id}` mist tenant-prefix
- **Priority:** LOW
- **Location:** [klai-portal/backend/app/api/internal.py:846](klai-portal/backend/app/api/internal.py#L846)
- **Recommendation:** `fb:{org.id}:{conversation_id}:{message_id}` — defense-in-depth tenant-prefix.
- **Confidence:** 80

### Finding B-10: Tenant-scoped Redis namespaces NIET geflusht op deprovisioning
- **Priority:** LOW
- **Location:** [klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:182-227](klai-portal/backend/app/services/provisioning/deprovisioning_steps.py#L182) — flusht alleen `configs:{slug}:*`.
- **Current situation:** Stale tenant keys met TTLs (60s rate-limit, 30s kb_ver, 300s templates) blijven staan. Slug-reuse binnen ~5min: stale templates-cache kan deleted tenant's last-known active templates surfacen aan nieuwe tenant.
- **Recommendation:** Extend `_flush_redis_tenant_keys` met `templates:{zitadel_org_id}:*`, `kb_ver:`, `kb_feature:`, `connector_rl:read:`, `connector_rl:write:`, `rl:`, `templates_rl:`.
- **Confidence:** 90

## B.3 Confidence

Overall: **70**.
- Wel: alle Qdrant call-sites enumerated; FalkorDB queries verified; Garage public/auth paths gemapt; Redis key-constructions enumerated; MongoDB provisioning + deprovisioning + only-portal-MongoDB-query (`feature_knowledge`) audited.
- Niet: live verification dat `klai_knowledge.org_id` payload-values vandaag Zitadel-strings zijn en niet legacy ints; live test dat per-tenant Mongo-user RBAC daadwerkelijk runtime-enforced is; volledige enumeratie van `_require_internal_token`-gated endpoints (B-6/B-8 illustreren klasse, inventaris incomplete).

---

# SECTION C — Cross-org-by-design + Webhook/OAuth tenant resolution

## C.0 Drift validatie

Items uit `auth-flow-review.md` + `tenant-scoping.md`:

| Prior ID | Status |
|---|---|
| TP-W1 (HIGH) — Replay-protection ontbreekt op Moneybird/Vexa/Gitea | **Open.** Mailer Zitadel webhook heeft nonce-store; Moneybird/Vexa/Gitea niet. SPEC-SEC-AUTH-HARDENING-001 commit `d3833a4a` extracted `klai-libs/webhook-replay` maar nog niet adopted. |
| TP-W2 (HIGH) — Gitea webhook fail-opens on empty secret | **Open.** Bevestigd in C-1. |
| TP-W3 (MED) — Geen rate-limit op portal/ingest webhooks | Open, out-of-scope dit slice. |
| TP-O1 (HIGH) — Connector-OAuth zonder PKCE | Open. State-cookie + signed Fernet bindt tenant; PKCE-gap is hygiëne, geen tenant-spoof primitive. |
| TP-O2 (MED) — `klai_oauth_state` cookie op `.getklai.com` domein | Open. |

## C.1 + C.2 Inventarisatie

Volledig in `coverage-matrix.md`. Hoofdpunten:

**Cross-org-by-design**: 5 portal-api sites gebruiken expliciet `cross_org_session()` helper met rationale (`bot_poller`, `recording_cleanup`, `invite_scheduler`, `meetings.py:704`, `deprovisioning`). 7 portal-api sites zijn IMPLICIT (geen helper, geen comment). Alle reapers in connector + scribe zijn IMPLICIT.

**Webhook auth**: alle 5 webhook-endpoints fail-closed op leeg secret behalve **Gitea** (C-1). Replay-protection alleen op mailer (C-9 voor rest). OAuth callbacks (auth_bff, oauth) zijn cryptografisch sterk.

## C.3 Findings

### Finding C-2 [CRIT — re-prioritized from HIGH]: `/api/admin/orgs/{slug}/retry-provisioning` mist `_require_platform_admin`
- **Priority:** CRIT (door synthese verhoogd van HIGH; live cross-tenant write door reguliere tenant-admin)
- **Location:** [klai-portal/backend/app/api/admin/retry_provisioning.py:33-159](klai-portal/backend/app/api/admin/retry_provisioning.py#L33)
- **Current situation:** Handler resolveert caller's eigen org via `_get_caller_org` en doet `_require_admin(caller_user)` only. Neemt URL `slug` parameter, lookup `failed_org` op slug **zonder check dat `slug == _caller_org.slug`**, revives row + schedule `provision_tenant(failed_org.id)` als BackgroundTask. Vergelijk sibling `deprovision_org.py:295 retry_deprovisioning` die WEL `_require_platform_admin(caller_org)` doet.
- **Attack scenario:** Elke user met `admin` rol in elke tenant ziet soft-deleted failed-rollback rij van elke andere tenant — bijvoorbeeld high-value customer wiens initial signup faalde — en doet `POST /api/admin/orgs/<victim-slug>/retry-provisioning`. Handler:
  1. `_get_caller_org` → attacker's org
  2. `_require_admin` → passes
  3. failed_row lookup op victim slug → success
  4. revive (`deleted_at = None`, `provisioning_status = "queued"`)
  5. `invalidate_tenant_slug_cache()` → slug-allowlist accepteert victim slug
  6. `provision_tenant(victim_org_id)` → re-runt 17-step Zitadel + LiteLLM + MongoDB + LibreChat provisioning onder victim slug
- **Recommendation:**
  ```python
  _require_admin(caller_user)
  _require_platform_admin(_caller_org)   # ADD THIS LINE
  ```
  + audit-log met `actor_user_id`, target slug, target org_id naar `tenant_lifecycle_events`. **Single-line fix; eenvoudige PR.**
- **Confidence:** 92

### Finding C-1: Gitea webhook fail-open + tenant-spoofable via Gitea org description
- **Priority:** HIGH
- **Location:** [klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:620-668](klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L620), `_get_org_id` op [:809-827](klai-knowledge-ingest/knowledge_ingest/routes/ingest.py#L809)
- **Current situation:** Twee compounding defects:
  1. HMAC-verificatie wrapped in `if settings.gitea_webhook_secret:` (line 630). Empty/missing secret = no auth (TP-W2 still open). `gitea_webhook_secret` heeft `str = ""` default in config en **geen `@field_validator`** die empty rejecteert.
  2. `org_id` afgeleid van `repository.full_name` (body) → split → `_get_org_id(gitea_org_name)` die `description` field van Gitea-org via Gitea API fetcht. Description editable door iedereen met admin op die Gitea-org. Geen cryptografische binding tussen webhook-payload's repo en resolved tenant.
- **Attack scenario:**
  - **A (fail-open):** Operator-typo of SOPS-rotation deletet `GITEA_WEBHOOK_SECRET`. Vanaf dan triggert elke unauthenticated POST met `repository.full_name = "org-VICTIM/kb-name"` `_get_org_id` tegen victim's Gitea-org en ingest attacker-controlled markdown.
  - **B (Gitea-spoof):** User met admin op WILLEKEURIGE Gitea-org onder `settings.gitea_url` wijzigt org-description naar target tenant's Zitadel org_id, push naar repo `org-{their-slug}/{victim-kb-slug}`, fire webhook (signature valid), ingest naar victim tenant. Tenant-binding = "whatever description Gitea-org currently advertises".
- **Recommendation:**
  - `@field_validator("gitea_webhook_secret", mode="after")` rejecting empty/whitespace, mirror van `_require_moneybird_webhook_token` en `_require_vexa_webhook_secret`. Pre-flight: confirm `GITEA_WEBHOOK_SECRET` in `klai-infra/core-01/.env.sops` **vóór** validator landt (per `validator-env-parity` pitfall).
  - Vervang `_get_org_id` door server-side mapping in `knowledge.org_config` ipv Gitea-description. Of allowlist van bekende-managed Gitea-orgs.
  - Voeg `# cross-org-by-design: ...` comment toe.
- **Confidence:** 95

### Finding C-9: Moneybird, Vexa webhooks missen replay-protection (TP-W1 re-affirmation)
- **Priority:** HIGH
- **Location:** [webhooks.py:31-95](klai-portal/backend/app/api/webhooks.py#L31), [meetings.py:675-783](klai-portal/backend/app/api/meetings.py#L675)
- **Current situation:** Beide verifiëren HMAC/shared-secret correct + fail-closed op empty (SPEC-SEC-WEBHOOK-001). Geen nonce-store, geen `(event_id, timestamp)` dedup. Mailer's `app/nonce.py` Redis-pattern niet extracted naar `klai-libs/webhook-replay` (commit `d3833a4a` mentions extraction maar nog niet adopted).
- **Attack scenario:** Attacker die kort webhook-payload onderschept (proxy compromise, log-leak, packet-capture) replays. Moneybird: `subscription_cancelled` → flips victim org `billing_status='cancelled'`; replay na operator-recovery re-cancelt. Vexa: meeting-completed payload replayed mid-meeting forces transcription/cleanup van active recording. Same-tenant integriteit-aanval (geen tenant-grens).
- **Recommendation:** Land SPEC-SEC-AUTH-HARDENING-001 `klai-libs/webhook-replay` adoption. Wire Moneybird (event_id) + Vexa (vexa_meeting_id+status+timestamp) naar Redis-backed nonce-store identiek aan mailer.
- **Confidence:** 90

### Finding C-3: invite_scheduler INSERT binnen `cross_org_session()`
- **Priority:** MED
- **Location:** [invite_scheduler.py:133-168](klai-portal/backend/app/services/invite_scheduler.py#L133)
- **Current situation:** `_join_meeting` opent `cross_org_session()` voor iCal-UID-dedup, dan **doet INSERT (`db.add(meeting); commit()`) in dezelfde session**. Comment documenteert alleen SELECT-scan. Met `vexa_meetings` Cat-C/D, INSERT onder `cross_org_admin=true` bypasst RLS-side cross-checks.
- **Attack scenario:** Niet direct exploiteerbaar; defense-in-depth gat. Future patch die attacker-controlled `org_id` in `_join_meeting` flowt (IMAP-listener bug die verified email's tenant misroute) → `cross_org_admin=true` → RLS accepteert INSERT tegen elke org_id zonder klacht.
- **Recommendation:** Split cross-org SELECT en per-tenant INSERT in twee sessions:
  ```python
  async with cross_org_session() as scan_db:
      existing = await scan_db.scalar(...)
      if existing is not None:
          return
  async with tenant_scoped_session(org_id) as db:
      meeting = VexaMeeting(...)
      db.add(meeting)
  ```
- **Confidence:** 70

### Finding C-4: portal-api lifespan stuck-detector opent un-tenanted session
- **Priority:** MED
- **Location:** [klai-portal/backend/app/main.py:60-69](klai-portal/backend/app/main.py#L60) (`_run_stuck_detector`)
- **Current situation:** `AsyncSessionLocal()` direct (`_AsyncSessionLocal as _AsyncSessionLocal`) → `reconcile_stuck_provisionings(reconcile_db)`. Geen `cross_org_session()`, geen `set_tenant`, geen `# cross-org-by-design:` comment.
- **Recommendation:** Wrap in `cross_org_session()` met comment, OF refactor `reconcile_stuck_provisionings` naar per-org iteratie met `tenant_scoped_session`.
- **Confidence:** 65

### Finding C-5: `/api/internal/connectors/{id}/finalize-delete` mist cross-org marker
- **Priority:** MED
- **Location:** [klai-portal/backend/app/api/internal_connectors.py:55-104](klai-portal/backend/app/api/internal_connectors.py#L55)
- **Current situation:** Auth via `X-Internal-Secret` (sound — `compare_digest`) maar SELECT/DELETE op `PortalConnector` enkel by `connector_id` — geen `org_id` filter, geen `set_tenant`, geen comment. Query slaagt omdat `portal_connectors` Cat-A is (`OR current_setting IS NULL` — empty GUC permissive). Functioneel correct (UUID), maar leunt op Cat-A permissiveness als impliciet vangnet.
- **Recommendation:** Voeg expliciete `# cross-org-by-design: ...` comment. Of: lookup connector eerst, dan `set_tenant(db, connector.org_id)` voor DELETE — maakt Cat-A reliance expliciet.
- **Confidence:** 75

### Finding C-6: klai-connector lifespan mass UPDATE zonder cross-org-tagging
- **Priority:** MED
- **Location:** [klai-connector/app/main.py:68-82](klai-connector/app/main.py#L68)
- **Current situation:** Lifespan opent `_db.session_maker()` en runt `UPDATE sync_runs SET status=PENDING WHERE status=RUNNING AND ...` over **elke tenant**. Comment 56-67 legt SPEC-reasoning uit maar gebruikt niet `# cross-org-by-design:` conventie. Per A-7: connector-schema heeft GEEN RLS — app-laag scoping is enige barrier.
- **Recommendation:** Voeg `# cross-org-by-design: startup recovery sweep across all tenants' sync_runs — runs once before HTTP traffic. Connector schema has no RLS (TP-5), so application-level intent is the only barrier.` toe. Bij SPEC-SEC-CONNECTOR-RLS-001 land: vervang met `cross_org_session()` semantics.
- **Confidence:** 75

### Finding C-7: `SyncRunReaper.tick()` cross-tenant scan ongetagged
- **Priority:** MED
- **Location:** [klai-connector/app/services/sync_run_reaper.py:103-194](klai-connector/app/services/sync_run_reaper.py#L103)
- **Current situation:** Background reaper opent `self._session_maker()` en runt `SELECT FROM sync_runs WHERE status==RUNNING AND ...` cross-tenant elke 5 minuten. Geen marker, geen RLS in schema. Per-row write via `with_for_update` re-fetch — OK — maar scan zelf is implicit cross-tenant.
- **Recommendation:** `# cross-org-by-design: ...` comment top of `tick()`. Long-term: post-RLS replace met `cross_org_session()`.
- **Confidence:** 75

### Finding C-8: scribe-api lifespan reaper scant alle rijen; Transcription heeft geen org_id
- **Priority:** LOW
- **Location:** [main.py:30-38](klai-scribe/scribe-api/app/main.py#L30) + [reaper.py:33-90](klai-scribe/scribe-api/app/services/reaper.py#L33)
- **Current situation:** `Transcription` model carries alleen `user_id`. Reaper `UPDATE transcriptions SET status='failed' WHERE status='processing' AND created_at < cutoff`. Strikt geen tenant-grens om te kruisen — maar dit betekent scribe-api is **structureel incapabel een tenant-grens te enforceren**. Cross-user-by-design ongedocumenteerd.
- **Recommendation:** (1) `# cross-user-by-design: Transcription has no org_id; if scribe needs per-tenant retention, add org_id first.` comment. (2) Track adding `org_id` aan `Transcription` als follow-up SPEC.
- **Confidence:** 65

### Finding C-10: Vexa webhook secret is global, niet per-tenant
- **Priority:** LOW
- **Location:** [meetings.py:48-89](klai-portal/backend/app/api/meetings.py#L48)
- **Current situation:** Single `vexa_webhook_secret` voor elke tenant. Iedereen met secret (Vexa support, dev met prod-env access, SOPS-reader) kan elke tenant's meeting-state driven. Tenant-resolutie via `vexa_meeting_id` (Vexa-issued int) → cross-org SELECT — sound — maar secret heeft geen tenant-binding.
- **Attack scenario:** Single secret leak = forge meeting-completed events voor elke tenant door scanning vexa_meeting_id values. Vexa-issued auto-increment → enumeration practical. Met C-9 (geen replay): one-time leak = durable cross-tenant write power.
- **Recommendation:** Per-tenant Vexa secrets niet feasible zonder Vexa-changes. Praktisch: (a) replay-protection (C-9) + (b) ACCEPT alleen events voor meetings in `ACTIVE_STATUSES` (huidige code doet dit voor platform+native_meeting_id branch, niet voor `vexa_meeting_id` branch).
- **Confidence:** 70

### Finding C-11: Token-based join-request approval geen per-org rate-limit
- **Priority:** LOW
- **Location:** [klai-portal/backend/app/api/admin/join_requests.py:91-128](klai-portal/backend/app/api/admin/join_requests.py#L91)
- **Current situation:** `?token=` pad unauthenticated. Barriers = token-validity + 24h expiry + status==pending. Geen rate-limit op forge-attempts. `verify_approval_token` HMAC — onhaalbaar te forgeren met secret, maar bij configuratie-drift (low-entropy ASCII) → unbounded brute-force.
- **Recommendation:** Per-IP rate-limit (existing partner_rate_limit Redis sliding-window helper), low budget (10/hour). Log failed verify op WARNING met request_id voor VictoriaLogs detection.
- **Confidence:** 60

## C.4 Confidence

Overall: **80**.
- Wel: 100% van `/webhook` + `/callback` routes; 100% van FastAPI lifespan-handlers; 100% van `cross_org_session` call-sites; 100% van `while True` loops; alle `app/api/admin/*.py` endpoints gesampled.
- Niet: live `pg_policies`/`relrowsecurity` checks; volledige `BackgroundTasks.add_task(...)` trace; line-by-line read van `_validate_callback_url`/IDP signup callback.

---

## Synthese: cross-finding observaties

### 1. Drie services zonder DB-laag bescherming = systemisch risico
A-7 (connector), A-8 (knowledge), A-10 (research) zijn niet drie losse findings — het is één klasse: SPEC-AUTH-006/007 RLS rolde alleen portal-api uit. De rest is "mocht volgen". Praktisch impact:
- Elke handler-bug die `WHERE org_id = ...` vergeet = direct cross-tenant. Geen DB-laag waarschuwing. Geen `RLS_GUARD_STRICT` log-event.
- Knowledge service is bovendien **target van A-13** (body-trust): zelfs zonder bug is de aanvalsoppervlakte een internal-secret-houder die `org_id` in body kiest.
- Research service is **target van A-12** (auth-resolver): legitiem-multi-org-user → silent cross-tenant.

### 2. Internal-secret model is overstretched
Zeven services delen één `INTERNAL_SECRET`. Findings A-13, B-2, B-6, B-8, en latent C-5 leunen allemaal op "trust the caller because they have the secret". SPEC-SEC-IDENTITY-ASSERT-001 is gestart maar dekt vandaag alleen retrieval-api `/retrieve` (per pitfall-rule). Volledige uitrol over knowledge-ingest, mailer, internal callbacks is de mitigatie.

### 3. Multi-org users zijn onvoldoende afgehandeld in research-api
A-12 is geen bug op zich; het is een gat waar SPEC-AUTH-006 (multi-org users in portal-api) nog niet doorgekomen is naar research-api. Combinatie met A-10 (geen RLS) maakt dit een live risico.

### 4. Garage public-read is een eigen klasse risico
B-4 staat los van alle andere findings. Het is een ontwerpkeuze (anonieme Caddy-proxy naar `/kb-images/*` voor cache + dedup) die op individuele KB-images werkt maar niet op tenant-isolation past. Een gelekte URL is een permanente cross-tenant lees. Dit is geen "per refactor te fixen" — het vereist architectuur-keuze.

### 5. Webhook fail-open + Gitea spoof samen
C-1 is twee defects in één endpoint. De fail-open (TP-W2) is een hygiëne-fix (validator). De Gitea-spoof via org-description is een design-flaw die alleen door bron-van-waarheid-verschuiving op te lossen is.

### 6. Alle CRIT/HIGH zijn statische defecten — geen externe-attacker-only paden
Behalve C-9 (replay) vereist geen finding netwerk-positie. Alles is exploiteerbaar door een legitieme account met de juiste rol/scope (tenant-admin voor C-2, multi-org user voor A-12, internal-secret-houder voor A-13/B-6, etc.). Dat is bemoedigend (geen externe-attacker-paths) maar onderstreept dat insiders en gecompromitteerde services het primaire dreigingsmodel zijn.

---

## Confidence summary (gehele audit)

**Overall: 78.**
- Sectie A: 82
- Sectie B: 70
- Sectie C: 80

Sterk geanchored: alle findings hebben file:line evidence. Geen speculation zonder anchor.

Wat we niet konden bereiken:
- Live `pg_policies` / `pg_class.relrowsecurity` queries (verifieert prod-state versus alembic-code).
- Live Qdrant payload-key inspectie op productie data (klai_knowledge.org_id type-confirmation).
- Live MongoDB per-tenant user RBAC test.
- Volledig `_require_internal_token`-gated endpoint inventaris.

Aanbeveling: bij implementatie van fixes ALTIJD een live drift-check op productie als laatste stap (`docker exec klai-core-postgres-1 psql -c "SELECT tablename, rowsecurity, forcerowsecurity FROM pg_tables JOIN pg_class ON ..."` etc.).
