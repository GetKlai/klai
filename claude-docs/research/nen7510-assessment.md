# NEN 7510 Compliance Assessment — Klai AI Platform

> Code-gevalideerde analyse. Elke claim is geverifieerd in de broncode.
> Datum: 2026-03-27 | Bijgewerkt: 2026-03-27 (SPEC-SEC-001 afgerond)

---

## Samenvatting

NEN 7510 is de Nederlandse norm voor informatiebeveiliging in de zorg (ISO 27001 + zorgsectorspecifieke aanvullingen). Dit document bevat een code-gevalideerde beoordeling van de Klai compliance-status.

**Totaalplaatje (na SPEC-SEC-001, 2026-03-27):**
- Technische fundering (netwerk, auth, crypto, audit): ~80–85% geïmplementeerd _(was ~60–65%)_
- Documentatie / formeel beleid: ~30%
- Operationele processen (DR, incident response): ~35% _(was ~25%, door geautomatiseerde backups + monitoring)_

**Afgerond via SPEC-SEC-001:** audit logging voor alle auth-events, MFA backend enforcement, logging middleware volgorde, audit log append-only (DB-niveau), audit log RLS, geautomatiseerde dagelijkse backups met off-site opslag en monitoring.

---

## Authenticatie & toegangsbeveiliging

### Wat er is (bevestigd in code)

**Zitadel OIDC** ([portal/backend/app/api/auth.py](../../../portal/backend/app/api/auth.py)):
- PKCE-enabled login via custom login UI
- TOTP (Google Authenticator-compatibel) — setup, confirm, login volledig uitgewerkt
- Passkeys / WebAuthn — setup en confirm endpoints aanwezig
- Email OTP — volledig uitgewerkt
- TOTP lockout na 5 mislukte pogingen (`_TOTP_MAX_FAILURES = 5`)
- SSO cookie: Fernet-versleuteld (AES-128-CBC-HMAC-SHA256), scoped op `.getklai.com`, httponly + secure + samesite=lax
- Open redirect preventie: `_validate_callback_url()` controleert of callback-URL op eigen domein valt

**RBAC** ([portal/backend/app/api/dependencies.py](../../../portal/backend/app/api/dependencies.py)):
- Rollen: `admin`, `group-admin`, `member`
- Systeemgroepen alleen beheerbaar door org admin
- `_get_caller_org()` valideert Bearer token live bij Zitadel (geen lokale cache)

**MFA-beleid** ([portal/backend/app/models/portal.py](../../../portal/backend/app/models/portal.py)):
- `mfa_policy` kolom op `portal_orgs`: `optional` / `recommended` / `required`
- Default: `optional`

### Kritieke nuancering

~~**`mfa_policy = "required"` wordt NIET backend-afgedwongen bij login.**~~ **✅ Opgelost (SPEC-SEC-001 Fix 2, 2026-03-27)**

De login-endpoint controleert nu het `mfa_policy` van de organisatie en weigert bij `"required"` als de gebruiker geen TOTP én geen passkey heeft. HTTP 403 met `"MFA required by your organization. Please set up two-factor authentication."` Bij lookup-fout wordt `"optional"` aangenomen (fail-open, logged als warning).

---

## Versleuteling

### Transport (bevestigd in Caddyfile)

- Wildcard TLS (`*.getklai.com`) via Let's Encrypt + Hetzner DNS plugin
- HSTS: `max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-Frame-Options: SAMEORIGIN` (niet op `auth.getklai.com` — nodig voor OIDC iframe)
- `admin off` in Caddy (geen beheer-API bereikbaar)

### At-rest

- SSO cookie: Fernet (AES-128-CBC) — **niet** AES-256-GCM zoals eerder geclaimd
- Connector secrets: AES-256-GCM ([security.py](../../../portal/backend/app/core/security.py)), 12-byte random nonce per encryptie
- Secrets in git: SOPS + age (X25519), dual recipients (MacBook + server)
- PostgreSQL, MongoDB, Redis: **geen column-level of filesystem-level encryptie geconfigureerd**

---

## Audit logging

### Wat er is (bevestigd in code)

- Tabel `portal_audit_log` aangemaakt via Alembic-migratie ([v2w3x4y5z6a7](../../../portal/backend/alembic/versions/v2w3x4y5z6a7_add_audit_log.py))
- Schema: `id`, `org_id`, `actor_user_id`, `action`, `resource_type`, `resource_id`, `details` (JSONB), `created_at`
- `log_event()` ([audit.py](../../../portal/backend/app/services/audit.py)) is de enige schrijfweg; faalt non-fataal
- Aangeroepen bij: gebruikersuitnodiging, gebruikersverwijdering, schorsing, offboarding, groepswijzigingen, vergaderwijzigingen

### Status na SPEC-SEC-001 (2026-03-27)

- ~~**Login events worden NIET geaudit.**~~ **✅ Opgelost (Fix 1)** — `auth.py` roept nu `log_event()` aan bij: succesvolle password login (`auth.login`), succesvolle TOTP (`auth.login.totp`), logout (`auth.logout`), mislukte password auth (`auth.login.failed`, details `{reason: invalid_credentials}`), mislukte TOTP (`auth.totp.failed`, details `{reason: invalid_code}`). `org_id=0` als sentinel bij onbekend e-mailadres.
- ~~**"Append-only" is alleen een conventie.**~~ **✅ Opgelost (Fix 4)** — PostgreSQL RULES toegevoegd via Alembic-migratie: `no_update_audit` en `no_delete_audit` op `portal_audit_log`. UPDATE/DELETE worden silently genegeerd zonder fout.
- ~~**Audit log heeft geen RLS.**~~ **✅ Opgelost (Fix 5)** — RLS ingeschakeld op `portal_audit_log` via dezelfde migration: `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy op `org_id`.
- **Logretentie: 30 dagen** (VictoriaLogs). NEN 7510 vereist voor zorginstellingen typisch 6–12 maanden. _(Nog openstaand — buiten scope SPEC-SEC-001)_

---

## Tenant isolatie

### Wat er is (bevestigd in migratie)

PostgreSQL Row Level Security op **5 tabellen** ([c5d6e7f8a9b0](../../../portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py)):
- `portal_groups`
- `portal_knowledge_bases`
- `portal_group_products`
- `portal_group_memberships`
- `portal_group_kb_access`

RLS-mechanisme: `set_tenant()` ([database.py](../../../portal/backend/app/core/database.py)) zet `app.current_org_id` als PostgreSQL session-variabele. RLS-policies lezen die waarde via `NULLIF(current_setting('app.current_org_id', true), '')::int`.

### Kritieke nuancering

**Geen RLS op:** `portal_orgs`, `portal_users`, `portal_audit_log`, `portal_connectors`, en alle overige tabellen. De code in `internal.py` stelt expliciet: *"portal_connectors has no RLS"*. Buiten de 5 bovenstaande tabellen berust isolatie volledig op correcte `org_id`-filters in applicatielogica.

**MongoDB**: één database per tenant (`mongodb://mongo/{tenant_id}`) — isolatie op database-niveau, goed.

**Meilisearch**: gedeeld tussen tenants met dezelfde `MEILI_MASTER_KEY`. Zoekindexen zijn niet per tenant geïsoleerd.

---

## Structlog context: org_id / user_id

### ✅ Opgelost (SPEC-SEC-001 Fix 3, 2026-03-27)

~~De `LoggingContextMiddleware` leest `request.state.org_id` en `request.state.user_id` **vóór** `call_next()`, dus vóór dat FastAPI route-dependencies draaien. In de praktijk zijn `org_id` en `user_id` altijd `None`.~~

De [LoggingContextMiddleware](../../../portal/backend/app/middleware/logging_context.py) bindt nu `org_id` en `user_id` **na** `call_next()`, zodat route-dependencies de kans hebben gehad `request.state` te vullen. `request_id` wordt nog steeds **vóór** `call_next()` gebonden (onveranderd). Unauthenticated routes krijgen `None` (geen exception).

---

## Netwerk & infrastructuur

### Bevestigd in docker-compose.yml en Caddyfile

- Docker networks: 8 geïsoleerde netwerken (`klai-net`, `net-mongodb`, `net-postgres`, `net-redis`, `net-meilisearch`, `inference`, `monitoring`, `socket-proxy`, `vexa-bots`)
- `inference` en `monitoring` zijn `internal: true` (geen internet-egress)
- UFW: alleen poorten 22, 80, 443 inbound
- `docker-socket-proxy`: minimale API-surface (CONTAINERS, NETWORKS, POST, DELETE)
- LiteLLM: `DISABLE_ADMIN_UI: "True"` en Grafana: `GF_AUTH_ANONYMOUS_ENABLED: "false"`
- Rate limiting in Caddy: per route, per IP — 10/min voor signup/billing, 60/min voor API, 120/min voor chat
- LibreChat: `ALLOW_EMAIL_LOGIN: false`, `ALLOW_REGISTRATION: false`

### Gevonden beveiligingsproblemen (niet in eerdere analyse)

~~**FalkorDB bindt op `0.0.0.0:6380`**~~ **✅ Opgelost (P0, vóór SPEC-SEC-001)** — Port mapping gewijzigd naar `127.0.0.1:6380:6379` in docker-compose.yml. FalkorDB niet langer extern bereikbaar.

**Hardcoded Firecrawl wachtwoord:**
```yaml
firecrawl-postgres:
  POSTGRES_PASSWORD: firecrawl_pass
```
Laag risico (intern netwerk), maar hygiëne-probleem.

**Zitadel-naar-PostgreSQL zonder TLS:**
`ZITADEL_DATABASE_POSTGRES_USER_SSL_MODE: disable` — acceptabel binnen sealed Docker networks, maar waard te noteren.

---

## Backups

### ✅ Opgelost (SPEC-SEC-001 Fix 6, 2026-03-27)

[backup.sh](../../../deploy/scripts/backup.sh) is uitgebreid en volledig geautomatiseerd:

**Wat er nu in zit:**
- `pg_dumpall` (PostgreSQL, incl. Gitea DB-schema)
- Gitea git-repositories + config (primaire KB-bron, via `docker run --volumes-from :ro alpine tar`)
- `mongodump` archive (MongoDB — LibreChat chatgeschiedenis)
- `redis-cli BGSAVE` + dump.rdb (Redis — sessiedata)
- Meilisearch snapshot (nice-to-have, derived — herindexeerbaar uit MongoDB)

**Automatisering:**
- Dagelijks om 02:00 via cron op core-01 (`0 2 * * * /opt/klai/scripts/backup.sh`)
- Lokale retentie: 30 dagen

**Off-site opslag:**
- Encrypted met `age` (dual-recipient: MacBook + core-01, zelfde sleutels als SOPS)
- Upload naar Hetzner Storage Box via `rsync -e ssh -p 23` (ipv rclone — equivalent)
- Geen remote pruning nodig (45 MB/dag, 100 GB capaciteit ≈ 2226 dagen)

**Monitoring:**
- Uptime Kuma push monitor "Backup core-01" (ID 48) op status.getklai.com
- `trap 'ERR'` → `_kuma_push down` bij enige fout
- Succesmelding met totale backup-grootte aan het einde

**FalkorDB en Qdrant zijn bewust uitgesloten** — derived indices, herindexeerbaar via ingest pipeline.

**Nog openstaand:** geteste restore-procedure (RTO/RPO documentatie) — buiten scope SPEC-SEC-001.

---

## Gecorrigeerde claim-tabel

| Claim (uit eerste analyse) | Status | Werkelijkheid |
|---|---|---|
| MFA niet geïmplementeerd | ❌ Incorrect | TOTP, passkeys, email OTP zijn volledig uitgewerkt |
| SSO cookie AES-256-GCM | ❌ Incorrect | Fernet (AES-128-CBC-HMAC-SHA256) |
| Login events geaudit | ~~❌ Incorrect~~ **✅ Opgelost** | ~~`emit_event()`, geen `log_event()`~~ `log_event()` aangeroepen voor alle auth-events (SPEC-SEC-001 Fix 1) |
| Audit log append-only (DB-niveau) | ~~❌ Incorrect~~ **✅ Opgelost** | ~~Alleen conventie~~ PostgreSQL RULES `no_update_audit` + `no_delete_audit` (SPEC-SEC-001 Fix 4) |
| org_id/user_id in logs per request | ~~❌ Incorrect~~ **✅ Opgelost** | ~~Altijd None~~ Gebonden na `call_next()` — gevuld voor authenticated routes (SPEC-SEC-001 Fix 3) |
| RLS op alle tenant data | ⚠ Overstated | 5 tabellen + `portal_audit_log` (Fix 5); `portal_connectors` en overige nog applicatielogica |
| Geen geautomatiseerde backups | ~~✓ Correct~~ **✅ Opgelost** | ~~Handmatig~~ Dagelijks cron + Hetzner Storage Box + Uptime Kuma (SPEC-SEC-001 Fix 6) |
| Geen rate limiting | ❌ Incorrect | Caddy heeft uitgebreide per-route rate limiting |
| FalkorDB veilig | ~~❌ Gemist~~ **✅ Opgelost** | ~~0.0.0.0:6380~~ `127.0.0.1:6380:6379` (P0, vóór SPEC) |
| MFA "required" afgedwongen | ~~❌ Incorrect~~ **✅ Opgelost** | ~~Backend enforces niets~~ HTTP 403 bij geen MFA + `mfa_policy="required"` (SPEC-SEC-001 Fix 2) |

---

## Prioritaire acties voor NEN 7510

### Direct (voor productie met zorginstellingen)

1. ~~**FalkorDB port**~~ **✅ Opgelost (P0, vóór SPEC-SEC-001)** — `127.0.0.1:6380:6379` in docker-compose.yml
2. ~~**LoggingContextMiddleware**~~ **✅ Opgelost (SPEC-SEC-001 Fix 3)** — `org_id`/`user_id` gebonden na `call_next()`
3. ~~**Login events in audit log**~~ **✅ Opgelost (SPEC-SEC-001 Fix 1)** — alle auth-events geaudit in `portal_audit_log`
4. ~~**Backup automatisering**~~ **✅ Opgelost (SPEC-SEC-001 Fix 6)** — dagelijks cron + Hetzner Storage Box + Uptime Kuma

### Kortetermijn

5. ~~**MFA backend enforcement**~~ **✅ Opgelost (SPEC-SEC-001 Fix 2)** — HTTP 403 bij `mfa_policy="required"` + geen MFA
6. **Log retentie verhogen** — VictoriaLogs van 30d naar 90d minimum; overweeg archivering naar cold storage _(open)_
7. ~~**Audit log append-only**~~ **✅ Opgelost (SPEC-SEC-001 Fix 4)** — PostgreSQL RULES blokkeren UPDATE/DELETE
8. ~~**Audit log RLS**~~ **✅ Opgelost (SPEC-SEC-001 Fix 5)** — tenant isolation policy op `portal_audit_log`

### Middellange termijn

9. **Database encryption at rest** — PostgreSQL volume-level encryption of column-level voor PII _(open)_
10. **Formeel incident response playbook** — gedocumenteerde procedure voor beveiligingsincidenten _(open)_
11. **Geteste disaster recovery** — backup restore testen, RTO/RPO definiëren _(open)_
12. **GDPR-inzage en -verwijdering API** — cascading delete over Zitadel + Portal _(open)_

---

## Zie ook

- [patterns/platform.md](../patterns/platform.md) — LiteLLM, Zitadel, Caddy configuratie
- [pitfalls/infrastructure.md](../pitfalls/infrastructure.md) — operationele valkuilen
- [pitfalls/process.md](../pitfalls/process.md) — AI-ontwikkelworkflow regels
