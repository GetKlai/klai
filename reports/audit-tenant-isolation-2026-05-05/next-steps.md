# Next Steps — Tenant Isolation Audit 2026-05-05

Geprioriteerde actielijst. Maximaal 10 items, geclusterd zodat één SPEC ≥ één agent ≥ één PR.

**Context:** systeem heeft op moment van schrijven geen actieve users — geen rolling-deploy nodig, geen backfill. Snelheid > soak-tijd, mits codekwaliteit gehouden.

---

## 1. CRIT: `/api/admin/orgs/{slug}/retry-provisioning` platform-admin gate

**Finding:** C-2
**SPEC:** `SPEC-TI-001-RETRY-PROVISIONING-GATE`
**Action:** voeg `_require_platform_admin(_caller_org)` toe aan handler. Audit-log naar `tenant_lifecycle_events`. Test toevoegen die niet-platform-admin → 403.
**Risk:** 1-line change. Test-only impact.
**Owner:** agent in worktree `klai-retry-prov-gate`.

---

## 2. HIGH: RLS rollout op connector schema

**Finding:** A-7
**SPEC:** `SPEC-TI-002-RLS-CONNECTOR` (vervangt SPEC-SEC-CONNECTOR-RLS-001 of bouwt erop)
**Action:**
- Migratie: `_rls_current_org_id() RETURNS text` helper-functie + ENABLE/FORCE + Cat-D policies op `connector.connectors` + `connector.sync_runs`
- Post-deploy SQL in `klai-connector/alembic/versions/post_deploy_<rev>.sql`
- Sessie-helpers (`set_tenant`, `tenant_scoped_session`, `cross_org_session`) kopiëren naar `klai-connector/app/core/database.py`
- `routes/sync.py`, `routes/internal.py`, `services/sync_run_reaper.py`, `app/main.py` lifespan: vervang `session_maker()` met passende helper
- Pin `sync_require_org_id=True` (al prod-default)
- `# cross-org-by-design:` comment op lifespan + reaper
- Tests: unit-tests die RLS enforcement bewijzen
**Owner:** agent in bestaande worktree `klai-connector-rls`.

---

## 3. HIGH: RLS rollout op knowledge schema

**Finding:** A-8 + A-13 (gecombineerd: RLS + identity-assertion op ingest-endpoints)
**SPEC:** `SPEC-TI-003-RLS-KNOWLEDGE`
**Action:**
- Helper-function `_rls_current_org_id() RETURNS text` (org_id is text in dit schema)
- ENABLE/FORCE + Cat-D op alle 9 + 4 junctions (totaal 13 tabellen)
- Sessie-helpers naar `klai-knowledge-ingest/knowledge_ingest/db.py` (asyncpg-based; helper iets anders dan SQLAlchemy)
- Async-context-manager patterns voor procrastinate tasks (`crawl_tasks.py`, `enrichment_tasks.py`, `rebuild_tasks.py`)
- Identity-assertion via `klai-libs/identity-assert` op alle endpoints die `org_id` uit body/query nemen (`/ingest/v1/start`, `/ingest/v1/kb/webhook`, `/ingest/v1/graph-stats`, etc.)
- Sender-side: portal-api callers MOETEN `X-Caller-Service` header zetten
- Voeg `entrypoint.sh` toe aan `klai-knowledge-ingest` (auto-migrate, anders pitfall `alembic-stamped-past-skipped-migration`)
- Tests
**Owner:** agent in worktree `klai-knowledge-rls`.

---

## 4. HIGH: RLS + auth-resolver fix op research schema

**Finding:** A-10 + A-11 + A-12
**SPEC:** `SPEC-TI-004-RLS-RESEARCH`
**Action:**
- ALTER `research.chat_messages.tenant_id` van VARCHAR(64) naar UUID
- Helper-function `_rls_current_org_id() RETURNS uuid`
- ENABLE/FORCE + Cat-D op 4 research-tabellen
- Sessie-helpers naar `klai-focus/research-api/app/db.py`
- **A-12 fix:** `_get_user_org` in `app/core/auth.py` MOET JWT `urn:zitadel:iam:org:project:resourceowner` claim als bron-van-waarheid gebruiken (zie standards-doc sectie 10)
- `_get_notebook_or_404` MOET expliciete `tenant_id` check op personal-scope branch
- `entrypoint.sh` voor auto-migrate
- Tests inclusief multi-org-user regressie-test
**Owner:** agent in worktree `klai-research-rls`.

---

## 5. HIGH: portal-api RLS hygiëne-batch

**Finding:** A-1, A-2, A-3, A-4, A-5, A-6
**SPEC:** `SPEC-TI-005-RLS-HYGIENE-PORTAL`
**Action:** één post-deploy SQL `post_deploy_<rev>_tenant_isolation_hygiene.sql`:
- A-1: upgrade Cat-A policies op `portal_users` + `portal_connectors` met expliciete `WITH CHECK (org_id = ...)` (zonder IS-NULL)
- A-2: voeg subquery-policy toe op `portal_group_memberships`
- A-3: ENABLE/FORCE op `partner_api_keys` + `partner_api_key_kb_access` + startup-assertion in `app/core/database.py` analoog aan `assert_portal_users_rls_ready`
- A-4: FORCE toevoegen aan `portal_feedback_events`, `widgets`, `widget_kb_access`, `tenant_lifecycle_events`
- A-5: vervang `WITH CHECK (true)` op audit-tables door `current_setting('app.current_org_id', true) = '' OR org_id = NULLIF(...)::int`
- A-6: doc + assertion voor `tenant_lifecycle_events` GUC-pattern
- Tests: per policy een fail-loud regression test
**Owner:** agent in worktree `klai-portal-rls-hygiene`.

---

## 6. HIGH: Webhook replay-protection adoption

**Finding:** C-9 (Moneybird + Vexa) + C-10 (Vexa secret tightening)
**SPEC:** `SPEC-TI-006-WEBHOOK-REPLAY-ADOPTION`
**Action:**
- Adopt `klai-libs/webhook-replay` in `klai-portal/backend/app/api/webhooks.py` (Moneybird) — nonce-parts `(event_id, timestamp)`
- Adopt in `klai-portal/backend/app/api/meetings.py` (Vexa) — nonce-parts `(vexa_meeting_id, status, timestamp)`
- Adopt in `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` (Gitea) — nonce-parts `(delivery_id,)` (X-Gitea-Delivery header)
- Vexa: ACCEPT alleen events voor meetings in `ACTIVE_STATUSES` op de `vexa_meeting_id` branch (vandaag alleen op de platform+native_meeting_id branch)
- Tests per webhook
**Owner:** agent in worktree `klai-webhook-replay`.

---

## 7. HIGH: Gitea webhook fail-closed + tenant-spoof fix

**Finding:** C-1
**SPEC:** `SPEC-TI-007-GITEA-WEBHOOK-HARDEN`
**Action:**
- `@field_validator("gitea_webhook_secret", mode="after")` rejecting empty/whitespace (mirror van `_require_moneybird_webhook_token`)
- **Pre-flight:** verify `GITEA_WEBHOOK_SECRET` exists in `klai-infra/core-01/.env.sops` BEFORE landing validator
- Vervang `_get_org_id` lookup van Gitea-org-description door server-side mapping. Twee opties:
  - **Optie A (gekozen):** nieuwe tabel `knowledge.gitea_repo_to_org` (zie standards-doc sectie 15)
  - Optie B: server-side `org_config.gitea_repo_mapping` JSONB
- Migratie + post-deploy SQL voor de mapping-tabel
- Beheerd via portal-api admin endpoint of automatisch bij Gitea-connector-creation
- Tests
**Owner:** agent in worktree `klai-gitea-harden`.

---

## 8. HIGH: retrieval-api router cross-tenant centroid contamination

**Finding:** B-1
**SPEC:** `SPEC-TI-008-RETRIEVAL-ROUTER-FIX`
**Action:**
- Voeg `org_id` parameter toe aan `_default_compute_centroids(catalog, org_id)` in `klai-retrieval-api/retrieval_api/services/router.py`
- Voeg `FieldCondition(key="org_id", match=MatchValue(value=org_id))` toe aan scroll-filter
- Cache-key blijft `_centroid_cache[org_id]` — corrigeert
- Test: twee tenants met overlappende source_labels, assert centroids verschillen
**Owner:** agent in worktree `klai-router-fix`.

---

## 9. MED: Garage KB-image auth-proxy (B-4)

**Finding:** B-4
**SPEC:** `SPEC-TI-009-GARAGE-AUTH-PROXY`
**Action:**
- **Architectuur-keuze (gemaakt):** Optie A — auth-proxy via portal-api endpoint
- Nieuwe portal-api route `GET /kb-images/{org_id}/{kb_slug}/{filename}` die:
  - User-session of widget-public-allowlist verifieert
  - Streamt vanuit Garage S3 API (private, authenticated; niet website-mode)
- Caddy config: `handle_path /kb-images/*` reverse-proxy naar portal-api in plaats van garage:3902
- Garage bucket: zet website-mode UIT, S3 API blijft authenticated
- Migration plan: oude image-URLs blijven werken via fallback redirect; nieuwe images via auth-proxied path
- Cache headers (CDN-friendly): `Cache-Control: private, max-age=86400` (24h binnen sessie)
- Tests
**Owner:** agent in worktree `klai-garage-proxy`.

**Belangrijk:** dit is de enige finding waar architectuur-keuze nodig was. Optie A gekozen omdat het de strongest-isolation pattern is en geen presigned-URL TTL micro-management vereist.

---

## 10. MED batch: cross-org markers + Redis hygiëne + scribe org_id + B-2 fix

**Findings:** B-2, B-5, B-9, B-10, A-9, C-3, C-4, C-5, C-6, C-7, C-8, B-7
**SPEC:** `SPEC-TI-010-CLEANUP-BATCH`
**Action:** parallel sub-tasks:

a. **Cross-org markers** (C-3..C-8): comments toevoegen + (waar mogelijk) `cross_org_session()` adoption. C-3: split scan en INSERT in twee sessies. Geen nieuwe gedrags-changes — alleen safety-clarification.

b. **Redis cache key consistency** (B-2, B-5):
- B-2: `klai-portal/backend/app/api/app_knowledge_bases.py:1228` van `str(org.id)` → `org.zitadel_org_id`
- B-5: `klai-portal/backend/app/services/litellm_cache.py:31-36` + `app_account.py:33-53,203` van int → `zitadel_org_id`

c. **Redis hygiëne** (B-9, B-10):
- B-9: `fb:{org.id}:{conversation_id}:{message_id}`
- B-10: extend `_flush_redis_tenant_keys` met alle tenant-namespaces (zie standards-doc 14)

d. **Scribe org_id** (A-9):
- ALTER `scribe.transcriptions` ADD COLUMN `org_id varchar(255)`
- Filter `(user_id, org_id)` op alle endpoints
- Cat-D RLS policy
- Use JWT `resourceowner` als source

e. **B-7**: `qdrant_store.delete_by_source` + `delete_by_notebook` + backfill-script: voeg `tenant_id: str` parameter toe en filter ook daarop.

f. **C-11**: per-IP rate-limit op token-approve path + log failed verifies op WARNING.

g. **B-8**: extend identity-assertion adoption (item 3) naar `/ingest/v1/graph-stats` + `/ingest/v1/source-count`.

h. **B-6**: `feature_knowledge` endpoint refactor naar Mongo-driven lookup zonder root-URI cross-tenant pivot.

**Owner:** drie sub-agents (a+b+c, d+e, f+g+h) parallel in eigen worktrees.

---

## Wat we NIET doen vannacht

| Item | Reden |
|---|---|
| MeiliSearch shared master-key uitsplitsen | Geen finding in deze audit; was prior-audit hygiëne. Aparte SPEC. |
| Connector-OAuth PKCE rollout (TP-O1) | Niet tenant-isolation-finding; aparte SPEC. |
| Caddy `--delete` flag voor rsync provisioning | Aparte infra-SPEC. |
| Volledige IAM-bucket-policy rollout op Garage | Optie B (alternatief voor B-4); pas overwegen als auth-proxy bottleneck wordt. |
| Live `pg_policies` drift-check op productie | Vereist SSH + DB-toegang die ik niet autonoom mag uitvoeren. Operator-step in elke RLS PR. |

---

## Volgorde van uitvoering vannacht

**Fase 1 (immediate, sequential):**
1. CRIT C-2 fix → PR (10 min)
2. SPEC-files schrijven voor #2-#10 (20-30 min)

**Fase 2 (parallel, eerste batch):**
3. RLS-CONNECTOR + RLS-KNOWLEDGE + RLS-RESEARCH + RLS-HYGIENE-PORTAL agents (parallel, 4 worktrees)

**Fase 3 (parallel, tweede batch — start zodra fase 2 klaar):**
4. WEBHOOK-REPLAY + GITEA-HARDEN + RETRIEVAL-ROUTER + GARAGE-PROXY agents (parallel, 4 worktrees)

**Fase 4 (parallel, derde batch):**
5. CLEANUP-BATCH (3 sub-agents)

**Fase 5 (synthesis):**
6. RESULTS.md schrijven met PR-list + status-per-cluster + open issues voor user

**Realistische completion:** fase 1 + 2 = vannacht zeker; fase 3 + 4 = waarschijnlijk; fase 5 + sommige cleanup = morgen ochtend.
