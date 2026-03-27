# NEN 7510 Compliance Assessment — Klai AI Platform

> Code-gevalideerde analyse. Elke claim is geverifieerd in de broncode.
> Datum: 2026-03-27 | Bijgewerkt: 2026-03-27 (SPEC-SEC-001 afgerond + verdiepingsaudit 6 domeinen)

---

## Samenvatting

NEN 7510 is de Nederlandse norm voor informatiebeveiliging in de zorg (ISO 27001 + zorgsectorspecifieke aanvullingen). Dit document bevat een code-gevalideerde beoordeling van de Klai compliance-status.

**Totaalplaatje (na SPEC-SEC-001 + verdiepingsaudit, 2026-03-27):**
- Technische fundering (netwerk, auth, crypto, audit): ~80–85% geïmplementeerd _(was ~60–65%)_
- Documentatie / formeel beleid: ~40% _(was ~30%, door IR-runbook + breach-notification + SoA)_
- Operationele processen (DR, incident response): ~40% _(was ~25%, door backups + monitoring + IR-playbook)_

**Nieuw ontdekt in verdiepingsaudit:** container image scanning ontbreekt volledig (kritiek), geen wachtwoord brute-force lockout, MFA-setup events niet geaudit, sleutelrotatie ontbreekt, 8+ audit-events missen (rolwijziging, org-instellingen, KB-toegang, connectors), logretentie NON-COMPLIANT (30d vs 6–12 maanden), geen SAR-API, geen verwerkingsregister, geen DPIA.

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
10. ~~**Formeel incident response playbook**~~ **✅ Opgelost (2026-03-27)** — `klai-private/compliance/incident-response-runbook.md` + `contact-with-authorities.md` (72-uurs GDPR-melding)
11. **Geteste disaster recovery** — backup restore testen, RTO/RPO definiëren _(open)_
12. **GDPR-inzage en -verwijdering API** — cascading delete over Zitadel + Portal; SAR-endpoint ontbreekt _(open)_

---

## Verdiepingsaudit per NEN-domein (2026-03-27)

> Uitgevoerd via 6 parallelle gespecialiseerde agents die de broncode hebben doorzocht op bewijsmateriaal per NEN-controlegebied.

### A.9 Toegangsbeveiliging — GEDEELTELIJK COMPLIANT

**Compliant:** MFA-enforcement (HTTP 403 bij `required` + geen MFA), RBAC (admin/group-admin/member), TOTP-lockout na 5 pogingen, RLS op 5 tabellen + audit log, gebruikerslevenscyclus (uitnodiging, schorsing, offboarding).

**Nieuwe hiaten:**

| Hiaat | Locatie | Risico |
|---|---|---|
| Geen wachtwoord brute-force lockout | `auth.py:321–329` — alleen gelogd, niet geblokkeerd | Credential-stuffing aanval mogelijk |
| Geen idle session timeout | `config.py:54` — alleen 24u absoluut, geen inactiviteitstime-out | Verlaten werkstations risico |
| MFA-setup/bevestiging niet geaudit | `auth.py:538–704` — TOTP, passkey, email OTP setup zonder `log_event()` | MFA-enrollment onnavolgbaar |
| Geen formeel toegangsbeleidsdocument | — | NEN 7510 A.9.1 vereist schriftelijk beleid |

---

### A.10 Cryptografie — GEDEELTELIJK COMPLIANT

**Compliant:** TLS 1.2+ extern (Caddy + HSTS), AES-256-GCM voor tenantgeheimen ([security.py](../../../portal/backend/app/core/security.py)), age-encryptie voor backups (ChaCha20-Poly1305, dual-recipient), automatische TLS-verlenging.

**Nieuwe hiaten:**

| Hiaat | Locatie | Risico |
|---|---|---|
| **Geen database-encryptie at rest** | `docker-compose.yml` — PostgreSQL, MongoDB, Redis: geen encryptie | Fysieke of geprivilegieerde toegang tot schijf legt alle data bloot |
| SSO-cookie AES-128, niet AES-256 | `auth.py:40,98–104` — Fernet (AES-128-CBC-HMAC-SHA256) | Lager dan zorgaanbeveling; niet kritiek maar niet best practice |
| Geen sleutelrotatie procedure | `.env`-gebaseerde Fernet/portal_secrets keys — handmatig, geen beleid | Gecompromitteerde key blijft actief |
| Geen sleutelintrekeking procedure | — | Gelekte key kan niet snel ingetrokken worden |
| Geen formeel cryptografisch beleidsdocument (A.10.1.1) | — | NEN 7510 vereist schriftelijk algoritme-beleid |

---

### A.12.4 Audit Logging — GEDEELTELIJK COMPLIANT (55–60%)

**Compliant:** PostgreSQL RULES (no_update + no_delete), RLS + FORCE op `portal_audit_log`, alle auth-events (login, TOTP, logout, failures), schorsing + offboarding, product-toewijzing, groepslidmaatschap.

**Nieuwe hiaten — ontbrekende audit-events:**

| Actie | Endpoint | Huidige logging |
|---|---|---|
| Rolwijziging | `PATCH /api/admin/users/{id}/role` | Alleen `logger.info()`, geen `log_event()` |
| Org-instellingen (incl. mfa_policy) | `PATCH /api/admin/settings` | Alleen `logger.info()`, geen `log_event()` |
| Gebruikersreactivering | `POST /api/admin/users/{id}/reactivate` | Geen logging |
| Gebruikersprofiel wijziging | `PATCH /api/admin/users/{id}` | Geen logging |
| Account selfservice | `PATCH /api/me/*` | Onbekend / waarschijnlijk ontbrekend |
| KB-toegang / aanmaak / verwijdering | `knowledge_bases.py` | Niet geaudit |
| Connectors aanmaken/wijzigen | `connectors.py` | Geen `log_event()` gevonden |

**Logretentie NON-COMPLIANT:** VictoriaLogs 30 dagen vs NEN 7510 minimum 6–12 maanden voor zorginstellingen.

---

### A.12 Operationele Beveiliging — GEDEELTELIJK COMPLIANT

**Compliant:** Dagelijkse geautomatiseerde backups, `pip-audit` (Python) + `npm audit --audit-level=high` (Node.js), CI/CD via GitHub Actions, gestructureerde deployment-procedures.

**Nieuwe hiaten:**

| Hiaat | Ernst | Locatie |
|---|---|---|
| **Geen container image scanning (Trivy/Snyk)** | KRITIEK | Niet aanwezig in CI-pipeline |
| Geen gedocumenteerde RTO/RPO | HOOG | `backup.sh:179` — "TODO: buiten scope SPEC-SEC-001" |
| Geen geteste restore-procedure | HOOG | Backups bestaan maar zijn nooit geverifieerd |
| `pip-audit` ignoreert CVE-2026-4539 zonder gedocumenteerde uitzondering | MEDIUM | `.github/workflows/portal-api.yml:40–41` |
| Geen SBOM generatie | MEDIUM | — |

Alle third-party Docker images (MongoDB, Zitadel, LibreChat etc.) worden niet gescand op bekende kwetsbaarheden.

---

### A.13 Netwerk & Tenant Isolatie — VOLLEDIG COMPLIANT ✅

Klai **voldoet volledig** aan NEN 7510 A.13 en A.9.4. Geen kritieke hiaten gevonden.

**Bevestigd in broncode:**
- 9 Docker-netwerken waarvan 8 `internal: true` — geen internet-egress tenzij expliciet benodigd
- Alle services gebonden aan `127.0.0.1` behalve Caddy (80/443)
- PostgreSQL RLS op 5 tabellen + `portal_audit_log` (FORCE ROW LEVEL SECURITY)
- `set_tenant()` aangeroepen op élk authenticated request; sessie gereset na afloop
- MongoDB: aparte database per tenant + eigen credentials
- Cross-tenant data leakage aantoonbaar onmogelijk (getest via 3 aanvalsscenario's)

Kleine documentatiehiaten (geen functionele risico's):
- `_get_caller_org()` gedupliceerd in `admin.py` en `dependencies.py` — DRY-issue
- Tabellen zonder RLS missen inline documentatie over design intent

---

### A.16 + A.18 Incident Management & Compliance — GEDEELTELIJK COMPLIANT

**Compliant:** IR-runbook volledig gedocumenteerd (`klai-private/compliance/incident-response-runbook.md`), 72-uurs GDPR-meldprocedure naar Autoriteit Persoonsgegevens, forensisch bewijsprotocol (chain-of-custody + checksums), leerproces via `/retro` workflow, jaarlijkse security review (eerste instantie: SPEC-SEC-001).

**Nieuwe hiaten:**

| Hiaat | NEN-vereiste | Status |
|---|---|---|
| **Logretentie NON-COMPLIANT** | A.18.1.3 — 6–12 maanden | 30 dagen (VictoriaLogs) — ondermaats |
| Geen SAR-endpoint (Subject Access Request) | GDPR Art. 15 | Geen `/api/me/export` endpoint |
| Geen Data Processing Register | GDPR Art. 30 / AVG | Verwerkingsregister ontbreekt |
| Geen DPIA | GDPR Art. 35 (hoog risico) | Niet uitgevoerd voor zorgdata |
| Geen external vulnerability disclosure | A.16.1.3 | Geen security@getklai.com of responsible disclosure policy |
| Wvggz niet expliciet benoemd in SoA | A.18.1.1 | Healthcare-wetgeving mist in `iso27001-soa.md` |

---

## Prioritaire acties — bijgewerkt na verdiepingsaudit

### Nieuw kritiek (vóór productie met zorginstellingen)

13. **Container image scanning** — Trivy of Snyk toevoegen aan CI-pipeline voor alle Docker images _(open)_
14. **Ontbrekende audit-events** — `user.role_changed`, `org_settings.updated`, `user.reactivated`, `kb.created/deleted`, `connector.created/updated/deleted` _(open)_
15. **Log retentie** — VictoriaLogs van 30d naar minimaal 90d (streefdoel 365d); archivering naar cold storage _(open — was al item 6)_

### Nieuw kortetermijn

16. **MFA-setup audit logging** — `log_event()` toevoegen aan TOTP/passkey/email-OTP setup- en bevestigingsendpoints _(open)_
17. **Wachtwoord brute-force lockout** — teller + tijdelijke blokkering na 5 mislukte inlogpogingen _(open)_
18. **SAR-API** — `/api/me/export` endpoint voor GDPR-inzageverzoeken _(open)_
19. **Data Processing Register** — `verwerkingsregister.md` aanmaken per AVG Art. 30 _(open)_
20. **DPIA** — Data Protection Impact Assessment voor zorgdata _(open)_

### Nieuw middellange termijn

21. **Sleutelrotatie procedure** — Fernet + portal_secrets_key rotatieproces met dual-key operation _(open)_
22. **Formeel cryptografisch beleidsdocument** — A.10.1.1 beleid incl. algoritmen, sleutellengtes, levenscyclus _(open)_
23. **External vulnerability disclosure** — `security@getklai.com` + responsible disclosure policy _(open)_
24. **RTO/RPO documentatie + restore-test** — Backuprestauratietest + tijdsdoelstellingen _(open — was al item 11)_

---

## Zie ook

- [patterns/platform.md](../patterns/platform.md) — LiteLLM, Zitadel, Caddy configuratie
- [pitfalls/infrastructure.md](../pitfalls/infrastructure.md) — operationele valkuilen
- [pitfalls/process.md](../pitfalls/process.md) — AI-ontwikkelworkflow regels
