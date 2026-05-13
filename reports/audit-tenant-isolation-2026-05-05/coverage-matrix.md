# Coverage Matrix — Tenant Isolation 2026-05-05

Per tabel / store / endpoint: welke laag(en) bescherming zijn aanwezig? Eén oogopslag wat WEL en NIET door RLS / cryptografische identity / app-filter is afgedekt.

**Legenda:**
- ✅ = volledig afgedekt
- 🟡 = partial / convention-based / drift-risk
- ❌ = geen bescherming
- n/a = niet van toepassing

---

## Postgres — public schema (portal-api)

| Tabel | Tenant col | DB-laag (RLS) | App-laag (`set_tenant`) | Cross-org marked? | Issues |
|---|---|---|---|---|---|
| portal_orgs | id (root) | n/a | n/a | n/a | Root tenant table |
| portal_users | org_id (int) | 🟡 Cat-A | ✅ | ✅ | A-1 (USING reused as WITH CHECK) |
| portal_user_products | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_user_kb_access | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_connectors | org_id (int) | 🟡 Cat-A | ✅ | ✅ | A-1 (USING reused) |
| portal_knowledge_bases | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_kb_tombstones | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_groups | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_group_products | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_group_kb_access | (via kb_id) | ✅ junction | ✅ | ✅ | — |
| **portal_group_memberships** | (via group_id) | ❌ ENABLE only, no policy | ✅ helpers | ✅ | **A-2 HIGH** |
| portal_retrieval_gaps | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| portal_taxonomy_nodes | (via kb_id) | ✅ junction | ✅ | ✅ | — |
| portal_taxonomy_proposals | (via kb_id) | ✅ junction | ✅ | ✅ | — |
| portal_templates | org_id (int) | ✅ Cat-D | ✅ | ✅ | — |
| vexa_meetings | org_id (nullable int) | ✅ hybrid C+D | ✅ | ✅ | — |
| **partner_api_keys** | org_id (int) | 🟡 ENABLE/FORCE in docstring only | ✅ Cat-B | ✅ | **A-3 HIGH** |
| **partner_api_key_kb_access** | (via partner_api_key_id) | 🟡 ENABLE/FORCE in docstring only | ✅ junction | ✅ | **A-3 HIGH** |
| **widgets** | org_id (int) | 🟡 ENABLE only, no FORCE | ✅ Cat-B | ✅ | **A-4 MED** |
| **widget_kb_access** | (via widget_id) | 🟡 no FORCE | ✅ junction | ✅ | **A-4 MED** |
| **portal_audit_log** | org_id (int) | 🟡 INSERT WITH CHECK (true) | ✅ Cat-C | ✅ | **A-5 MED** (audit-integrity) |
| **product_events** | org_id (nullable int) | 🟡 INSERT WITH CHECK (true) | ✅ Cat-C | ✅ | **A-5 MED** |
| **portal_feedback_events** | org_id (int) | 🟡 INSERT (true) + no FORCE | ✅ Cat-C | ✅ | **A-4 + A-5 MED** |
| **tenant_lifecycle_events** | org_id_snapshot | 🟡 INSERT (true) + no FORCE + GUC-reliance | ✅ Cat-C | ✅ | **A-4 + A-5 + A-6 MED** |
| portal_join_requests | org_id (nullable int) | ✅ Cat-A explicit | ✅ | ✅ | Fixed PR #364 |

**Samenvatting portal-api:** 17 tabellen volledig afgedekt; 4 tabellen met MED-niveau hygiëne-gat; 1 tabel HIGH (group_memberships); 2 tabellen HIGH (partner_api_keys ENABLE-status onbekend op prod).

---

## Postgres — connector schema (klai-connector)

| Tabel | Tenant col | DB-laag (RLS) | App-laag | Cross-org marked? | Issues |
|---|---|---|---|---|---|
| **connector.connectors** | org_id (varchar) | ❌ NONE | ✅ filters in `routes/sync.py:94` | ❌ lifespan ungetagged | **A-7 HIGH** |
| **connector.sync_runs** | org_id (varchar, nullable) | ❌ NONE (in flight: SPEC-SEC-CONNECTOR-RLS-001) | 🟡 conditional filter (env flag) | ❌ reaper ungetagged | **A-7 HIGH** + C-6 + C-7 |

**Samenvatting connector:** ZERO RLS. Alleen app-filters. Eén refactor verwijderd van leak.

---

## Postgres — knowledge schema (klai-knowledge-ingest)

| Tabel | Tenant col | DB-laag (RLS) | App-laag | Issues |
|---|---|---|---|---|
| **knowledge.artifacts** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.entities** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.crawl_domains** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.crawl_jobs** | org_id (text) | ❌ | 🟡 body-trusted (A-13) | **A-8 + A-13 HIGH** |
| **knowledge.crawled_pages** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.kb_config** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.org_config** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.page_links** | org_id (text) | ❌ | ✅ | **A-8 HIGH** |
| **knowledge.parent_chunks** | org_id (text) | ❌ | ✅ via cascade artifact_id | **A-8 HIGH** |
| knowledge.artifact_entities | (via artifact_id) | ❌ junction | ✅ | A-8 (junction) |
| knowledge.artifact_images | (via artifact_id) | ❌ junction | ✅ | A-8 (junction) |
| knowledge.derivations | (via parent/child_id) | ❌ junction | ✅ | A-8 (junction) |
| knowledge.embedding_queue | (via artifact_id) | ❌ no own col | ✅ | A-8 (junction) |
| knowledge.rag_eval_results | — | n/a | n/a | analytics, no tenant |

**Samenvatting knowledge:** ZERO RLS op grootste data-bearing surface. Plus body-trust op start_crawl. Hoogste single-cluster impact.

---

## Postgres — scribe schema (klai-scribe)

| Tabel | Tenant col | DB-laag (RLS) | App-laag | Issues |
|---|---|---|---|---|
| **scribe.transcriptions** | user_id only (no org_id) | ❌ no policy | ✅ user_id filter | **A-9 MED** (geen tenant-grens mogelijk) |

**Samenvatting scribe:** structureel incapabel tenant-grens te enforceren. User-move tussen orgs lekt.

---

## Postgres — research schema (klai-focus/research-api)

| Tabel | Tenant col | DB-laag (RLS) | App-laag | Issues |
|---|---|---|---|---|
| **research.notebooks** | tenant_id (UUID) | ❌ | 🟡 personal-scope mist tenant_id check | **A-10 + A-12 HIGH** |
| **research.sources** | tenant_id (UUID) | ❌ | ✅ via notebook | **A-10 HIGH** |
| **research.chunks** | tenant_id (UUID) | ❌ | ✅ via source | **A-10 HIGH** |
| **research.chat_messages** | tenant_id (**VARCHAR(64)**) | ❌ | ✅ | **A-10 + A-11 MED** |

**Samenvatting research:** ZERO RLS + auth-resolver bug (A-12) = direct exploiteerbaar door multi-org user.

---

## Postgres — overige services

| Service | Tabellen met tenant data | Status |
|---|---|---|
| klai-mailer | — | Stateless, geen DB-state. n/a |
| klai-retrieval-api | — | Geen eigen tabellen; INSERTs op product_events | ✅ |
| klai-knowledge-mcp | — | Stateless | n/a |

---

## Externe data-stores

### Qdrant

| Collection | Tenant key | Type | Filter-discipline | Issues |
|---|---|---|---|---|
| **klai_knowledge** | `org_id` payload | string (Zitadel) | 🟡 B-1 router scrolt zonder filter | **B-1 HIGH** |
| **klai_focus** | `tenant_id` payload | string (Zitadel) | 🟡 B-7 delete_by_source filtert UUID-only | B-7 LOW |

**Samenvatting Qdrant:** twee collections, twee verschillende keys (oorzaak van #343, gefixt). Routing-bug (B-1) is hot-path leak. App-only barrier — geen Qdrant-side ACL beschikbaar.

### FalkorDB / Graphiti

| Aspect | Status |
|---|---|
| Per-org graph isolation via `select_graph(org_id)` | ✅ |
| `group_id` property op nodes | ✅ |
| Geen FalkorDB-side ACL | App-only barrier |
| Spec-AUTH-006 group_id consistency | ✅ |

**Samenvatting FalkorDB:** sterke fysieke isolatie via per-graph; combineerd met Graphiti's group_id filter voor defense-in-depth. Geen findings.

### Garage S3

| Bucket | Tenant key | Auth-laag | Issues |
|---|---|---|---|
| **klai-images** (KB-images) | object-key prefix `{org_id}/...` | ❌ **anoniem publiek leesbaar via Caddy** | **B-4 MED** |
| klai-scribe (transcripts) | object-key prefix `{slug}/...` | ✅ S3 API + shared access-key | App-only |

**Samenvatting Garage:** KB-images is structureel risico (gelekte URL = permanente cross-tenant lees). Scribe is app-only maar niet publiek.

### Redis

| Namespace | Tenant key in path? | Producer/consumer match? | Issues |
|---|---|---|---|
| `configs:{slug}:*` | ✅ | ✅ | Geflusht op deprovision |
| `sess:*` | (per-user, niet per-tenant) | n/a | ✅ |
| `templates:{X}:*` | 🟡 writer Zitadel-string, invalidator int | ❌ | **B-5 MED** |
| `kb_ver:{X}:*` | 🟡 zelfde mismatch | ❌ | **B-5 MED** |
| `kb_feature:*` | ✅ | ✅ | Niet geflusht op deprovision (B-10 LOW) |
| `rl:*` | ✅ | ✅ | Niet geflusht (B-10 LOW) |
| `partner_rl:*` | ✅ | ✅ | Niet geflusht (B-10 LOW) |
| `connector_rl:*` | ✅ | ✅ | Niet geflusht (B-10 LOW) |
| `fb:{message_id}:{conversation_id}` | ❌ | n/a | **B-9 LOW** |
| `signup_email_rl:*` | ✅ | ✅ | — |
| `identity_verify:*` | ✅ via klai_identity_assert | ✅ | — |
| `totp_pending:*` | ✅ | ✅ | — |
| `mailer:nonce:*` | (per-event, niet per-tenant) | n/a | ✅ — replay-protection |
| `partner_rl:*` | ✅ | ✅ | — |

**Samenvatting Redis:** mostly OK met tenant-prefixing. B-5 cache-invalidation mismatch is grootste functioneel risico (silent stale). B-10 flush-incomplete is hygiëne.

### MongoDB

| Aspect | Status |
|---|---|
| Per-tenant DB `librechat-{slug}` | ✅ |
| Per-tenant Mongo-user met `readWrite` only on own DB | ✅ |
| Container-level isolation per tenant | ✅ |
| portal-api `feature_knowledge` query gebruikt root URI | 🟡 **B-6 MED** (caller-supplied org_id query-param) |

**Samenvatting MongoDB:** sterkste isolatie in audit. DB-level RBAC + container-isolation. Eén lekpad via internal-secret-protected `/internal/.../feature/knowledge`.

---

## Webhook + OAuth callbacks

| Endpoint | Auth | Tenant resolutie | Replay-protection | Spoofbaar? |
|---|---|---|---|---|
| `POST /api/webhooks/moneybird` | ✅ HMAC + validator + compare_digest | Body `entity.id` → DB lookup | ❌ **C-9 HIGH** | Niet (HMAC), wel replay |
| `POST /api/bots/internal/webhook` (Vexa) | ✅ Bearer + validator + compare_digest | Body `vexa_meeting_id` → cross_org SELECT | ❌ **C-9 HIGH** | Niet (HMAC), wel replay; **C-10 LOW** global secret |
| `POST /ingest/v1/webhook/gitea` | 🟡 **HMAC fail-open if empty** + validator missing | Body `repository.full_name` → **Gitea description spoofbaar** | ❌ | **C-1 HIGH** |
| `POST /notify` (Mailer Zitadel) | ✅ HMAC + Redis nonce-store | Zitadel-signed `recipient_email()` | ✅ | Niet |
| `POST /internal/send` (Mailer) | ✅ X-Internal-Secret + recipient binding | Template-derived expected recipient | ✅ idempotent | Niet |
| `GET /api/auth/oidc/callback` | ✅ State (single-use) + PKCE S256 | JWT `sub` → portal_users | ✅ | Niet |
| `GET /auth/idp-callback` | ✅ Zitadel intentId/intentToken | Zitadel session details | ✅ | Niet |
| `GET /api/oauth/{provider}/callback` | ✅ Fernet state cookie + compare_digest | state.connector_id → DB lookup | ✅ | Niet |
| `POST /api/admin/join-requests/{id}/approve?token=` | ✅ HMAC token | DB row | ❌ **C-11 LOW** | Niet praktisch |
| `POST /ingest/v1/kb/webhook` | ✅ X-Internal-Secret | Caller-supplied | ⚠️ trust caller | A-13/B-8 class |
| `POST /api/internal/connectors/{id}/finalize-delete` | ✅ X-Internal-Secret + compare_digest | connector_id (UUID) | ✅ idempotent | C-5 (cosmetic) |

**Samenvatting webhooks:**
- Mailer + OAuth callbacks: ✅ goud-standaard
- Moneybird + Vexa: 🟡 missen replay-protection (C-9)
- Gitea: ❌ compounded fail-open + tenant-spoof (C-1)
- Internal endpoints met body-org_id: ⚠️ identity-assertion uitrol nodig (A-13/B-2/B-6/B-8 class)

---

## Cross-org-by-design sites

| Site | Service | Marker present? | Risk |
|---|---|---|---|
| `bot_poller.py:154-164` | portal | ✅ helper + comment | OK |
| `recording_cleanup.py:130-138` | portal | ✅ helper + comment | OK |
| `invite_scheduler.py:64-67/100-104/130-133` | portal | ✅ helper + comment | C-3 (INSERT in scan-session) |
| `meetings.py:704-708` (Vexa lookup) | portal | ✅ helper | OK |
| `deprovisioning_orchestrator/steps.py` | portal | ✅ set_tenant + comment | OK |
| `tenant_host.py:116` (slug cache) | portal | ❌ ungetagged | LOW |
| `events.py:43-56` (`emit_event`) | portal | ❌ ungetagged | A-5 related |
| `audit/__init__.py:56-68` (`log_event`) | portal | 🟡 docstring only | A-5 related |
| `internal.py:195-207` (`_log_internal_call`) | portal | 🟡 comment only | A-5 related |
| `provisioning/orchestrator.py:215` | portal | ❌ ungetagged | LOW |
| `tenant_matcher.py:73` | portal | ❌ ungetagged | LOW |
| `auth.py:355` (OIDC pre-callback) | portal | ❌ ungetagged | LOW |
| `main.py:64` (lifespan stuck-detector) | portal | ❌ ungetagged | **C-4 MED** |
| `internal_connectors.py:55` (finalize-delete) | portal | ❌ ungetagged | **C-5 MED** |
| `klai-connector/main.py:68-82` (lifespan UPDATE) | connector | ❌ ungetagged | **C-6 MED** |
| `sync_run_reaper.py:103` | connector | ❌ ungetagged | **C-7 MED** |
| `scribe/main.py:30 + reaper.py:33` | scribe | ❌ ungetagged | **C-8 LOW** |
| `knowledge-ingest/backfill.py:45` | knowledge | ❌ ungetagged + random-tenant pick | LOW |

---

## Samenvatting per service (single-row "wat is er nodig")

| Service | RLS | Identity-assertion | Cross-org markers | Webhooks | Andere |
|---|---|---|---|---|---|
| klai-portal/backend | ✅ mostly (5 hygiëne-fixes) | ✅ sender side | 🟡 7 sites ungetagged | 🟡 Moneybird/Vexa replay | C-2 CRIT, B-4 garage proxy |
| klai-connector | ❌ zero (in flight) | ✅ via portal-api | ❌ 2 reapers ungetagged | n/a | — |
| klai-knowledge-ingest | ❌ zero | ❌ alleen INTERNAL_SECRET | ❌ ungetagged | 🟡 Gitea fail-open + spoof | A-13 body-trust |
| klai-knowledge-mcp | n/a | ✅ (live adopter) | n/a | n/a | — |
| klai-focus/research-api | ❌ zero | 🟡 sender, niet receiver | ❌ ungetagged | n/a | A-12 auth-resolver bug |
| klai-mailer | n/a | ✅ (live adopter) | n/a | ✅ goud-standaard | — |
| klai-retrieval-api | n/a | ✅ receiver (live) | n/a | n/a | B-1 router-contamination |
| klai-scribe | ❌ no policy + geen tenant-col | ✅ (live adopter) | ❌ ungetagged | n/a | A-9 mis org_id-kolom |

**De grootste-bang-for-buck cluster:** RLS-rollout op connector + knowledge + research schemas (A-7/A-8/A-10) + identity-assertion uitbreiding naar knowledge-ingest endpoints (A-13/B-2/B-6/B-8). Beide zijn pattern-replicatie van bestaande klai-libs en behandelen ~60% van de findings.
