# NEN 7510 Compliance Assessment — Klai AI Platform

> Code-gevalideerde analyse. Elke claim is geverifieerd in de broncode.
> Datum: 2026-03-27

---

## Samenvatting

NEN 7510 is de Nederlandse norm voor informatiebeveiliging in de zorg (ISO 27001 + zorgsectorspecifieke aanvullingen). Dit document bevat een code-gevalideerde beoordeling van de Klai compliance-status.

**Totaalplaatje:**
- Technische fundering (netwerk, auth, crypto): ~60–65% geïmplementeerd
- Documentatie / formeel beleid: ~30%
- Operationele processen (DR, incident response): ~25%

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

**`mfa_policy = "required"` wordt NIET backend-afgedwongen bij login.** De login-endpoint controleert `has_totp` (heeft de gebruiker TOTP ingesteld?), maar als een gebruiker `mfa_policy="required"` heeft en geen TOTP heeft ingesteld, logt hij alsnog in. Enforcement berust volledig op de frontend UI. Dit is geen NEN 7510-conforme afdwinging van MFA.

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

### Kritieke nuancering

- **Login events worden NIET geaudit.** `auth.py` roept `emit_event("login", ...)` aan (product analytics), geen `log_event()`. Er is geen audit trail van inlogpogingen, uitloggen, of mislukte auth.
- **"Append-only" is alleen een conventie.** Geen PostgreSQL-triggers of DDL-constraints die DELETE/UPDATE blokkeren. De code doet het niet, maar de database staat het toe.
- **Audit log heeft geen RLS.** Een applicatiefout kan in principe org A's logs blootstellen aan org B.
- **Logretentie: 30 dagen** (VictoriaLogs). NEN 7510 vereist voor zorginstellingen typisch 6–12 maanden.

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

### Bevinding (kritiek)

De [LoggingContextMiddleware](../../../portal/backend/app/middleware/logging_context.py) leest `request.state.org_id` en `request.state.user_id` **vóór** `call_next()`, dus vóór dat FastAPI route-dependencies draaien. Er is geen auth-middleware in [main.py](../../../portal/backend/app/main.py) die `request.state.org_id` zet.

**In de praktijk zijn `org_id` en `user_id` in de structlog context altijd `None`.** Alleen `request_id` wordt betrouwbaar gebonden. De opmerking in de middleware (*"Auth middleware runs before this"*) is feitelijk onjuist — er is geen auth middleware.

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

**FalkorDB bindt op `0.0.0.0:6380`:**
```yaml
falkordb:
  ports:
    - "6380:6379"   # ← niet 127.0.0.1:6380:6379
```
Docker bypasses UFW via iptables. FalkorDB (Redis-compatibel, geen auth ingesteld) is waarschijnlijk bereikbaar van buiten. Fix: verander naar `127.0.0.1:6380:6379` of verwijder de port mapping als je het alleen intern gebruikt.

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

### Bevinding

[backup.sh](../../../deploy/scripts/backup.sh) bestaat en doet:
- `pg_dumpall` (PostgreSQL)
- `mongodump` (MongoDB)
- `redis-cli BGSAVE` + `rdb` kopiëren
- Meilisearch snapshot via API

**Maar:** het script is gedocumenteerd als *"Pre-upgrade backup"* met *"Usage: ./scripts/backup.sh"*. Er is geen cronjob, geen automatische scheduling, en geen off-site opslag. Het is een handmatig hulpmiddel.

**NEN 7510 vereist:** geautomatiseerde, regelmatige backups; off-site opslag; geteste restore-procedure; gedocumenteerde RTO/RPO.

---

## Gecorrigeerde claim-tabel

| Claim (uit eerste analyse) | Status | Werkelijkheid |
|---|---|---|
| MFA niet geïmplementeerd | ❌ Incorrect | TOTP, passkeys, email OTP zijn volledig uitgewerkt |
| SSO cookie AES-256-GCM | ❌ Incorrect | Fernet (AES-128-CBC-HMAC-SHA256) |
| Login events geaudit | ❌ Incorrect | `emit_event()`, geen `log_event()` — niet in audit log |
| Audit log append-only (DB-niveau) | ❌ Incorrect | Alleen conventie, geen DB-constraint |
| org_id/user_id in logs per request | ❌ Incorrect | Middleware leest ze vóór auth — altijd None |
| RLS op alle tenant data | ⚠ Overstated | Alleen 5 tabellen; rest applicatielogica |
| Geen geautomatiseerde backups | ✓ Correct | Script bestaat maar is handmatig |
| Geen rate limiting | ❌ Incorrect | Caddy heeft uitgebreide per-route rate limiting |
| FalkorDB veilig | ❌ Gemist | 0.0.0.0:6380 waarschijnlijk extern bereikbaar |
| MFA "required" afgedwongen | ❌ Incorrect | Backend enforces niets bij login |

---

## Prioritaire acties voor NEN 7510

### Direct (voor productie met zorginstellingen)

1. **FalkorDB port** — verander `"6380:6379"` naar `"127.0.0.1:6380:6379"` in docker-compose.yml
2. **LoggingContextMiddleware** — bind `org_id`/`user_id` na `call_next()`, of bind handmatig in route handlers
3. **Login events in audit log** — `log_event()` aanroepen in `auth.py` bij login, logout, en mislukte auth
4. **Backup automatisering** — backup.sh dagelijks via cron, output naar off-site locatie (bijv. Hetzner Object Storage)

### Kortetermijn

5. **MFA backend enforcement** — bij `mfa_policy="required"`: weiger login als geen MFA-methode ingesteld
6. **Log retentie verhogen** — VictoriaLogs van 30d naar 90d minimum; overweeg archivering naar cold storage
7. **Audit log append-only** — voeg PostgreSQL trigger toe die UPDATE/DELETE blokkeert op `portal_audit_log`
8. **Audit log RLS** — voeg RLS toe op `portal_audit_log`

### Middellange termijn

9. **Database encryption at rest** — PostgreSQL volume-level encryption of column-level voor PII
10. **Formeel incident response playbook** — gedocumenteerde procedure voor beveiligingsincidenten
11. **Geteste disaster recovery** — backup restore testen, RTO/RPO definiëren
12. **GDPR-inzage en -verwijdering API** — cascading delete over Zitadel + Portal

---

## Zie ook

- [patterns/platform.md](../patterns/platform.md) — LiteLLM, Zitadel, Caddy configuratie
- [pitfalls/infrastructure.md](../pitfalls/infrastructure.md) — operationele valkuilen
- [pitfalls/process.md](../pitfalls/process.md) — AI-ontwikkelworkflow regels
